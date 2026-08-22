"""Configuration for the synthetic reconciliation dataset generator.

Every knob that affects the shape of the generated data lives here so that a
dataset is fully described by (GenConfig, seed).  Nothing downstream is allowed
to reach for a random number that is not derived from this config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Scenario(str, Enum):
    """The defect classes injected into the dataset.

    This enum IS the test suite: every member is a distinct reconciliation
    capability that the matcher must demonstrate, and every generated record
    is labelled with exactly one of them in the answer key.
    """

    CLEAN_1TO1 = "clean_1to1"
    DATE_OFFSET = "date_offset"
    FEE_DEDUCTION = "fee_deduction"
    ROUNDING = "rounding"
    MANY_TO_ONE = "many_to_one"
    DUPLICATE_SETTLEMENT = "duplicate_settlement"
    MISSING_ON_BANK = "missing_on_bank"
    MISSING_ON_GATEWAY = "missing_on_gateway"
    PARTIAL_REFUND = "partial_refund"
    FX_SETTLEMENT = "fx_settlement"
    GARBLED_NARRATION = "garbled_narration"
    NOT_SETTLEABLE = "not_settleable"


class Resolution(str, Enum):
    """What a correct agent is expected to DO with a link.

    Kept separate from Scenario on purpose: several scenarios are auto-matchable
    (they just require a smarter matcher), while others are genuine exceptions
    that must be surfaced, and one -- NOT_SETTLEABLE -- is a trap where the
    correct behaviour is to stay silent.
    """

    AUTO_MATCH = "auto_match"
    EXCEPTION_UNSETTLED = "exception:unsettled"
    EXCEPTION_UNEXPLAINED_CREDIT = "exception:unexplained_credit"
    EXCEPTION_DUPLICATE = "exception:duplicate_settlement"
    NO_ACTION = "no_action"


class AmountBasis(str, Enum):
    """Which figure the bank actually credited for a settled transaction.

    Real settlement files vary: fees are sometimes netted per transaction and
    sometimes billed monthly, so the matcher cannot assume a single basis.
    The answer key records the true basis so a fee-aware matcher can be scored
    on whether it picked the right one.
    """

    NET = "net"                          # amount - fee - tax
    GROSS = "gross"                      # fees billed separately, full amount credited
    GROSS_MINUS_FEE = "gross_minus_fee"  # fee netted, GST billed separately


# Weights are relative, not percentages; they are normalised at build time.
# CLEAN_1TO1 is handled separately via defect_rate, so it is absent here.
#
# The three true-exception classes are deliberately over-weighted relative to
# their real-world frequency.  The exception list is the graded differentiator,
# and per-category precision computed over 5 instances moves in 20% steps --
# too coarse to report honestly.  These weights put ~15-20 cases in every
# exception bucket at 500 records, which is the point where a per-category
# number starts to mean something.  This is a stated property of the dataset,
# not a claim about production traffic.
#
# Even so, the thinner auto-match classes land around 8-14 cases at 500 records,
# which is fine for a headline match rate but noisy for a per-class breakdown.
# Run `make data-large` (2000 records) when reporting per-class numbers; keep
# the headline metrics at 500, which is the graded throughput bar.
DEFAULT_DEFECT_WEIGHTS: dict[Scenario, float] = {
    Scenario.DATE_OFFSET: 4.0,
    Scenario.FEE_DEDUCTION: 3.5,
    Scenario.ROUNDING: 2.5,
    Scenario.MANY_TO_ONE: 3.5,
    Scenario.DUPLICATE_SETTLEMENT: 4.0,
    Scenario.MISSING_ON_BANK: 6.0,
    Scenario.MISSING_ON_GATEWAY: 5.0,
    Scenario.PARTIAL_REFUND: 2.5,
    Scenario.FX_SETTLEMENT: 4.0,
    Scenario.GARBLED_NARRATION: 4.5,
    Scenario.NOT_SETTLEABLE: 2.5,
}

PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet", "emi"]
METHOD_WEIGHTS = [0.46, 0.28, 0.14, 0.08, 0.04]

# Per-method fee rates (fraction of gross).  Roughly modelled on published
# Indian PA pricing; exact values do not matter, consistency does.
METHOD_FEE_RATE: dict[str, Decimal] = {
    "upi": Decimal("0.0000"),
    "card": Decimal("0.0200"),
    "netbanking": Decimal("0.0175"),
    "wallet": Decimal("0.0200"),
    "emi": Decimal("0.0300"),
}

GST_RATE = Decimal("0.18")

# Real payment amounts are not uniformly distributed -- they pile up on price
# points, because that is how things are priced.  This matters more than it
# looks: with continuously-drawn amounts, (amount, date) is very nearly a unique
# key and a trivial matcher scores ~99%, which would make every headline number
# in this project meaningless.  Clustering amounts onto price points is what
# creates genuine collisions, and collisions are what force the matcher to use
# narration evidence -- i.e. what the LLM adjudication path exists for.
PRICE_POINTS: tuple[Decimal, ...] = tuple(
    Decimal(v) for v in (
        "49", "99", "149", "199", "249", "299", "349", "399", "449", "499",
        "599", "699", "799", "899", "999", "1199", "1499", "1799", "1999",
        "2499", "2999", "3499", "3999", "4999", "5999", "7999", "9999",
        "12999", "14999", "19999", "24999", "29999", "49999",
    )
)

# Non-INR transactions settle to INR at a rate with a spread applied.
FX_RATES: dict[str, Decimal] = {
    "USD": Decimal("87.40"),
    "EUR": Decimal("94.15"),
    "GBP": Decimal("110.80"),
    "AED": Decimal("23.80"),
}
FX_SPREAD = Decimal("0.0125")  # 1.25% markup retained by the PA


@dataclass(frozen=True)
class GenConfig:
    """A complete, reproducible description of one synthetic dataset."""

    n_records: int = 500
    seed: int = 42
    defect_rate: float = 0.30

    start_date: date = date(2026, 6, 1)
    horizon_days: int = 45

    # Settlement timing
    standard_lag_days: int = 1          # T+1 is the happy path
    offset_lag_choices: tuple[int, ...] = (0, 2, 3, 4)
    skip_weekend_settlement: bool = True

    # Fraction of INR tickets that snap to a catalogue price point rather than
    # being drawn from the continuous bands below.  Tune this and the intrinsic
    # ambiguity floor moves with it; `make ambiguity` reports the resulting rate.
    price_point_share: float = 0.45

    # Amount distribution (INR, lognormal-ish via explicit bands)
    amount_bands: tuple[tuple[int, int, float], ...] = (
        (50, 500, 0.30),
        (500, 5_000, 0.42),
        (5_000, 50_000, 0.22),
        (50_000, 400_000, 0.06),
    )

    many_to_one_batch_size: tuple[int, int] = (2, 8)
    rounding_paise: tuple[int, int] = (1, 5)
    partial_refund_fraction: tuple[float, float] = (0.10, 0.60)

    defect_weights: dict[Scenario, float] = field(
        default_factory=lambda: dict(DEFAULT_DEFECT_WEIGHTS)
    )

    def __post_init__(self) -> None:
        if self.n_records < 1:
            raise ValueError("n_records must be >= 1")
        if not 0.0 <= self.defect_rate <= 1.0:
            raise ValueError("defect_rate must be in [0, 1]")
        if not 0.0 <= self.price_point_share <= 1.0:
            raise ValueError("price_point_share must be in [0, 1]")
        if not self.defect_weights:
            raise ValueError("defect_weights must not be empty")
        if any(w < 0 for w in self.defect_weights.values()):
            raise ValueError("defect weights must be non-negative")
        lo, hi = self.many_to_one_batch_size
        if lo < 2 or hi < lo:
            raise ValueError("many_to_one_batch_size must be (lo>=2, hi>=lo)")
