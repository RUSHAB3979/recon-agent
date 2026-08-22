"""Record types for the two source systems and for the ground-truth answer key.

Money is Decimal everywhere and is quantised to 2dp at construction.  Floats
are never used for currency: a reconciliation engine that reports a 0.01
tolerance breach caused by its own binary-float error is worse than useless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from .config import AmountBasis, Resolution, Scenario

TWO_PLACES = Decimal("0.01")


def money(value: Decimal | str | int) -> Decimal:
    """Quantise to 2dp with banker's-safe half-up rounding."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class GatewayTxn:
    """A row in the payment gateway ledger (source A)."""

    txn_id: str
    order_id: str
    amount: Decimal          # gross, in `currency`
    currency: str
    status: str              # captured | refunded | partially_refunded | failed | created
    created_at: datetime
    method: str
    fee: Decimal
    tax: Decimal
    net_amount: Decimal      # amount - fee - tax, in `currency`
    refund_amount: Decimal   # deviation from the original spec -- see docs/DATA_SPEC.md

    def to_row(self) -> dict[str, str]:
        return {
            "txn_id": self.txn_id,
            "order_id": self.order_id,
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
            "status": self.status,
            "created_at": self.created_at.isoformat(sep=" ", timespec="seconds"),
            "method": self.method,
            "fee": f"{self.fee:.2f}",
            "tax": f"{self.tax:.2f}",
            "net_amount": f"{self.net_amount:.2f}",
            "refund_amount": f"{self.refund_amount:.2f}",
        }


@dataclass
class BankCredit:
    """A row in the bank settlement statement (source B).

    Note what is NOT here: txn_id, order_id, currency, fee breakdown.  A real
    bank statement carries a UTR, a date, an INR amount and a free-text
    narration -- the narration is the only bridge back to the gateway, which is
    precisely why garbled narrations are the case where an LLM earns its place.
    """

    utr: str
    settlement_date: date
    credit_amount: Decimal   # always INR
    narration: str
    bank_ref: str

    def to_row(self) -> dict[str, str]:
        return {
            "utr": self.utr,
            "settlement_date": self.settlement_date.isoformat(),
            "credit_amount": f"{self.credit_amount:.2f}",
            "narration": self.narration,
            "bank_ref": self.bank_ref,
        }


@dataclass
class TruthLink:
    """One ground-truth relationship between the two sources.

    A link is the unit the agent is scored against.  It may span many gateway
    txns (batch settlement) or many bank credits (duplicate settlement), or
    neither side (a true exception).

    `expected_resolution` is deliberately separate from `scenario`: knowing the
    defect class tells you what was wrong, knowing the resolution tells you what
    a correct agent should have DONE about it.  Scoring uses the latter.
    """

    link_id: str
    scenario: Scenario
    resolution: Resolution
    txn_ids: list[str]
    utrs: list[str]
    amount_basis: AmountBasis | None
    expected_inr_total: Decimal | None   # what the bank should have credited, INR
    actual_inr_total: Decimal | None     # what it did credit (differs for defects)
    notes: str

    def to_row(self) -> dict[str, str]:
        return {
            "link_id": self.link_id,
            "scenario": self.scenario.value,
            "expected_resolution": self.resolution.value,
            "txn_ids": "|".join(self.txn_ids),
            "utrs": "|".join(self.utrs),
            "n_txns": str(len(self.txn_ids)),
            "n_credits": str(len(self.utrs)),
            "amount_basis": self.amount_basis.value if self.amount_basis else "",
            "expected_inr_total": (
                f"{self.expected_inr_total:.2f}" if self.expected_inr_total is not None else ""
            ),
            "actual_inr_total": (
                f"{self.actual_inr_total:.2f}" if self.actual_inr_total is not None else ""
            ),
            "notes": self.notes,
        }


@dataclass
class TruthPair:
    """An atomic (txn_id, utr) correspondence.

    Precision, recall and false-match rate are computed at this granularity
    because it is the only level at which a partially-correct batch match can be
    scored fairly: getting 7 of 8 legs of a batch right should score 7/8, not 0.
    """

    txn_id: str
    utr: str
    link_id: str
    scenario: Scenario

    def to_row(self) -> dict[str, str]:
        return {
            "txn_id": self.txn_id,
            "utr": self.utr,
            "link_id": self.link_id,
            "scenario": self.scenario.value,
        }


@dataclass
class Dataset:
    """Everything one generator run produces."""

    gateway: list[GatewayTxn]
    bank: list[BankCredit]
    links: list[TruthLink]
    pairs: list[TruthPair]
    config_meta: dict[str, object]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for link in self.links:
            counts[link.scenario.value] = counts.get(link.scenario.value, 0) + 1
        return dict(sorted(counts.items()))
