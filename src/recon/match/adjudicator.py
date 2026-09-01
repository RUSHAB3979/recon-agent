"""Rung three: read the evidence the gates cannot read.

WHAT THIS IS FOR

    The deterministic ladder resolves an anonymous settlement line only when
    exactly one candidate survives nine admissibility gates. On the primary
    batch it leaves 16 lines standing with two survivors apiece. Those lines are
    not failures -- declining them is the correct behaviour, and the engine
    scores 0.00% false matches precisely because it declines them -- but a
    residual that nothing ever acts on is a residual that stays on somebody's
    desk forever.

    This module acts on exactly those lines and nothing else.

WHAT WAS CUT, AND WHY

    An earlier design had the model PROPOSE which record to test while the
    deterministic engine ruled on whether it passed. That was removed as
    circular. Proposing candidates is a search problem the gates already solve
    exhaustively: by the time a line reaches here, the complete set of
    admissible candidates is known, closed, and provably correct. A model asked
    to propose members of a set that has already been enumerated can only be
    redundant when it is right and noise when it is wrong.

    What is left is narrower and does not overlap anything the engine can do:

        the gates decide WHICH CANDIDATES ARE ADMISSIBLE
        this module decides WHICH ADMISSIBLE CANDIDATE THE EVIDENCE NAMES

FOUR STRUCTURAL GUARANTEES, NOT FOUR PROMISES

    1. It never sees a resolved case. The input is the abstention list, which
       is produced by the ladder and cannot be widened from here.

    2. It answers from a closed list. The model is shown lettered candidates and
       must return a letter or decline. A returned label outside the shortlist
       is discarded by :class:`AdjudicationPass`, not trusted and repaired --
       the model does not get to name an event the gates never admitted.

    3. It does no arithmetic, because there is none left to do. Every candidate
       reaching it has already matched the delta exactly in integer paise, so
       the amounts are identical across the shortlist by construction and carry
       exactly zero discriminating information. The prompt says so explicitly,
       which is what stops the model inventing a numeric justification for a
       decision it actually made on other grounds.

    4. Abstention survives. A confidence below ``min_confidence`` is recorded as
       a decline, and declining is always available. This matters because the
       residual is deliberately mixed: on the primary batch 12 of the 16 lines
       are separable from the settlement note and 4 are not. An adjudicator
       that resolves everything handed to it scores the first group and false
       matches the second, which is a worse outcome than declining all 16.

THE READER IS INJECTED, AND THE DEFAULT DECLINES

    :class:`EvidenceReader` is a one-method protocol. The default
    implementation, :class:`DecliningReader`, declines every line -- so a
    checkout with no API key runs the whole pipeline, reproduces today's
    published numbers exactly, and degrades to "routed to human review" rather
    than crashing or silently guessing. The Anthropic-backed reader is one
    implementation of the protocol and is not privileged in the design.

    That is also the honest framing of the claim. What has been measured is
    that arithmetic, exact matching and lexical similarity cannot separate these
    candidates. It has NOT been shown that a language model is the only thing
    that can. An embedding model, a classifier, or a maintained product-to-
    category map could each plausibly serve here; they would implement this same
    protocol. The claim the repo defends is that the residual requires semantic
    interpretation of evidence outside the accounting ladder -- not that any
    particular mechanism is necessary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
import json
import os
from typing import Protocol

from recon.match.normalize import Batch, DetailLine
from recon.match.passes import (
    Abstention,
    Cardinality,
    Claim,
    ClaimTier,
    PassResult,
)

__all__ = [
    "Adjudication",
    "AdjudicationPass",
    "AdjudicationRequest",
    "AnthropicReader",
    "Candidate",
    "DecliningReader",
    "EvidenceReader",
    "ScriptedReader",
    "Usage",
    "build_request",
    "default_reader",
]


# Standard list prices, US dollars per million tokens, as (input, output).
# Quoted at standard rather than promotional rates on purpose: an introductory
# price that expires before the thing ships is not the cost of running it.
PRICING: Mapping[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5": (Decimal("3"), Decimal("15")),
    "claude-haiku-4-5-20251001": (Decimal("1"), Decimal("5")),
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Below this, a stated preference is recorded as a decline. It is a declared
# policy rather than a fitted threshold, and it is tuned -- if at all -- against
# data/dev only.
DEFAULT_MIN_CONFIDENCE = 0.70

# Letters, not event ids, are what the model answers with. An id is a token
# sequence a model can plausibly complete into something that does not exist; a
# single letter from a list of two or three cannot be, and the mapping back to
# the real id happens in this file where it can be checked.
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class Usage:
    """Token and cost accounting for one call, or a sum over many."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            calls=self.calls + other.calls,
        )

    def cost_usd(self, model: str) -> Decimal:
        """Cost in dollars, quantised to six places.

        Cached reads are billed at a tenth of the input rate. An unknown model
        returns zero rather than guessing a price -- a fabricated cost is worse
        than an absent one, because it looks like a measurement.
        """

        rates = PRICING.get(model)
        if rates is None:
            return Decimal("0")
        input_rate, output_rate = rates
        billed_input = Decimal(self.input_tokens) * input_rate
        billed_cache = Decimal(self.cache_read_tokens) * input_rate / 10
        billed_output = Decimal(self.output_tokens) * output_rate
        total = (billed_input + billed_cache + billed_output) / Decimal(1_000_000)
        return total.quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class Candidate:
    """One admissible event, with only the fields that can discriminate.

    Amount and date are deliberately absent. Every candidate on a shortlist
    matched the delta exactly and fell inside the same window -- that is what
    gates 2 and 3 established -- so including them would offer the model a
    difference that does not exist and invite a fabricated justification.
    """

    label: str
    event_id: str
    parent_txn_id: str
    parent_description: str


@dataclass(frozen=True, slots=True)
class AdjudicationRequest:
    """One undecidable line, and the closed set of candidates for it."""

    settlement_id: str
    detail_id: str
    note: str
    candidates: tuple[Candidate, ...]

    def candidate_by_label(self, label: str) -> Candidate | None:
        for candidate in self.candidates:
            if candidate.label == label:
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class Adjudication:
    """What a reader concluded. ``label is None`` means it declined."""

    detail_id: str
    label: str | None
    confidence: float
    reasoning: str
    model: str = ""
    usage: Usage = field(default_factory=Usage)


class EvidenceReader(Protocol):
    """Anything that can read a note and name a candidate, or decline."""

    model: str

    def read(self, request: AdjudicationRequest) -> Adjudication: ...


class DecliningReader:
    """The null reader: declines every line.

    This is the default, and it is not a stub. With it the pipeline produces
    exactly the numbers the deterministic engine produces on its own, which
    means the repo's published results never depend on a network call, an API
    key, or a model's mood. Turning the real reader on can only move the
    residual; it cannot silently move anything else.
    """

    # Named rather than blank, because this identity is written into the sealed
    # audit record like any other reader's. A record whose decider is empty is
    # ambiguous between "nothing read this" and "the field was never filled in";
    # "no-reader" says which. It is absent from PRICING, so it also costs
    # nothing, which is the other true thing about it.
    model = "no-reader"

    def read(self, request: AdjudicationRequest) -> Adjudication:
        return Adjudication(
            detail_id=request.detail_id,
            label=None,
            confidence=0.0,
            reasoning="no evidence reader configured; routed to human review",
            model=self.model,
        )


class ScriptedReader:
    """A reader driven by a fixed mapping, for tests.

    Tests of the surrounding machinery -- the closed-list guard, the confidence
    floor, the audit counters -- must not depend on a live model, or they would
    be measuring the model rather than the code under test.
    """

    model = "scripted"

    def __init__(
        self,
        answers: Mapping[str, tuple[str | None, float]],
        *,
        default: tuple[str | None, float] = (None, 0.0),
    ) -> None:
        self._answers = dict(answers)
        self._default = default
        self.seen: list[AdjudicationRequest] = []

    def read(self, request: AdjudicationRequest) -> Adjudication:
        self.seen.append(request)
        label, confidence = self._answers.get(request.detail_id, self._default)
        return Adjudication(
            detail_id=request.detail_id,
            label=label,
            confidence=confidence,
            reasoning="scripted",
            model=self.model,
            usage=Usage(input_tokens=0, output_tokens=0, calls=1),
        )


# --------------------------------------------------------------------------
# the Anthropic-backed reader
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are adjudicating a single unresolved line in a payment reconciliation.

A deterministic engine has already done all of the accounting. It has verified,
for every candidate below, that the amount reproduces the settlement delta
exactly in integer paise, that the date falls inside the settlement window, that
the currency agrees, that the parent payment exists and itself settled, and that
committing to the candidate would leave every other line in the batch solvable.

Those checks passed for EVERY candidate. The amounts are identical. The dates
are inside the same window. Nothing numeric distinguishes them and you must not
claim that anything numeric does. Arithmetic is not your job and there is none
left to do.

Your job is narrower: an operations note was written against the settlement
line, and each candidate refund traces back to a payment for a particular
product. Decide whether the note is written about one of those products
specifically.

Answer with the letter of that candidate, or with null.

Answer null when the note could describe any of the candidates equally well, or
describes none of them, or is too generic to separate them. Declining is a
correct and expected answer, not a failure: a wrong attribution silently
finalises a wrong number in a ledger, while a decline routes the line to a human
who will resolve it. Some of the lines you will see are deliberately not
separable. If you find yourself reaching for a reason, the answer is null.

Confidence is your probability that the letter you named is correct. Report it
honestly; a well-calibrated 0.55 is more useful than a reflexive 0.95."""


RESPONSE_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "label": {
            "type": ["string", "null"],
            "description": "Letter of the chosen candidate, or null to decline.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Probability the named candidate is correct.",
        },
        "reasoning": {
            "type": "string",
            "description": (
                "One or two sentences citing the note and the product it names. "
                "Do not cite amounts or dates; they are identical."
            ),
        },
    },
    "required": ["label", "confidence", "reasoning"],
    "additionalProperties": False,
}


def render_request(request: AdjudicationRequest) -> str:
    """The user turn. Kept in one function so tests can read what was sent."""

    lines = [
        f"Settlement line: {request.detail_id}",
        f"Operations note: {request.note!r}",
        "",
        "Candidates (identical in amount, currency and window):",
    ]
    for candidate in request.candidates:
        lines.append(
            f"  {candidate.label}. refund of payment {candidate.parent_txn_id}"
            f" -- product: {candidate.parent_description!r}"
        )
    lines.append("")
    lines.append("Which candidate is the note about? Answer null if it does not say.")
    return "\n".join(lines)


class AnthropicReader:
    """Reads the note with a Claude model and returns a structured verdict.

    The system prompt is marked cacheable because it is byte-identical on every
    call and dominates the input. Cost per 500 records is a reported metric, and
    paying full input price for the same 300-token preamble sixteen times would
    make that metric wrong in the direction that flatters us.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        effort: str = "low",
        max_tokens: int = 1024,
    ) -> None:
        if client is None:
            import anthropic  # imported lazily so the package stays optional

            client = anthropic.Anthropic()
        self._client = client
        self.model = model
        self._effort = effort
        self._max_tokens = max_tokens

    def read(self, request: AdjudicationRequest) -> Adjudication:
        try:
            response = self._call(request)
        except Exception as error:  # narrowed below by _transport_failure
            failure = _transport_failure(error)
            if failure is None:
                raise
            # A line the network ate is a line for a human, not a line to guess
            # at. Declining keeps the failure visible in the exception list
            # instead of turning it into a silent gap in the batch.
            return Adjudication(
                detail_id=request.detail_id,
                label=None,
                confidence=0.0,
                reasoning=f"adjudicator unreachable ({failure}); routed to human review",
                model=self.model,
                usage=Usage(calls=1),
            )
        return self._to_adjudication(request, response)

    def _call(self, request: AdjudicationRequest) -> object:
        return self._client.messages.create(  # type: ignore[attr-defined]
            model=self.model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": render_request(request)}],
            thinking={"type": "adaptive"},
            output_config={  # type: ignore[arg-type]
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": dict(RESPONSE_SCHEMA)},
            },
        )

    def _to_adjudication(self, request: AdjudicationRequest, response: object) -> Adjudication:
        usage = getattr(response, "usage", None)
        recorded = Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            calls=1,
        )

        payload = _extract_json(response)
        if payload is None:
            # A malformed response is a decline, never an exception that takes
            # the batch down and never a guess. One unreadable line must not
            # cost the other 449.
            return Adjudication(
                detail_id=request.detail_id,
                label=None,
                confidence=0.0,
                reasoning="model returned no parseable verdict; declined",
                model=self.model,
                usage=recorded,
            )

        label = payload.get("label")
        confidence = payload.get("confidence", 0.0)
        return Adjudication(
            detail_id=request.detail_id,
            label=label if isinstance(label, str) else None,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
            reasoning=str(payload.get("reasoning", "")),
            model=self.model,
            usage=recorded,
        )


def _transport_failure(error: Exception) -> str | None:
    """Name the failure if it is the API failing, otherwise return None.

    Ordered most specific first, and deliberately not a bare ``except``: a
    ``TypeError`` from a bug in this module must still crash the run, because a
    bug that silently degrades to "declined every line" would look exactly like
    a model that declined every line.
    """

    try:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            NotFoundError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover - anthropic not installed
        return None

    for kind, label in (
        (NotFoundError, "model or endpoint not found"),
        (RateLimitError, "rate limited"),
        (APIStatusError, "API error"),
        (APIConnectionError, "connection error"),
    ):
        if isinstance(error, kind):
            return label
    return None


def _extract_json(response: object) -> dict[str, object] | None:
    """Pull the structured verdict out of a Messages response."""

    for block in getattr(response, "content", ()) or ():
        if getattr(block, "type", None) != "text":
            continue
        try:
            parsed = json.loads(getattr(block, "text", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def default_reader() -> EvidenceReader:
    """An Anthropic reader when a key is configured, otherwise the null one.

    Selection is by environment, not by flag, so that the same command produces
    the deterministic-only numbers on a machine with no key -- which is what CI
    runs, and therefore what the published figures are.
    """

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicReader()
        except Exception:  # pragma: no cover - import or construction failure
            return DecliningReader()
    return DecliningReader()


# --------------------------------------------------------------------------
# the rung
# --------------------------------------------------------------------------


class AdjudicationPass:
    """Acts on the ladder's abstentions, and on nothing else."""

    name = "adjudication"
    cardinality = Cardinality.ONE_TO_ONE
    tolerance = None

    def __init__(
        self,
        reader: EvidenceReader | None = None,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_calls: int | None = None,
    ) -> None:
        self.reader = reader if reader is not None else DecliningReader()
        self.min_confidence = min_confidence
        self.max_calls = max_calls
        self.adjudications: list[Adjudication] = []
        self.usage = Usage()

    def run(self, batch: Batch, consumed: frozenset[str]) -> PassResult:
        """Present for the :class:`Pass` protocol; this rung needs residuals.

        Running it without them is a no-op rather than an error, because a
        ladder assembled without the residual plumbing should degrade to the
        deterministic result, not raise in the middle of a batch.
        """

        return PassResult(pass_name=self.name, examined=0)

    def run_residual(
        self,
        batch: Batch,
        consumed: frozenset[str],
        abstentions: Sequence[Abstention],
    ) -> PassResult:
        result = PassResult(pass_name=self.name)
        detail_by_id = {line.detail_id: line for line in batch.details}

        for abstention in abstentions:
            line = detail_by_id.get(abstention.detail_id)
            if line is None:
                continue

            # Only events still unclaimed are on the table. An earlier rung may
            # have consumed one since the abstention was recorded, and a
            # shortlist that has narrowed to a single candidate is a fact the
            # gates already own -- claiming it here would be this rung taking
            # credit for gate 9's work.
            available = tuple(
                event_id
                for event_id in abstention.candidate_event_ids
                if event_id not in consumed
            )
            if len(available) < 2:
                result.abstentions.append(abstention)
                continue

            request = build_request(batch, line, available)
            if request is None or not request.note:
                # No reader was consulted, so none is named. These two declines
                # are this rung's own rules firing -- there is nothing to read,
                # or there is no budget left to read it with -- and attributing
                # them to a model would put its name on a decision it never saw.
                result.abstentions.append(
                    _declined(abstention, "no semantic evidence on the line")
                )
                continue

            if self.max_calls is not None and result.examined >= self.max_calls:
                result.abstentions.append(
                    _declined(abstention, "call budget exhausted; routed to human review")
                )
                continue

            result.examined += 1
            verdict = self.reader.read(request)
            self.adjudications.append(verdict)
            self.usage = self.usage + verdict.usage

            claim = self._to_claim(request, verdict, line)
            if claim is None:
                result.abstentions.append(
                    _declined(
                        abstention,
                        _decline_reason(request, verdict, self.min_confidence),
                        decided_by=verdict.model or None,
                    )
                )
                continue
            result.claims.append(claim)

        result.counters.update(
            {
                "calls": self.usage.calls,
                "lines_adjudicated": result.examined,
                "resolved": len(result.claims),
                "declined": len(result.abstentions),
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
            }
        )
        return result

    def _to_claim(
        self, request: AdjudicationRequest, verdict: Adjudication, line: DetailLine
    ) -> Claim | None:
        if verdict.label is None:
            return None

        # The closed-list guard. A label the shortlist does not contain is
        # discarded rather than repaired: the gates decided which events are
        # admissible and this rung has no standing to add one.
        candidate = request.candidate_by_label(verdict.label)
        if candidate is None:
            return None

        if verdict.confidence < self.min_confidence:
            return None

        return Claim(
            settlement_id=request.settlement_id,
            event_id=candidate.event_id,
            detail_id=request.detail_id,
            pass_name=self.name,
            # SUGGESTED, never CONFIRMED. The gates could not separate these
            # candidates, so no amount of fluent reasoning turns the result into
            # a proof. The tier is what tells a reviewer which claims to look at
            # first, and this rung's claims are exactly those claims.
            tier=ClaimTier.SUGGESTED,
            confidence=verdict.confidence,
            reasons=(f"adjudicated on note evidence: {verdict.reasoning}",),
            # The reader that actually decided, carried into the sealed record.
            # ``verdict.model`` rather than ``self.reader.model`` so that a
            # reader which routes between models reports the one that answered
            # this line, not the one it was constructed with.
            decided_by=verdict.model or None,
        )

    def cost_usd(self) -> Decimal:
        return self.usage.cost_usd(self.reader.model)


def _declined(
    abstention: Abstention, reason: str, *, decided_by: str | None = None
) -> Abstention:
    return Abstention(
        settlement_id=abstention.settlement_id,
        detail_id=abstention.detail_id,
        pass_name="adjudication",
        candidate_event_ids=abstention.candidate_event_ids,
        reason=reason,
        decided_by=decided_by,
    )


def _decline_reason(
    request: AdjudicationRequest, verdict: Adjudication, floor: float
) -> str:
    if verdict.label is None:
        return f"adjudicator declined: {verdict.reasoning}"
    if request.candidate_by_label(verdict.label) is None:
        return f"adjudicator named {verdict.label!r}, which is not a candidate; discarded"
    return (
        f"adjudicator preferred {verdict.label} at confidence "
        f"{verdict.confidence:.2f}, below the {floor:.2f} floor"
    )


def build_request(
    batch: Batch, line: DetailLine, event_ids: Iterable[str]
) -> AdjudicationRequest | None:
    """Assemble the closed candidate list for one undecidable line.

    A refund event carries no product description of its own -- that is a
    deliberate property of the schema -- so the description is reached through
    the refund's ``txn_id`` to its parent payment. The hop is the point: an
    agent that reads a category off the refund row is reading a column, while
    one that traverses lineage to find it is reconciling.
    """

    candidates: list[Candidate] = []
    for index, event_id in enumerate(sorted(event_ids)):
        event = batch.events.get(event_id)
        if event is None:
            continue
        parents = batch.payments_by_txn.get(event.txn_id, ())
        description = next(
            (parent.description for parent in parents if parent.description), ""
        )
        candidates.append(
            Candidate(
                label=LABELS[index % len(LABELS)],
                event_id=event_id,
                parent_txn_id=event.txn_id,
                parent_description=description,
            )
        )

    if len(candidates) < 2:
        return None

    return AdjudicationRequest(
        settlement_id=line.settlement_id,
        detail_id=line.detail_id,
        note=(line.reference_text or "").strip(),
        candidates=tuple(candidates),
    )
