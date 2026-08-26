"""The pass ladder: ordered, named rules, each declaring what it is allowed to do.

Three production reconciliation engines -- BlackLine Transaction Matching,
Oracle Account Reconciliation, and Modern Treasury -- converge on the same
shape, and this module adopts their vocabulary rather than inventing one.

    ORDERED AND NAMED
        Oracle recommends running precise exact rules before permissive ones,
        and Modern Treasury states that rules are evaluated sequentially and
        must be ordered deliberately. Order is therefore a property of the
        ladder, not an accident of the code, and every claim records which pass
        produced it. That is what makes a per-pass yield table possible: the
        report can say which rule earned its keep, and a rule that resolves
        nothing on real data is a rule to delete.

    DECLARED CARDINALITY
        Oracle exposes five rule types by cardinality. Declaring it turns an
        assumption into a checkable property: a pass declared ONE_TO_ONE that
        emits two claims for the same line is a bug the runner catches, rather
        than a double allocation discovered later by the scorer.

    TYPED TOLERANCE
        Oracle supports fixed amounts, percentages, and percentages capped at a
        maximum. A single scalar tolerance is the amateur version -- it either
        admits too much on large amounts or too little on small ones. The
        trichotomy is the standard, so it is what this models.

    THREE TIERS
        CONFIRMED, SUGGESTED, ABSTAIN. Oracle ships Suggested and Confirmed
        rule types and an opt-in No Ambiguous flag; BlackLine separates
        automatic from suggested rules. The difference here is that abstention
        is the default rather than a configuration option, and it is measured.

THE ONE PLACE THIS DEPARTS FROM THE INDUSTRY MODEL, AND WHY

    Simple passes are pure declarations: field, operator, value. The hard
    passes -- refund corroboration and control-equation reconstruction -- are
    code behind the same interface.

    This is not a shortcut. Every engine named above has the same escape hatch:
    BlackLine has suggested-match rules, Oracle has Many to Many. A generic
    condition language covers the easy eighty percent and then acquires an
    imperative extension, because the remaining twenty percent is where the
    domain lives. Modelling that honestly beats forcing a nine-gate
    corroboration argument through a condition DSL and making it worse code in
    exchange for looking uniform.

WHAT A PASS MAY NOT DO

    A pass may not mutate the batch, and it may not consume a candidate
    directly. It returns claims; the runner decides which survive and records
    the consumption. If passes consumed candidates themselves, the result would
    depend on the order in which each pass happened to iterate, and two passes
    could both believe they own the same refund event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence

from recon.match.normalize import Batch

__all__ = [
    "JOIN_ONLY_LADDER",
    "Cardinality",
    "Claim",
    "ClaimTier",
    "Pass",
    "PassResult",
    "Tolerance",
    "ToleranceKind",
    "ExactJoinPass",
    "DEFAULT_LADDER",
]


class Cardinality(str, Enum):
    """How many records on each side one claim from this pass may relate."""

    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:M"


class ClaimTier(str, Enum):
    """How much the ladder is willing to assert about a claim.

    CONFIRMED   evidence is sufficient to allocate without review
    SUGGESTED   a preference is defensible but a human should confirm it
    ABSTAIN     the evidence does not separate the candidates, and guessing
                would trade a measured non-answer for an unmeasured wrong one
    """

    CONFIRMED = "CONFIRMED"
    SUGGESTED = "SUGGESTED"
    ABSTAIN = "ABSTAIN"


class ToleranceKind(str, Enum):
    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"
    PERCENTAGE_CAPPED = "PERCENTAGE_CAPPED"


@dataclass(frozen=True, slots=True)
class Tolerance:
    """A declared allowance, evaluated entirely in integer paise.

    ``percentage_bps`` is basis points so the percentage arithmetic stays
    integral: a float percentage would reintroduce the representation error
    that integer paise exists to remove, and it would do so exactly at the
    boundary where a match is accepted or refused.
    """

    kind: ToleranceKind
    fixed_paise: int = 0
    percentage_bps: int = 0
    cap_paise: int | None = None

    def __post_init__(self) -> None:
        if self.fixed_paise < 0 or self.percentage_bps < 0:
            raise ValueError("tolerance components must be non-negative")
        if self.kind is ToleranceKind.PERCENTAGE_CAPPED and self.cap_paise is None:
            raise ValueError("PERCENTAGE_CAPPED requires cap_paise")
        if self.cap_paise is not None and self.cap_paise < 0:
            raise ValueError("cap_paise must be non-negative")

    def allowance_paise(self, reference_paise: int) -> int:
        """The largest absolute difference this tolerance admits."""
        magnitude = abs(reference_paise)
        if self.kind is ToleranceKind.FIXED:
            return self.fixed_paise
        # Integer division truncates toward zero, which makes the allowance
        # slightly tighter than the nominal percentage rather than looser.
        # Erring tight is the correct direction: a wrong match costs more than
        # a missed one, and the missed one is still reported.
        scaled = magnitude * self.percentage_bps // 10_000
        if self.kind is ToleranceKind.PERCENTAGE:
            return scaled
        assert self.cap_paise is not None
        return min(scaled, self.cap_paise)

    def admits(self, expected_paise: int, actual_paise: int) -> bool:
        return abs(expected_paise - actual_paise) <= self.allowance_paise(expected_paise)


EXACT = Tolerance(ToleranceKind.FIXED, fixed_paise=0)


@dataclass(frozen=True, slots=True)
class Claim:
    """One attribution a pass asserts, with the evidence that produced it.

    ``reasons`` is not decoration. Every decision in this system has to be
    explainable to an operator and overridable by one, so a claim that cannot
    say why it exists is not admissible evidence -- it is an opinion.
    """

    settlement_id: str
    event_id: str
    detail_id: str | None
    pass_name: str
    tier: ClaimTier
    confidence: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
        if self.tier is ClaimTier.ABSTAIN:
            raise ValueError(
                "an ABSTAIN outcome is the absence of a claim, not a claim with a tier; "
                "return no claim and record the reason on the pass result instead"
            )

    @property
    def allocation(self) -> tuple[str, str]:
        """The atomic pair the scorer checks: (settlement_id, event_id)."""
        return (self.settlement_id, self.event_id)


@dataclass(frozen=True, slots=True)
class Abstention:
    """A line the pass examined, could have claimed, and deliberately did not."""

    settlement_id: str
    detail_id: str
    pass_name: str
    candidate_event_ids: tuple[str, ...]
    reason: str


@dataclass
class PassResult:
    """Everything one pass produced, including what it refused to produce.

    Abstentions are returned rather than dropped because the count of declined
    lines is a headline metric. A ladder that reported only its successes would
    make the abstention rate unobservable, and abstention is the behaviour this
    whole design is built to make safe.
    """

    pass_name: str
    claims: list[Claim] = field(default_factory=list)
    abstentions: list[Abstention] = field(default_factory=list)
    examined: int = 0

    # Free-form diagnostic counters the pass wants published -- per-gate
    # elimination counts, candidate multiplicities, and so on. They travel in
    # the result rather than on the pass instance so a consumer never has to
    # reach into a rung's private state to report what it did, and so a rung
    # that is reused across two runs cannot leak the first run's numbers into
    # the second.
    counters: dict[str, int] = field(default_factory=dict)


class Pass(Protocol):
    """The uniform interface every rung of the ladder implements."""

    name: str
    cardinality: Cardinality
    tolerance: Tolerance | None

    def run(self, batch: Batch, consumed: frozenset[str]) -> PassResult:
        """Emit claims for lines this pass can attribute.

        ``consumed`` names events already claimed by an earlier pass. A pass
        must treat them as unavailable; the runner enforces this, but a pass
        that ignores it wastes work producing claims that will be rejected.
        """
        ...


# ------------------------------------------------------------- rung one


class ExactJoinPass:
    """Attribute every detail line that already names its event.

    This is the most precise rung and therefore runs first, exactly as Oracle
    recommends: a line carrying an ``event_id`` is not an inference at all, it
    is a fact the export states outright. Confidence is 1.0 because there is
    nothing probabilistic about reading a foreign key.

    It looks trivial, and it is -- but it is load-bearing for honesty rather
    than for score. The published floor claims these same pairs, so the agent
    must claim them too. A matcher that asserted only the handful of pairs it
    had to infer would post near-perfect allocation precision by volunteering
    almost nothing, which flatters the agent in precisely the direction that
    makes the comparison against the floor meaningless.
    """

    name = "exact_join"
    cardinality = Cardinality.ONE_TO_ONE
    tolerance: Tolerance | None = None

    def run(self, batch: Batch, consumed: frozenset[str]) -> PassResult:
        result = PassResult(self.name)
        for line in batch.details:
            if line.is_anonymous:
                continue
            result.examined += 1
            event_id = line.event_id
            assert event_id is not None
            if event_id in consumed:
                # An earlier rung already owns this event. Two lines naming the
                # same event is a duplicate-export symptom, not a second
                # attribution, and the control stage reports it as such.
                continue
            if event_id not in batch.events:
                # The export names an event the ledger does not contain. That is
                # a data-integrity break, not an attribution, so no claim is
                # made and the control stage reports it.
                continue
            result.claims.append(
                Claim(
                    settlement_id=line.settlement_id,
                    event_id=event_id,
                    detail_id=line.detail_id,
                    pass_name=self.name,
                    tier=ClaimTier.CONFIRMED,
                    confidence=1.0,
                    reasons=(
                        f"settlement_detail line {line.detail_id} names event {event_id}",
                    ),
                )
            )
        return result


# The ladder, in evaluation order. Specific before broad, exact before
# tolerant. Later rungs are added by subsequent phases; the order is the
# contract, so a new rung is inserted deliberately rather than appended.
# Ordered, and the order is the argument. Each rung may only see what earlier
# rungs left behind, so the cheapest and most certain evidence is spent first
# and every later rung inherits a smaller, harder residual. Reversing this would
# let a rung that reasons under uncertainty consume a record that a foreign key
# would have settled outright.
#
# Imported here rather than at module top because the recovery rung imports the
# vocabulary defined above; the alternative is a circular import.
def _default_ladder() -> tuple[Pass, ...]:
    from recon.match.recovery import RefundCorroborationPass

    return (ExactJoinPass(), RefundCorroborationPass())


JOIN_ONLY_LADDER: tuple[Pass, ...] = (ExactJoinPass(),)
DEFAULT_LADDER: tuple[Pass, ...] = _default_ladder()


def ladder_names(ladder: Sequence[Pass] = DEFAULT_LADDER) -> tuple[str, ...]:
    return tuple(rung.name for rung in ladder)
