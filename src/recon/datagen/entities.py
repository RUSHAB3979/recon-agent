"""Typed rows make illegal monetary representations difficult to construct.

All emitted money is integer paise. The one shared half-up primitive below is
therefore both an arithmetic helper and an audit boundary: fee and tax code has
no alternative rounding implementation to drift away from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import Family, Resolution, Scenario


def round_half_up(numerator: int, denominator: int) -> int:
    """Return numerator / denominator rounded to nearest, with ties away from zero."""
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        raise TypeError("round_half_up accepts integers only")
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


@dataclass(frozen=True)
class BatchConfigRow:
    batch_id: str
    seed: int
    family: Family
    n_gateway_events: int
    n_cases: int
    generated_at: datetime

    def to_row(self) -> dict[str, str]:
        return {
            "batch_id": self.batch_id,
            "seed": str(self.seed),
            "family": self.family.value,
            "n_gateway_events": str(self.n_gateway_events),
            "n_cases": str(self.n_cases),
            "generated_at": _timestamp(self.generated_at),
        }


@dataclass(frozen=True)
class PricingRule:
    method: str
    fee_rate_bps: int
    gst_rate_bps: int

    def to_row(self) -> dict[str, str]:
        return {
            "method": self.method,
            "fee_rate_bps": str(self.fee_rate_bps),
            "gst_rate_bps": str(self.gst_rate_bps),
        }


@dataclass(frozen=True)
class GatewayEvent:
    event_id: str
    event_type: str
    txn_id: str
    order_id: str
    amount_paise: int
    currency: str
    status: str
    created_at: datetime
    method: str

    def to_row(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "txn_id": self.txn_id,
            "order_id": self.order_id,
            "amount_paise": str(self.amount_paise),
            "currency": self.currency,
            "status": self.status,
            "created_at": _timestamp(self.created_at),
            "method": self.method,
        }


@dataclass(frozen=True)
class SettlementDetail:
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

    def to_row(self) -> dict[str, str]:
        return {
            "detail_id": self.detail_id,
            "settlement_id": self.settlement_id,
            "event_id": self.event_id or "",
            "line_type": self.line_type,
            "gross_effect_paise": str(self.gross_effect_paise),
            "fee_paise": str(self.fee_paise),
            "tax_paise": str(self.tax_paise),
            "net_effect_paise": str(self.net_effect_paise),
            "settled_at": _timestamp(self.settled_at),
            "currency": self.currency,
            "reference_text": self.reference_text or "",
        }


@dataclass(frozen=True)
class SettlementSummary:
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

    def to_row(self) -> dict[str, str]:
        return {
            "settlement_id": self.settlement_id,
            "utr": self.utr,
            "settlement_date": self.settlement_date.isoformat(),
            "gross_payment_paise": str(self.gross_payment_paise),
            "refund_paise": str(self.refund_paise),
            "fee_paise": str(self.fee_paise),
            "tax_paise": str(self.tax_paise),
            "net_amount_paise": str(self.net_amount_paise),
            "line_count": str(self.line_count),
            "currency": self.currency,
            "status": self.status,
        }


@dataclass(frozen=True)
class BankStatementRow:
    bank_row_id: str
    utr: str
    posted_at: datetime
    credit_amount_paise: int
    currency: str
    narration: str
    bank_ref: str

    def to_row(self) -> dict[str, str]:
        return {
            "bank_row_id": self.bank_row_id,
            "utr": self.utr,
            "posted_at": _timestamp(self.posted_at),
            "credit_amount_paise": str(self.credit_amount_paise),
            "currency": self.currency,
            "narration": self.narration,
            "bank_ref": self.bank_ref,
        }


@dataclass(frozen=True)
class AnswerKeyCase:
    case_id: str
    scenario: Scenario
    expected_outcome: Resolution
    settlement_ids: tuple[str, ...]
    bank_row_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    expected_exception_category: str | None
    notes: str

    def to_row(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario.value,
            "expected_outcome": self.expected_outcome.value,
            "settlement_ids": "|".join(self.settlement_ids),
            "bank_row_ids": "|".join(self.bank_row_ids),
            "event_ids": "|".join(self.event_ids),
            "expected_exception_category": self.expected_exception_category or "",
            "notes": self.notes,
        }


@dataclass(frozen=True)
class AnswerKeyAllocation:
    event_id: str
    settlement_id: str
    bank_row_id: str | None

    def to_row(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "settlement_id": self.settlement_id,
            "bank_row_id": self.bank_row_id or "",
        }


@dataclass
class Dataset:
    """All six inputs and both answer-key views from one generator run."""

    batch_config: list[BatchConfigRow]
    pricing_rules: list[PricingRule]
    gateway: list[GatewayEvent]
    details: list[SettlementDetail]
    summaries: list[SettlementSummary]
    bank: list[BankStatementRow]
    cases: list[AnswerKeyCase]
    allocations: list[AnswerKeyAllocation]
    config_meta: dict[str, object]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in self.cases:
            key = case.scenario.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
