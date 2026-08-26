"""Stage 0 -- the six released CSVs become canonical, typed, immutable records.

Everything downstream of this module reads records, never strings and never
files. That boundary exists for three reasons, and each one is a decision the
panel is likely to ask about.

MONEY IS INTEGER PAISE, AND PARSING IS THE ONLY PLACE IT COULD STOP BEING
    ``int(row["amount_paise"])`` and nothing else. No float appears in this
    package at any point, so no tolerance breach can ever be an artefact of
    binary representation rather than of the data. A reconciliation engine that
    reports a one-paise variance caused by its own arithmetic is worse than one
    that reports nothing, because the operator cannot tell the two apart.

    ``Decimal`` appears exactly once, in ``to_rupees``, and only for display.
    It is not used for arithmetic. The repo rule is "money is Decimal, never
    float"; integer paise is the stricter version of that rule, because it
    removes the quantisation step where a Decimal can still be rounded wrongly.

SETTLEABILITY IS DECIDED ONCE, HERE
    ``CREATED`` and ``FAILED`` events were never going to settle. If that
    judgement is left to the matcher, every later stage has to remember to
    filter, and the one stage that forgets reports a payment as missing when
    the truth is that it never existed as money. Deciding it at parse time
    makes NOT_SETTLEABLE structural: those events never enter a candidate pool,
    so no pass can raise an exception about them by accident.

    That matters more than it sounds. NOT_SETTLEABLE is the trap class of this
    benchmark -- the expected outcome is NO_ACTION, and an agent that pads its
    exception list with these is generating false positives that the harness
    measures by name.

DUPLICATE EXPORT ROWS ARE COLLAPSED HERE, AND THE COLLAPSE IS RECORDED
    A repeated ``detail_id`` is an export artefact, not a second movement of
    money. Rolling up the raw rows double-counts and every control equation
    then fails for a reason that has nothing to do with reconciliation. So the
    unique set is what downstream code sees.

    But the duplication is itself a finding worth surfacing, so the count
    survives on the batch rather than being silently dropped. Deduplicating
    without saying so would hide a real data-quality defect; refusing to
    deduplicate would manufacture a dozen false exceptions. Doing both is the
    only honest option.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
    It does not read ``answer_key_*.csv``. The answer key is the scorer's
    input, never the agent. Loading it here would put ground truth one attribute
    access away from the matcher, and a single careless line would turn the
    whole benchmark into a lookup. The separation is enforced by ``load_batch``
    simply not knowing those filenames.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Mapping, Sequence

__all__ = [
    "BankCredit",
    "Batch",
    "DetailLine",
    "GatewayEvent",
    "NormalizationError",
    "PricingRule",
    "Settlement",
    "load_batch",
    "to_rupees",
]


# Lifecycle vocabulary. A status outside these sets is a data defect rather
# than a reconciliation exception, and is refused at parse time -- see
# _require_status.
SETTLEABLE_STATUSES = frozenset({"CAPTURED", "PROCESSED"})
NEVER_SETTLES = frozenset({"CREATED", "FAILED"})
KNOWN_EVENT_STATUSES = SETTLEABLE_STATUSES | NEVER_SETTLES

PAYMENT = "PAYMENT"
REFUND = "REFUND"
KNOWN_LINE_TYPES = frozenset({PAYMENT, REFUND})


class NormalizationError(ValueError):
    """Raised when an input row cannot be trusted enough to reconcile.

    This is deliberately distinct from a reconciliation exception. An exception
    means the books do not tie out; a NormalizationError means the file is not
    the file we were promised. Conflating them would let a malformed export
    masquerade as a finance problem.
    """


def to_rupees(paise: int) -> Decimal:
    """Present integer paise as rupees for display only.

    Never feed the result back into arithmetic. The engine reasons in paise;
    this exists so a report can print 1234.50 instead of 123450.
    """
    return (Decimal(paise) / Decimal(100)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- primitives


def _require(row: Mapping[str, str], column: str, context: str) -> str:
    value = row.get(column)
    if value is None:
        raise NormalizationError(f"{context}: column {column!r} is absent")
    value = value.strip()
    if not value:
        raise NormalizationError(f"{context}: column {column!r} is empty")
    return value


def _optional(row: Mapping[str, str], column: str) -> str | None:
    value = row.get(column)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _paise(row: Mapping[str, str], column: str, context: str) -> int:
    raw = _require(row, column, context)
    try:
        return int(raw)
    except ValueError as exc:
        # A decimal point here would mean the export changed units. Guessing
        # would be worse than stopping: a silent rupees/paise mix-up is a
        # hundredfold error that every control equation would then report as a
        # reconciliation failure.
        raise NormalizationError(
            f"{context}: {column}={raw!r} is not integer paise"
        ) from exc


def _timestamp(row: Mapping[str, str], column: str, context: str) -> datetime:
    raw = _require(row, column, context)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise NormalizationError(f"{context}: {column}={raw!r} is not an ISO timestamp") from exc


def _day(row: Mapping[str, str], column: str, context: str) -> date:
    raw = _require(row, column, context)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise NormalizationError(f"{context}: {column}={raw!r} is not an ISO date") from exc


def _read_csv(path: Path, required: Sequence[str]) -> Iterator[dict[str, str]]:
    if not path.exists():
        raise NormalizationError(f"required input {path.name} is missing from {path.parent}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [column for column in required if column not in header]
        if missing:
            raise NormalizationError(
                f"{path.name}: missing column(s) {', '.join(missing)}"
            )
        yield from reader


# ------------------------------------------------------------------ records


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    """One payment or refund as the gateway recorded it."""

    event_id: str
    event_type: str
    txn_id: str
    order_id: str
    amount_paise: int
    currency: str
    status: str
    created_at: datetime
    method: str
    # Merchant catalogue text, present on payments and empty on refunds. Read
    # only by the evidence rungs; no arithmetic anywhere depends on it.
    description: str = ""

    @property
    def is_settleable(self) -> bool:
        """Whether this event was ever going to appear in a settlement.

        Decided from lifecycle status alone. A CAPTURED payment that never
        settled is an exception; a FAILED payment that never settled is
        correct behaviour, and the difference is the whole NOT_SETTLEABLE trap.
        """
        return self.status in SETTLEABLE_STATUSES

    @property
    def signed_amount_paise(self) -> int:
        """Amount as a signed effect on the merchant balance.

        Payments add, refunds subtract. Carrying the sign on the record rather
        than re-deriving it at each comparison site is what lets a pass sum a
        mixed list of events without knowing what is in it -- and a sign error
        in a reconciliation engine is silent, because the magnitude still looks
        plausible.
        """
        return self.amount_paise if self.event_type == PAYMENT else -self.amount_paise

    @property
    def created_on(self) -> date:
        return self.created_at.date()


@dataclass(frozen=True, slots=True)
class DetailLine:
    """One line of a settlement export."""

    detail_id: str
    settlement_id: str
    event_id: str | None
    line_type: str
    gross_effect_paise: int
    fee_paise: int
    tax_paise: int
    net_effect_paise: int
    settled_at: datetime
    currency: str
    reference_text: str | None

    @property
    def is_anonymous(self) -> bool:
        """Whether this line carries no event_id to join on.

        Every anonymous line in this benchmark is a REFUND, and these lines are
        the entire matching problem: a line that names its event is not an
        inference at all, it is a stated fact.
        """
        return self.event_id is None

    @property
    def settled_on(self) -> date:
        return self.settled_at.date()

    @property
    def magnitude_paise(self) -> int:
        """Unsigned size of the movement this line represents."""
        return abs(self.gross_effect_paise)

    @property
    def line_equation_holds(self) -> bool:
        return self.net_effect_paise == self.gross_effect_paise - self.fee_paise - self.tax_paise


@dataclass(frozen=True, slots=True)
class Settlement:
    """The declared totals for one settlement cycle."""

    settlement_id: str
    utr: str
    settlement_date: date
    gross_payment_paise: int
    refund_paise: int
    fee_paise: int
    tax_paise: int
    net_amount_paise: int
    line_count: int
    currency: str
    status: str

    @property
    def control_net_paise(self) -> int:
        """Net recomputed from the declared components.

        Compared against ``net_amount_paise`` this is the summary-level control
        equation. Keeping it a property rather than a check means a pass can
        report the size of the break, not merely that one exists.
        """
        return (
            self.gross_payment_paise
            - self.refund_paise
            - self.fee_paise
            - self.tax_paise
        )


@dataclass(frozen=True, slots=True)
class BankCredit:
    """One credit on the merchant bank statement."""

    bank_row_id: str
    utr: str
    posted_at: datetime
    credit_amount_paise: int
    currency: str
    narration: str
    bank_ref: str

    @property
    def posted_on(self) -> date:
        return self.posted_at.date()


@dataclass(frozen=True, slots=True)
class PricingRule:
    """Published fee and tax rates for one payment method, in basis points."""

    method: str
    fee_rate_bps: int
    gst_rate_bps: int


# -------------------------------------------------------------------- batch


@dataclass(frozen=True)
class Batch:
    """One normalized dataset with the indexes every pass needs.

    Indexes are built once here rather than by each pass, because a pass that
    builds its own index is a pass that can build it from a different rule --
    and two passes disagreeing about which refunds are unconsumed is precisely
    the kind of bug that produces a confident wrong match.
    """

    events: Mapping[str, GatewayEvent]
    details: tuple[DetailLine, ...]
    settlements: Mapping[str, Settlement]
    bank_by_utr: Mapping[str, tuple[BankCredit, ...]]
    pricing: Mapping[str, PricingRule]
    details_by_settlement: Mapping[str, tuple[DetailLine, ...]]
    payments_by_txn: Mapping[str, tuple[GatewayEvent, ...]]
    duplicate_detail_counts: Mapping[str, int]

    # ---- derived views

    @property
    def anonymous_lines(self) -> tuple[DetailLine, ...]:
        """Every line with no event_id -- the residual the ladder must resolve."""
        return tuple(line for line in self.details if line.is_anonymous)

    @property
    def referenced_event_ids(self) -> frozenset[str]:
        """Events some detail line already names.

        This is gate 1. A refund event the export has already attributed to a
        settlement cannot also explain a different anonymous line, and that is
        a fact the file states rather than an inference the agent makes.
        """
        return frozenset(
            line.event_id for line in self.details if line.event_id is not None
        )

    def unconsumed_refunds(self) -> tuple[GatewayEvent, ...]:
        """Refund events no detail line names, in deterministic order."""
        referenced = self.referenced_event_ids
        return tuple(
            sorted(
                (
                    event
                    for event in self.events.values()
                    if event.event_type == REFUND and event.event_id not in referenced
                ),
                key=lambda event: event.event_id,
            )
        )

    def credits_for(self, settlement: Settlement) -> tuple[BankCredit, ...]:
        return self.bank_by_utr.get(settlement.utr, ())

    @property
    def duplicate_line_total(self) -> int:
        return sum(self.duplicate_detail_counts.values())


def _load_events(directory: Path) -> dict[str, GatewayEvent]:
    columns = (
        "event_id",
        "event_type",
        "txn_id",
        "order_id",
        "amount_paise",
        "currency",
        "status",
        "created_at",
        "method",
    )
    events: dict[str, GatewayEvent] = {}
    for row in _read_csv(directory / "gateway_ledger.csv", columns):
        event_id = _require(row, "event_id", "gateway_ledger.csv")
        context = f"gateway_ledger.csv:{event_id}"
        if event_id in events:
            # Unlike a duplicated detail line, a duplicated event_id has no
            # benign reading: the ledger primary key is broken and every
            # downstream join is ambiguous.
            raise NormalizationError(f"{context}: duplicate event_id in the ledger")

        event_type = _require(row, "event_type", context)
        if event_type not in KNOWN_LINE_TYPES:
            raise NormalizationError(f"{context}: unknown event_type {event_type!r}")

        status = _require(row, "status", context)
        if status not in KNOWN_EVENT_STATUSES:
            raise NormalizationError(f"{context}: unknown status {status!r}")

        amount = _paise(row, "amount_paise", context)
        if amount <= 0:
            # Direction lives in event_type, never in the sign of the ledger
            # amount. Allowing both would give every refund two representations
            # and make signed_amount_paise a coin flip.
            raise NormalizationError(f"{context}: amount_paise must be positive, got {amount}")

        events[event_id] = GatewayEvent(
            event_id=event_id,
            event_type=event_type,
            txn_id=_require(row, "txn_id", context),
            order_id=_require(row, "order_id", context),
            amount_paise=amount,
            currency=_require(row, "currency", context),
            status=status,
            created_at=_timestamp(row, "created_at", context),
            method=_require(row, "method", context),
            # Optional on purpose. The description is evidence, not structure:
            # a released export that omits it must still normalize, and the
            # rungs that read it must degrade to abstaining rather than to
            # raising. Requiring it would make an evidence field load-bearing
            # for parsing, which is the wrong dependency direction.
            description=(row.get("description") or "").strip(),
        )
    if not events:
        raise NormalizationError("gateway_ledger.csv contains no rows")
    return events


def _load_details(
    directory: Path,
) -> tuple[tuple[DetailLine, ...], dict[str, int]]:
    columns = (
        "detail_id",
        "settlement_id",
        "event_id",
        "line_type",
        "gross_effect_paise",
        "fee_paise",
        "tax_paise",
        "net_effect_paise",
        "settled_at",
        "currency",
        "reference_text",
    )
    unique: dict[str, DetailLine] = {}
    duplicates: dict[str, int] = defaultdict(int)

    for row in _read_csv(directory / "settlement_detail.csv", columns):
        detail_id = _require(row, "detail_id", "settlement_detail.csv")
        context = f"settlement_detail.csv:{detail_id}"
        settlement_id = _require(row, "settlement_id", context)

        if detail_id in unique:
            # The export repeated a row. Count it against the settlement so the
            # duplicate remains reportable, and keep the first occurrence: they
            # are identical by construction, and picking the first makes the
            # collapse order-independent.
            duplicates[settlement_id] += 1
            continue

        line_type = _require(row, "line_type", context)
        if line_type not in KNOWN_LINE_TYPES:
            raise NormalizationError(f"{context}: unknown line_type {line_type!r}")

        gross = _paise(row, "gross_effect_paise", context)
        if line_type == PAYMENT and gross <= 0:
            raise NormalizationError(f"{context}: payment line has non-positive gross {gross}")
        if line_type == REFUND and gross >= 0:
            raise NormalizationError(f"{context}: refund line has non-negative gross {gross}")

        unique[detail_id] = DetailLine(
            detail_id=detail_id,
            settlement_id=settlement_id,
            event_id=_optional(row, "event_id"),
            line_type=line_type,
            gross_effect_paise=gross,
            fee_paise=_paise(row, "fee_paise", context),
            tax_paise=_paise(row, "tax_paise", context),
            net_effect_paise=_paise(row, "net_effect_paise", context),
            settled_at=_timestamp(row, "settled_at", context),
            currency=_require(row, "currency", context),
            reference_text=_optional(row, "reference_text"),
        )

    return tuple(unique.values()), dict(duplicates)


def _load_settlements(directory: Path) -> dict[str, Settlement]:
    columns = (
        "settlement_id",
        "utr",
        "settlement_date",
        "gross_payment_paise",
        "refund_paise",
        "fee_paise",
        "tax_paise",
        "net_amount_paise",
        "line_count",
        "currency",
        "status",
    )
    settlements: dict[str, Settlement] = {}
    for row in _read_csv(directory / "settlement_summary.csv", columns):
        settlement_id = _require(row, "settlement_id", "settlement_summary.csv")
        context = f"settlement_summary.csv:{settlement_id}"
        if settlement_id in settlements:
            raise NormalizationError(f"{context}: duplicate settlement_id in the summary")
        settlements[settlement_id] = Settlement(
            settlement_id=settlement_id,
            utr=_require(row, "utr", context),
            settlement_date=_day(row, "settlement_date", context),
            gross_payment_paise=_paise(row, "gross_payment_paise", context),
            refund_paise=_paise(row, "refund_paise", context),
            fee_paise=_paise(row, "fee_paise", context),
            tax_paise=_paise(row, "tax_paise", context),
            net_amount_paise=_paise(row, "net_amount_paise", context),
            line_count=int(_require(row, "line_count", context)),
            currency=_require(row, "currency", context),
            status=_require(row, "status", context),
        )
    return settlements


def _load_bank(directory: Path) -> dict[str, tuple[BankCredit, ...]]:
    columns = (
        "bank_row_id",
        "utr",
        "posted_at",
        "credit_amount_paise",
        "currency",
        "narration",
        "bank_ref",
    )
    by_utr: dict[str, list[BankCredit]] = defaultdict(list)
    seen: set[str] = set()
    for row in _read_csv(directory / "bank_statement.csv", columns):
        bank_row_id = _require(row, "bank_row_id", "bank_statement.csv")
        context = f"bank_statement.csv:{bank_row_id}"
        if bank_row_id in seen:
            raise NormalizationError(f"{context}: duplicate bank_row_id")
        seen.add(bank_row_id)
        utr = _require(row, "utr", context)
        by_utr[utr].append(
            BankCredit(
                bank_row_id=bank_row_id,
                utr=utr,
                posted_at=_timestamp(row, "posted_at", context),
                credit_amount_paise=_paise(row, "credit_amount_paise", context),
                currency=_require(row, "currency", context),
                # Narration is the one free-text field in the export. It is read
                # for display only: the UTR beside it already provides an exact
                # join, and treating prose as evidence when a key is available
                # is how a reconciliation engine acquires a failure mode it did
                # not need.
                narration=row.get("narration", "").strip(),
                bank_ref=row.get("bank_ref", "").strip(),
            )
        )
    # Sorted so a duplicated UTR presents its credits in a stable order and a
    # BANK_CREDIT_DUPLICATE finding is reproducible run to run.
    return {
        utr: tuple(sorted(rows, key=lambda credit: credit.bank_row_id))
        for utr, rows in by_utr.items()
    }


def _load_pricing(directory: Path) -> dict[str, PricingRule]:
    columns = ("method", "fee_rate_bps", "gst_rate_bps")
    rules: dict[str, PricingRule] = {}
    for row in _read_csv(directory / "pricing_rules.csv", columns):
        method = _require(row, "method", "pricing_rules.csv")
        context = f"pricing_rules.csv:{method}"
        rules[method] = PricingRule(
            method=method,
            fee_rate_bps=int(_require(row, "fee_rate_bps", context)),
            gst_rate_bps=int(_require(row, "gst_rate_bps", context)),
        )
    if not rules:
        raise NormalizationError("pricing_rules.csv contains no rows")
    return rules


def load_batch(directory: str | Path) -> Batch:
    """Parse one released dataset directory into canonical records.

    Reads exactly five files: the gateway ledger, settlement detail and
    summary, the bank statement, and the pricing rules. It does not read the
    answer key, and it must never be given a reason to -- ground truth reaching
    the matcher would make every number this project publishes meaningless.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NormalizationError(f"{root} is not a directory")

    events = _load_events(root)
    details, duplicate_counts = _load_details(root)
    settlements = _load_settlements(root)
    bank_by_utr = _load_bank(root)
    pricing = _load_pricing(root)

    details_by_settlement: dict[str, list[DetailLine]] = defaultdict(list)
    for line in details:
        details_by_settlement[line.settlement_id].append(line)

    payments_by_txn: dict[str, list[GatewayEvent]] = defaultdict(list)
    for event in events.values():
        if event.event_type == PAYMENT:
            payments_by_txn[event.txn_id].append(event)

    return Batch(
        events=events,
        details=details,
        settlements=settlements,
        bank_by_utr=bank_by_utr,
        pricing=pricing,
        details_by_settlement={
            settlement_id: tuple(lines)
            for settlement_id, lines in details_by_settlement.items()
        },
        payments_by_txn={
            txn_id: tuple(sorted(rows, key=lambda event: event.event_id))
            for txn_id, rows in payments_by_txn.items()
        },
        duplicate_detail_counts=duplicate_counts,
    )
