"""Deterministic audit records and human overrides for reconciliation decisions.

The audit log is deliberately append-only.  Overrides are separate records and
are applied only when an effective view is requested, leaving every automated
decision available for inspection.

TWO HASHES, TWO JOBS

    ``decision_id`` is an IDENTITY. It hashes ``stage``, ``subject`` and
    ``action`` only, which is exactly what makes it stable and useful: the same
    stage reaching the same verdict about the same subject gets the same id
    across runs, so it can be referenced by an override, deduplicated on append,
    and cited in a report. Its narrowness is the feature.

    ``record_hash`` is a SEAL. It covers the COMPLETE record -- inputs, rule,
    result, confidence, reasoning, timestamp, overridable flag, every Decimal
    path -- chained to the record before it:

        record_hash = SHA256(previous_hash || canonical_json(complete record))

    with the first record chained to ``GENESIS_HASH``.

    Keeping the two separate is deliberate, and conflating them would break
    both. An identity that changed whenever the reasoning text changed could not
    be referenced. A seal that covered only stage, subject and action would let
    anyone rewrite the amount, the rule that fired, or the confidence, and leave
    the log validating perfectly -- which is precisely the tampering an audit
    trail exists to detect.

    Because each seal includes its predecessor, the chain also detects edits
    that leave individual records intact: deleting a record, inserting one, or
    reordering two all break every link downstream. The final ``head_hash`` is a
    single value that attests to the whole log, so it can be published,
    timestamped, or countersigned without shipping the log itself.

    What this does NOT provide is protection against an attacker who can rewrite
    the entire file, since they can simply recompute the chain. Defeating that
    needs the head hash held somewhere the writer cannot reach -- an append-only
    store, a signature, or a third party. The chain makes tampering DETECTABLE
    given a trusted head, not IMPOSSIBLE, and that distinction is stated here
    rather than left for a reviewer to point out.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias


STAGES = frozenset(f"S{number}" for number in range(7))
ACTIONS = frozenset({"match", "exception", "abstain", "no_action"})

Subject: TypeAlias = str | Sequence[str] | AbstractSet[str]
Timestamp: TypeAlias = datetime | str


class AuditError(Exception):
    """Base class for audit and override errors."""


class InvalidDecisionError(AuditError, ValueError):
    """Raised when a decision cannot form a valid audit record."""


class DuplicateDecisionError(AuditError, ValueError):
    """Raised when an existing decision ID would be replaced or duplicated."""


class AuditIntegrityError(AuditError, ValueError):
    """Raised when serialized audit data is malformed or has been altered."""


class AuditChainError(AuditIntegrityError):
    """A record does not reproduce the hash that claims to seal it.

    Distinct from a plain integrity error because the remedy differs: a
    malformed record is a bug in whatever wrote it, a broken chain link is
    evidence that the file changed after it was written.
    """


class NonOverridableDecisionError(AuditError, ValueError):
    """Raised when a human tries to override a protected decision."""


class UnknownDecisionError(AuditError, KeyError):
    """Raised when an override refers to a decision that is not in the log."""


class DuplicateOverrideError(AuditError, ValueError):
    """Raised when more than one override targets the same decision."""


def _decimal_string(value: Decimal) -> str:
    """Return an exact two-decimal representation, rejecting lossy values."""

    if not value.is_finite():
        raise InvalidDecisionError("Decimal values in decisions must be finite")

    fixed = format(value, "f")
    fractional = fixed.partition(".")[2].rstrip("0")
    if len(fractional) > 2:
        raise InvalidDecisionError(
            f"Decimal value {value!r} cannot be represented exactly at 2dp"
        )
    return format(value, ".2f")


def _normalise_subject(subject: Subject) -> str | tuple[str, ...]:
    if isinstance(subject, str):
        if not subject:
            raise InvalidDecisionError("subject must not be empty")
        return subject
    if isinstance(subject, (Sequence, AbstractSet)):
        if isinstance(subject, (bytes, bytearray)):
            raise InvalidDecisionError("subject must be a string or a collection of strings")
        values = tuple(subject)
        if not values or not all(isinstance(value, str) and value for value in values):
            raise InvalidDecisionError(
                "a multi-item subject must contain one or more non-empty strings"
            )
        # A multi-item subject denotes a set, so order and duplicate inputs do
        # not affect its identity.
        return tuple(sorted(set(values)))
    raise InvalidDecisionError("subject must be a string or a collection of strings")


def _freeze_value(value: object) -> object:
    """Copy JSON-like data into recursively immutable containers."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidDecisionError("float values in decisions must be finite")
        return value
    if isinstance(value, Decimal):
        # Canonicalise the exponent as well as the value so the in-memory
        # record and its 2dp serialized form are structurally identical.
        return Decimal(_decimal_string(value))
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidDecisionError("decision mapping keys must be strings")
            frozen[key] = _freeze_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen_items = [_freeze_value(item) for item in value]
        return tuple(sorted(frozen_items, key=_canonical_json))
    raise InvalidDecisionError(
        f"unsupported value in decision payload: {type(value).__name__}"
    )


def _encode_value(
    value: object,
    path: tuple[str | int, ...],
    decimal_paths: list[tuple[str | int, ...]],
) -> object:
    if isinstance(value, Decimal):
        decimal_paths.append(path)
        return _decimal_string(value)
    if isinstance(value, Mapping):
        return {
            key: _encode_value(item, path + (key,), decimal_paths)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [
            _encode_value(item, path + (index,), decimal_paths)
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise InvalidDecisionError(
        f"unsupported value in decision payload: {type(value).__name__}"
    )


def _canonical_json(value: object) -> str:
    decimal_paths: list[tuple[str | int, ...]] = []
    encoded = _encode_value(value, (), decimal_paths)
    envelope = {
        "decimal_paths": [list(path) for path in sorted(decimal_paths, key=repr)],
        "value": encoded,
    }
    return json.dumps(
        envelope,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decision_id(stage: str, subject: object, action: str) -> str:
    identity = {"action": action, "stage": stage, "subject": subject}
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"dec_{digest[:24]}"


GENESIS_HASH = "0" * 64

# Fields that carry the chain itself. They are excluded from the sealed payload
# -- a hash cannot cover its own value -- and are not part of a Decision.
_CHAIN_FIELDS = frozenset({"previous_hash", "record_hash"})


def _dumps(record: Mapping[str, object]) -> str:
    """The one serialisation used for both writing and sealing.

    Writing and hashing must agree byte for byte or the chain would fail to
    verify a file it had just written, so they share this function rather than
    each spelling out the same four keyword arguments.
    """

    return json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _record_hash(previous_hash: str, record: Mapping[str, object]) -> str:
    """Seal a complete record against its predecessor."""

    sealed = {
        key: value for key, value in record.items() if key not in _CHAIN_FIELDS
    }
    return hashlib.sha256(
        (previous_hash + _dumps(sealed)).encode("utf-8")
    ).hexdigest()


def _verify_chain_link(
    record: Mapping[str, object], previous_hash: str, line_number: int
) -> str:
    """Check one link and return the hash the next record must chain to."""

    for name in ("previous_hash", "record_hash"):
        value = record.get(name)
        if not isinstance(value, str) or not value:
            raise AuditChainError(
                f"line {line_number} carries no usable {name}; the file predates "
                "the hash chain or the field was stripped"
            )

    if record["previous_hash"] != previous_hash:
        raise AuditChainError(
            f"line {line_number} does not follow the record before it: it chains "
            f"to {record['previous_hash']!r} but the previous record sealed to "
            f"{previous_hash!r}. A record was inserted, removed, or reordered"
        )

    expected = _record_hash(previous_hash, record)
    if record["record_hash"] != expected:
        raise AuditChainError(
            f"line {line_number} has been altered since it was written: its "
            f"contents seal to {expected!r}, not to the {record['record_hash']!r} "
            "it claims"
        )
    return expected


def _validate_timestamp(timestamp: Timestamp) -> None:
    if not isinstance(timestamp, (datetime, str)):
        raise InvalidDecisionError("timestamp must be an injected datetime or string")
    if isinstance(timestamp, str) and not timestamp:
        raise InvalidDecisionError("timestamp must not be empty")


@dataclass(frozen=True, slots=True)
class Decision:
    """One immutable, deterministic pipeline decision."""

    stage: str
    subject: Subject
    action: str
    inputs: Mapping[str, object]
    rule: str
    result: object
    confidence: float | None
    reasoning: str
    timestamp: Timestamp
    overridable: bool
    decision_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise InvalidDecisionError("stage must be one of S0 through S6")
        if self.action not in ACTIONS:
            raise InvalidDecisionError(
                f"action must be one of {', '.join(sorted(ACTIONS))}"
            )
        if not isinstance(self.inputs, Mapping):
            raise InvalidDecisionError("inputs must be a mapping")
        if not isinstance(self.rule, str) or not self.rule:
            raise InvalidDecisionError("rule must be a non-empty string")
        if not isinstance(self.reasoning, str) or not self.reasoning:
            raise InvalidDecisionError("reasoning must be a non-empty string")
        if type(self.overridable) is not bool:
            raise InvalidDecisionError("overridable must be a bool")
        if self.confidence is not None:
            if type(self.confidence) is not float:
                raise InvalidDecisionError("confidence must be a float or None")
            if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise InvalidDecisionError("confidence must be between 0 and 1")
        _validate_timestamp(self.timestamp)

        subject = _normalise_subject(self.subject)
        inputs = _freeze_value(self.inputs)
        result = _freeze_value(self.result)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "result", result)
        object.__setattr__(
            self,
            "decision_id",
            _decision_id(self.stage, subject, self.action),
        )


def _decision_to_record(decision: Decision) -> dict[str, object]:
    decimal_paths: list[tuple[str | int, ...]] = []
    timestamp = decision.timestamp
    timestamp_type = "datetime" if isinstance(timestamp, datetime) else "str"
    timestamp_value = timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp

    record: dict[str, object] = {
        "decision_id": decision.decision_id,
        "stage": decision.stage,
        "subject": _encode_value(decision.subject, ("subject",), decimal_paths),
        "action": decision.action,
        "inputs": _encode_value(decision.inputs, ("inputs",), decimal_paths),
        "rule": decision.rule,
        "result": _encode_value(decision.result, ("result",), decimal_paths),
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "timestamp": timestamp_value,
        "overridable": decision.overridable,
        "timestamp_type": timestamp_type,
    }
    record["decimal_paths"] = [
        list(path) for path in sorted(decimal_paths, key=repr)
    ]
    return record


def _decode_value(
    value: object,
    decimal_paths: set[tuple[str | int, ...]],
    path: tuple[str | int, ...],
    seen_paths: set[tuple[str | int, ...]],
) -> object:
    if path in decimal_paths:
        if not isinstance(value, str):
            raise AuditIntegrityError(f"Decimal at path {path!r} is not a string")
        try:
            decimal_value = Decimal(value)
            if _decimal_string(decimal_value) != value:
                raise AuditIntegrityError(
                    f"Decimal at path {path!r} is not in canonical 2dp form"
                )
        except InvalidDecisionError as error:
            raise AuditIntegrityError(str(error)) from error
        except Exception as error:
            raise AuditIntegrityError(f"invalid Decimal at path {path!r}") from error
        seen_paths.add(path)
        return decimal_value
    if isinstance(value, dict):
        return {
            key: _decode_value(item, decimal_paths, path + (key,), seen_paths)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _decode_value(item, decimal_paths, path + (index,), seen_paths)
            for index, item in enumerate(value)
        ]
    return value


def _decision_from_record(record: object, line_number: int) -> Decision:
    if not isinstance(record, dict):
        raise AuditIntegrityError(f"line {line_number} is not a JSON object")

    # The chain fields seal the record but are not part of the decision, so they
    # are verified separately and dropped before the field check below.
    record = {key: value for key, value in record.items() if key not in _CHAIN_FIELDS}

    expected_fields = {
        "decision_id",
        "stage",
        "subject",
        "action",
        "inputs",
        "rule",
        "result",
        "confidence",
        "reasoning",
        "timestamp",
        "overridable",
        "timestamp_type",
        "decimal_paths",
    }
    if set(record) != expected_fields:
        missing = sorted(expected_fields - set(record))
        extra = sorted(set(record) - expected_fields)
        raise AuditIntegrityError(
            f"line {line_number} has invalid fields; missing={missing}, extra={extra}"
        )

    raw_paths = record["decimal_paths"]
    if not isinstance(raw_paths, list):
        raise AuditIntegrityError(f"line {line_number} has invalid decimal_paths")
    converted_paths: list[tuple[str | int, ...]] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, list) or not all(
            (isinstance(token, str) or type(token) is int) for token in raw_path
        ):
            raise AuditIntegrityError(f"line {line_number} has an invalid Decimal path")
        converted_paths.append(tuple(raw_path))
    decimal_paths = set(converted_paths)
    if len(decimal_paths) != len(converted_paths):
        raise AuditIntegrityError(f"line {line_number} contains duplicate Decimal paths")

    payload = {key: value for key, value in record.items() if key != "decimal_paths"}
    seen_paths: set[tuple[str | int, ...]] = set()
    decoded = _decode_value(payload, decimal_paths, (), seen_paths)
    if seen_paths != decimal_paths:
        raise AuditIntegrityError(f"line {line_number} contains unused Decimal paths")
    assert isinstance(decoded, dict)

    timestamp_type = decoded.pop("timestamp_type")
    timestamp_value = decoded["timestamp"]
    if not isinstance(timestamp_value, str):
        raise AuditIntegrityError(f"line {line_number} has a non-string timestamp")
    if timestamp_type == "datetime":
        try:
            decoded["timestamp"] = datetime.fromisoformat(timestamp_value)
        except ValueError as error:
            raise AuditIntegrityError(
                f"line {line_number} has an invalid datetime timestamp"
            ) from error
    elif timestamp_type != "str":
        raise AuditIntegrityError(f"line {line_number} has an invalid timestamp_type")

    serialized_id = decoded.pop("decision_id")
    if not isinstance(serialized_id, str):
        raise AuditIntegrityError(f"line {line_number} has a non-string decision_id")
    try:
        decision = Decision(**decoded)  # type: ignore[arg-type]
    except (TypeError, InvalidDecisionError) as error:
        raise AuditIntegrityError(f"invalid decision on line {line_number}: {error}") from error
    if decision.decision_id != serialized_id:
        raise AuditIntegrityError(
            f"decision_id mismatch on line {line_number}; record may have been altered"
        )
    return decision


class AuditLog:
    """An append-only sequence of decisions with deterministic JSONL I/O.

    CPython 3.11's JSON encoder emits the shortest decimal representation that
    round-trips to the same binary float, and its decoder reconstructs that
    float.  Consequently confidence values retain their exact ``float.hex()``
    value across ``write``/``read`` while remaining JSON numbers.  Decimal
    amounts are written as canonical 2dp strings; per-record ``decimal_paths``
    metadata restores only those strings to :class:`~decimal.Decimal`.
    """

    def __init__(self, decisions: Iterable[Decision] = ()) -> None:
        self._decisions: list[Decision] = []
        self._by_id: dict[str, Decision] = {}
        for decision in decisions:
            self.append(decision)

    def __len__(self) -> int:
        return len(self._decisions)

    def __iter__(self) -> Iterator[Decision]:
        return iter(self._decisions)

    def __getitem__(self, index: int | slice) -> Decision | tuple[Decision, ...]:
        if isinstance(index, slice):
            return tuple(self._decisions[index])
        return self._decisions[index]

    @property
    def decisions(self) -> tuple[Decision, ...]:
        """Return an immutable snapshot of the log entries."""

        return tuple(self._decisions)

    def append(self, decision: Decision) -> None:
        """Append a new decision, rejecting replacement of an existing ID."""

        if not isinstance(decision, Decision):
            raise TypeError("AuditLog can only append Decision instances")
        if decision.decision_id in self._by_id:
            raise DuplicateDecisionError(
                f"decision_id {decision.decision_id!r} already exists; audit entries "
                "cannot be replaced or mutated"
            )
        self._decisions.append(decision)
        self._by_id[decision.decision_id] = decision

    def get(self, decision_id: str) -> Decision:
        """Return a decision by ID, raising for an unknown ID."""

        try:
            return self._by_id[decision_id]
        except KeyError as error:
            raise UnknownDecisionError(
                f"decision_id {decision_id!r} does not exist in the audit log"
            ) from error

    def by_stage(self, stage: str) -> tuple[Decision, ...]:
        """Return decisions made by *stage* in append order."""

        return tuple(decision for decision in self._decisions if decision.stage == stage)

    def by_subject(self, subject: Subject) -> tuple[Decision, ...]:
        """Return decisions for *subject* in append order."""

        normalised = _normalise_subject(subject)
        return tuple(
            decision for decision in self._decisions if decision.subject == normalised
        )

    def chain(self) -> tuple[str, ...]:
        """The hash chain this log seals to, in append order.

        Recomputed from the decisions rather than cached, so it cannot drift out
        of step with what ``write`` would emit.
        """

        hashes: list[str] = []
        previous = GENESIS_HASH
        for decision in self._decisions:
            record = _decision_to_record(decision)
            record["previous_hash"] = previous
            previous = _record_hash(previous, record)
            hashes.append(previous)
        return tuple(hashes)

    @property
    def head_hash(self) -> str:
        """The single value that attests to the whole log.

        ``GENESIS_HASH`` for an empty log. Publishing this one string somewhere
        the writer cannot reach is what turns a detectable-in-principle chain
        into an actually trustworthy one.
        """

        chain = self.chain()
        return chain[-1] if chain else GENESIS_HASH

    def write(self, path: str | PathLike[str]) -> None:
        """Write the complete log as deterministic, hash-chained UTF-8 JSONL."""

        with Path(path).open("w", encoding="utf-8", newline="\n") as output:
            previous = GENESIS_HASH
            for decision in self._decisions:
                record = _decision_to_record(decision)
                record["previous_hash"] = previous
                record["record_hash"] = _record_hash(previous, record)
                previous = record["record_hash"]
                output.write(_dumps(record))
                output.write("\n")

    @classmethod
    def read(cls, path: str | PathLike[str], *, verify: bool = True) -> AuditLog:
        """Read JSONL and validate the chain, IDs, types, and duplicates.

        ``verify=False`` reads a log written before the chain existed. It is not
        the default, because a reader that silently accepts an unsealed file
        gives exactly the false assurance the chain was added to remove.
        """

        log = cls()
        previous = GENESIS_HASH
        with Path(path).open("r", encoding="utf-8", newline="") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise AuditIntegrityError(f"blank line at line {line_number}")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AuditIntegrityError(
                        f"invalid JSON on line {line_number}: {error.msg}"
                    ) from error
                if verify:
                    if not isinstance(record, dict):
                        raise AuditIntegrityError(
                            f"line {line_number} is not a JSON object"
                        )
                    previous = _verify_chain_link(record, previous, line_number)
                decision = _decision_from_record(record, line_number)
                try:
                    log.append(decision)
                except DuplicateDecisionError as error:
                    raise AuditIntegrityError(
                        f"duplicate decision on line {line_number}: {decision.decision_id}"
                    ) from error
        return log


@dataclass(frozen=True, slots=True)
class Override:
    """A human instruction layered over one immutable decision."""

    decision_id: str
    new_action: str
    reason: str
    operator: str
    timestamp: Timestamp

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise InvalidDecisionError("override decision_id must be a non-empty string")
        if self.new_action not in ACTIONS:
            raise InvalidDecisionError(
                f"override action must be one of {', '.join(sorted(ACTIONS))}"
            )
        if not isinstance(self.reason, str) or not self.reason:
            raise InvalidDecisionError("override reason must be a non-empty string")
        if not isinstance(self.operator, str) or not self.operator:
            raise InvalidDecisionError("override operator must be a non-empty string")
        _validate_timestamp(self.timestamp)


@dataclass(frozen=True, slots=True)
class EffectiveDecision:
    """A resolved decision that retains its original and override provenance."""

    original: Decision
    effective_action: str
    override: Override | None = None

    @property
    def decision_id(self) -> str:
        return self.original.decision_id

    @property
    def stage(self) -> str:
        return self.original.stage

    @property
    def subject(self) -> Subject:
        return self.original.subject

    @property
    def action(self) -> str:
        """Return the effective action after applying a possible override."""

        return self.effective_action

    @property
    def original_action(self) -> str:
        return self.original.action

    @property
    def inputs(self) -> Mapping[str, object]:
        return self.original.inputs

    @property
    def rule(self) -> str:
        return self.original.rule

    @property
    def result(self) -> object:
        return self.original.result

    @property
    def confidence(self) -> float | None:
        return self.original.confidence

    @property
    def reasoning(self) -> str:
        return self.original.reasoning

    @property
    def timestamp(self) -> Timestamp:
        return self.original.timestamp

    @property
    def overridable(self) -> bool:
        return self.original.overridable

    @property
    def overridden(self) -> bool:
        return self.override is not None

    @property
    def overridden_by(self) -> str | None:
        return self.override.operator if self.override is not None else None

    @property
    def override_reason(self) -> str | None:
        return self.override.reason if self.override is not None else None

    @property
    def override_timestamp(self) -> Timestamp | None:
        return self.override.timestamp if self.override is not None else None


class EffectiveDecisions:
    """Immutable resolved view returned by :meth:`OverrideSet.apply`."""

    def __init__(self, decisions: Iterable[EffectiveDecision]) -> None:
        self._decisions = tuple(decisions)
        self._by_id = {decision.decision_id: decision for decision in self._decisions}

    def __len__(self) -> int:
        return len(self._decisions)

    def __iter__(self) -> Iterator[EffectiveDecision]:
        return iter(self._decisions)

    def __getitem__(
        self, index: int | slice
    ) -> EffectiveDecision | tuple[EffectiveDecision, ...]:
        return self._decisions[index]

    def get(self, decision_id: str) -> EffectiveDecision:
        """Return an effective decision by ID, raising for an unknown ID."""

        try:
            return self._by_id[decision_id]
        except KeyError as error:
            raise UnknownDecisionError(
                f"decision_id {decision_id!r} does not exist in the effective view"
            ) from error

    def by_stage(self, stage: str) -> tuple[EffectiveDecision, ...]:
        """Return effective decisions made by *stage*."""

        return tuple(decision for decision in self._decisions if decision.stage == stage)

    def by_subject(self, subject: Subject) -> tuple[EffectiveDecision, ...]:
        """Return effective decisions for *subject*."""

        normalised = _normalise_subject(subject)
        return tuple(
            decision for decision in self._decisions if decision.subject == normalised
        )


class OverrideSet:
    """A deterministic set containing at most one override per decision."""

    def __init__(self, overrides: Iterable[Override] = ()) -> None:
        self._overrides: list[Override] = []
        self._by_id: dict[str, Override] = {}
        for override in overrides:
            self.add(override)

    def __len__(self) -> int:
        return len(self._overrides)

    def __iter__(self) -> Iterator[Override]:
        return iter(self._overrides)

    def add(self, override: Override) -> None:
        """Add an override, rejecting ambiguous duplicate targets."""

        if not isinstance(override, Override):
            raise TypeError("OverrideSet can only contain Override instances")
        if override.decision_id in self._by_id:
            raise DuplicateOverrideError(
                f"decision_id {override.decision_id!r} already has an override"
            )
        self._overrides.append(override)
        self._by_id[override.decision_id] = override

    def apply(self, log: AuditLog) -> EffectiveDecisions:
        """Validate and layer these overrides over *log* without changing it."""

        for override in self._overrides:
            try:
                original = log.get(override.decision_id)
            except UnknownDecisionError as error:
                raise UnknownDecisionError(
                    f"cannot override unknown decision_id {override.decision_id!r}"
                ) from error
            if not original.overridable:
                raise NonOverridableDecisionError(
                    f"decision_id {override.decision_id!r} is not overridable"
                )

        return EffectiveDecisions(
            EffectiveDecision(
                original=decision,
                effective_action=(
                    self._by_id[decision.decision_id].new_action
                    if decision.decision_id in self._by_id
                    else decision.action
                ),
                override=self._by_id.get(decision.decision_id),
            )
            for decision in log
        )


def _confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "deterministic"
    if confidence < 0.5:
        return "low"
    if confidence < 0.8:
        return "medium"
    return "high"


def summarise(
    log: AuditLog, overrides: OverrideSet | None = None
) -> dict[str, object]:
    """Count effective decisions by stage, action, and confidence band.

    Confidence bands are ``low`` for values below 0.5, ``medium`` for values
    from 0.5 up to (but excluding) 0.8, ``high`` from 0.8 through 1.0, and
    ``deterministic`` when confidence is ``None``.
    """

    effective = (overrides or OverrideSet()).apply(log)
    stage_counts = Counter(decision.stage for decision in effective)
    action_counts = Counter(decision.action for decision in effective)
    confidence_counts = Counter(
        _confidence_band(decision.confidence) for decision in effective
    )
    band_order = ("deterministic", "low", "medium", "high")
    return {
        "by_stage": dict(sorted(stage_counts.items())),
        "by_action": dict(sorted(action_counts.items())),
        "by_confidence_band": {
            band: confidence_counts[band]
            for band in band_order
            if confidence_counts[band]
        },
        "overrides_applied": sum(decision.overridden for decision in effective),
    }


def _expand(paths: Iterable[Path]) -> list[Path]:
    """Resolve each argument to concrete log files.

    A directory expands to every ``audit.jsonl`` beneath it. Expanding here
    rather than in the Makefile is not a style preference: ``$(wildcard)`` is
    evaluated when the Makefile is parsed, so a target that wrote journals and
    then verified them in the same invocation would verify the empty set that
    existed before the run started.
    """

    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("audit.jsonl")))
        elif path.exists():
            found.append(path)
        # A path that is neither contributes nothing. The caller reports every
        # argument it searched when the total comes to zero, which says more
        # than a per-path "no such file" would.
    return found


def main(argv: list[str] | None = None) -> int:
    """Verify the hash chain of one or more audit logs.

    Exits non-zero on the first failure so it can gate a build, and non-zero
    when there is nothing to verify at all -- a check that passes over an empty
    set is not a check. An audit trail nothing re-reads is a log file.
    """

    parser = argparse.ArgumentParser(
        prog="python -m recon.match.audit",
        description="Verify the tamper-evident hash chain of an audit log.",
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="+",
        help="JSONL audit log(s), or directories to search for audit.jsonl",
    )
    args = parser.parse_args(argv)

    logs = _expand(args.path)
    if not logs:
        print(
            "no audit logs found in "
            + ", ".join(str(path) for path in args.path)
            + " -- run 'make audit' to write them"
        )
        return 1

    status = 0
    for path in logs:
        try:
            log = AuditLog.read(path)
        except (AuditError, OSError) as error:
            print(f"FAIL  {path}: {error}")
            status = 1
        else:
            print(f"OK    {path}: {len(log)} records, head {log.head_hash}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
