"""Configuration for reproducible, prevalence-auditable benchmark batches.

Scenario prevalence belongs to cases rather than exported rows. Keeping the
three family share tables here prevents row multiplication (for example,
duplicate detail exports) from quietly changing the benchmark's class balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Family(str, Enum):
    """The benchmark split whose published case shares are being generated."""

    DEVELOPMENT = "development"
    PRIMARY = "primary"
    STRESS = "stress"


class Scenario(str, Enum):
    """The twelve mutually exclusive reconciliation cases in the benchmark."""

    STRAIGHT_THROUGH = "STRAIGHT_THROUGH"
    REFUND_LATER_CYCLE = "REFUND_LATER_CYCLE"
    CONTESTED_REFUND = "CONTESTED_REFUND"
    DUPLICATE_DETAIL_EXPORT = "DUPLICATE_DETAIL_EXPORT"
    CORROBORATED_REFUND = "CORROBORATED_REFUND"
    CAPTURED_UNSETTLED = "CAPTURED_UNSETTLED"
    NOT_SETTLEABLE = "NOT_SETTLEABLE"
    FEE_TAX_VARIANCE = "FEE_TAX_VARIANCE"
    BANK_CREDIT_MISSING = "BANK_CREDIT_MISSING"
    BANK_CREDIT_DUPLICATE = "BANK_CREDIT_DUPLICATE"
    AMBIGUOUS_REFUND = "AMBIGUOUS_REFUND"
    DESCRIBED_REFUND = "DESCRIBED_REFUND"


class Resolution(str, Enum):
    """The action that is scored independently from the diagnostic label."""

    RECONCILED = "RECONCILED"
    EXCEPTION = "EXCEPTION"
    NO_ACTION = "NO_ACTION"
    ABSTAIN = "ABSTAIN"


# Integer percentages are intentional: largest-remainder apportionment can
# reproduce the published mix without a floating-point sampling path.
#
# PRIMARY is the headline family, so it has to contain every scenario class.
# Nine of the eleven were present before; BANK_CREDIT_MISSING and
# BANK_CREDIT_DUPLICATE were not, which meant an agent with no bank-side
# handling whatsoever scored identically on the one number we intend to
# publish.  A headline family that cannot distinguish those two agents is not
# measuring the thing it claims to measure.
#
# The shares stay deliberately different from the other two families.  PRIMARY
# is the realistic-prevalence mix rather than the enriched one, so its defect
# rates are the lowest of the three, and matching DEVELOPMENT exactly would
# refit the frozen thresholds to the prevalence they are later scored against.
PRIMARY_CASE_SHARES: tuple[tuple[Scenario, int], ...] = (
    (Scenario.STRAIGHT_THROUGH, 37),
    (Scenario.DESCRIBED_REFUND, 6),
    (Scenario.REFUND_LATER_CYCLE, 10),
    (Scenario.CONTESTED_REFUND, 8),
    (Scenario.DUPLICATE_DETAIL_EXPORT, 8),
    (Scenario.CORROBORATED_REFUND, 6),
    (Scenario.CAPTURED_UNSETTLED, 6),
    (Scenario.NOT_SETTLEABLE, 6),
    (Scenario.FEE_TAX_VARIANCE, 4),
    (Scenario.AMBIGUOUS_REFUND, 4),
    (Scenario.BANK_CREDIT_MISSING, 3),
    (Scenario.BANK_CREDIT_DUPLICATE, 2),
)

# The development family exists to freeze thresholds, and it is the only family
# that may be tuned on.  It therefore has to contain EVERY scenario, including
# CONTESTED_REFUND and every exception enriched in the stress mix.
#
# This is not a cosmetic completeness point.  The abstention threshold is frozen
# by choosing the lowest value that produces zero falsely-accepted recovery
# allocations across the development seeds.  If AMBIGUOUS_REFUND were absent,
# no threshold could ever produce a false accept, the rule would select zero,
# the agent would never abstain, and it would false-match every ambiguous case
# it later met.  A tuning set has to contain the phenomenon being tuned for.
#
# CONTESTED_REFUND, CORROBORATED_REFUND, and AMBIGUOUS_REFUND are the recovery
# classes the threshold has to tell apart: amount collisions must be resolved
# by evidence in the first, are already unique in the second, and remain tied
# in the third.  All three therefore have material development prevalence.
#
# These shares deliberately differ from STRESS_CASE_SHARES.  Making them equal
# would fit the frozen threshold to the exact prevalence of the batch it is
# later scored on, which is the overfitting this three-family split exists to
# prevent.  Development prevalence is never reported.
DEVELOPMENT_CASE_SHARES: tuple[tuple[Scenario, int], ...] = (
    (Scenario.STRAIGHT_THROUGH, 16),
    (Scenario.DESCRIBED_REFUND, 10),
    (Scenario.CONTESTED_REFUND, 14),
    (Scenario.CORROBORATED_REFUND, 10),
    (Scenario.REFUND_LATER_CYCLE, 9),
    (Scenario.DUPLICATE_DETAIL_EXPORT, 9),
    (Scenario.AMBIGUOUS_REFUND, 8),
    (Scenario.CAPTURED_UNSETTLED, 6),
    (Scenario.FEE_TAX_VARIANCE, 6),
    (Scenario.NOT_SETTLEABLE, 5),
    (Scenario.BANK_CREDIT_MISSING, 4),
    (Scenario.BANK_CREDIT_DUPLICATE, 3),
)

STRESS_CASE_SHARES: tuple[tuple[Scenario, int], ...] = (
    (Scenario.STRAIGHT_THROUGH, 10),
    (Scenario.DESCRIBED_REFUND, 10),
    (Scenario.CONTESTED_REFUND, 14),
    (Scenario.REFUND_LATER_CYCLE, 10),
    (Scenario.DUPLICATE_DETAIL_EXPORT, 10),
    (Scenario.CORROBORATED_REFUND, 10),
    (Scenario.FEE_TAX_VARIANCE, 8),
    (Scenario.CAPTURED_UNSETTLED, 7),
    (Scenario.BANK_CREDIT_MISSING, 7),
    (Scenario.AMBIGUOUS_REFUND, 6),
    (Scenario.BANK_CREDIT_DUPLICATE, 5),
    (Scenario.NOT_SETTLEABLE, 3),
)

SCENARIO_OUTCOMES: dict[Scenario, Resolution] = {
    Scenario.STRAIGHT_THROUGH: Resolution.RECONCILED,
    Scenario.REFUND_LATER_CYCLE: Resolution.RECONCILED,
    Scenario.CONTESTED_REFUND: Resolution.RECONCILED,
    Scenario.DUPLICATE_DETAIL_EXPORT: Resolution.RECONCILED,
    Scenario.CORROBORATED_REFUND: Resolution.RECONCILED,
    Scenario.CAPTURED_UNSETTLED: Resolution.EXCEPTION,
    Scenario.NOT_SETTLEABLE: Resolution.NO_ACTION,
    Scenario.FEE_TAX_VARIANCE: Resolution.EXCEPTION,
    Scenario.BANK_CREDIT_MISSING: Resolution.EXCEPTION,
    Scenario.BANK_CREDIT_DUPLICATE: Resolution.EXCEPTION,
    Scenario.AMBIGUOUS_REFUND: Resolution.ABSTAIN,
    # Resolvable, but only by reading the operations note against the
    # merchant descriptor. The deterministic ladder abstains here and is
    # scored wrong for it, which is the entire point of the class.
    Scenario.DESCRIBED_REFUND: Resolution.RECONCILED,
}

PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet", "emi"]
METHOD_WEIGHTS = [0.46, 0.28, 0.14, 0.08, 0.04]

# The original rates are retained exactly, expressed once as integer basis
# points so no binary float or second monetary rounding path can enter a fee.
METHOD_FEE_RATE: dict[str, int] = {
    "upi": 0,
    "card": 200,
    "netbanking": 175,
    "wallet": 200,
    "emi": 300,
}
GST_RATE_BPS = 1800

# Real payment amounts are not uniformly distributed -- they pile up on price
# points, because that is how things are priced. This matters more than it
# looks: with continuously-drawn amounts, (amount, date) is very nearly a unique
# key and a trivial matcher scores ~99%, which would make every headline number
# in this project meaningless. Clustering amounts onto price points is what
# creates genuine collisions, and collisions are what force reconciliation to
# use the rest of the evidence rather than treating amount as an identifier.
PRICE_POINTS: tuple[Decimal, ...] = tuple(
    Decimal(v) for v in (
        "49", "99", "149", "199", "249", "299", "349", "399", "449", "499",
        "599", "699", "799", "899", "999", "1199", "1499", "1799", "1999",
        "2499", "2999", "3499", "3999", "4999", "5999", "7999", "9999",
        "12999", "14999", "19999", "24999", "29999", "49999",
    )
)


@dataclass(frozen=True)
class GenConfig:
    """A complete description of a dataset, including its deterministic clock."""

    n_records: int = 500
    seed: int = 42
    family: Family = Family.PRIMARY

    start_date: date = date(2026, 6, 1)
    horizon_days: int = 45
    standard_lag_days: int = 1
    recovery_window_days: int = 4
    skip_weekend_settlement: bool = True

    # This 0.45 is load-bearing: changing it changes the amount-collision floor.
    price_point_share: float = 0.45

    # Amount distribution (INR, lognormal-ish via explicit bands). The float
    # weights select a band only; generated money is drawn as integer paise.
    amount_bands: tuple[tuple[int, int, float], ...] = (
        (50, 500, 0.30),
        (500, 5_000, 0.42),
        (5_000, 50_000, 0.22),
        (50_000, 400_000, 0.06),
    )

    def __post_init__(self) -> None:
        if isinstance(self.family, str):
            object.__setattr__(self, "family", Family(self.family))
        if self.n_records < 3:
            raise ValueError("n_records must be >= 3 because every case has 3-7 events")
        if self.horizon_days < 1:
            raise ValueError("horizon_days must be >= 1")
        if not 0.0 <= self.price_point_share <= 1.0:
            raise ValueError("price_point_share must be in [0, 1]")
        if self.recovery_window_days < 0:
            raise ValueError("recovery_window_days must be non-negative")

    @property
    def case_shares(self) -> tuple[tuple[Scenario, int], ...]:
        if self.family is Family.STRESS:
            return STRESS_CASE_SHARES
        if self.family is Family.DEVELOPMENT:
            return DEVELOPMENT_CASE_SHARES
        return PRIMARY_CASE_SHARES
