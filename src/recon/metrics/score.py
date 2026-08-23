"""Score agents and published baselines against the current answer key.

There is deliberately one scoring core for both consumers.  A baseline floor
and an agent score produced by different implementations cannot support a
capability claim: scorer drift would be an alternative explanation for the
gap.  This is different from ``round_half_up`` appearing independently in the
generator and baseline, where the second implementation is a check on the
generator's arithmetic rather than a measurement shared by two competitors.

Only identifiers, scenarios, outcomes, and diagnostic categories cross this
module's CSV boundary.  Monetary columns are neither requested nor parsed, so
there is no route by which integer paise could accidentally become a binary
float in a published metric.  Floats are confined to unit-interval confidence
values and ratios derived from integer counts.

The specification says non-exception cases have no exception category, while
its scenario table and generated data attach ``*_WARNING`` diagnostics to
reconciled cases and a scenario-named diagnostic to an abstention case.  Those
two self-describing forms are retained as key metadata so the real benchmark
remains loadable, but they are not exception-category targets.  Other
categories on non-exception outcomes still fail validation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from recon.datagen.config import Resolution


OUTCOMES: tuple[str, ...] = tuple(resolution.value for resolution in Resolution)
_OUTCOME_SET = frozenset(OUTCOMES)
_RECONCILED = Resolution.RECONCILED.value
_EXCEPTION = Resolution.EXCEPTION.value
_NO_ACTION = Resolution.NO_ACTION.value
_ABSTAIN = Resolution.ABSTAIN.value

Allocation = tuple[str, str]


def _clean_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or None")
    cleaned = value.strip()
    return cleaned or None


def _confidence(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("confidence must be a finite number between 0 and 1")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be a finite number between 0 and 1") from exc
    if not isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be a finite number between 0 and 1")
    return result


def _normalise_allocation(value: object) -> Allocation:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("allocations must contain (event_id, settlement_id) tuples")
    return (
        _clean_identifier(value[0], "event_id"),
        _clean_identifier(value[1], "settlement_id"),
    )


@dataclass(frozen=True, slots=True)
class CaseDecision:
    """A case-level claim that cannot hide an uncheckable attribution.

    Allocations live beside the outcome because a correct-looking
    ``RECONCILED`` label is not sufficient evidence: naming the wrong refund
    event is a false match and must fail the case as well as the atomic metric.
    """

    case_id: str
    outcome: str
    category: str | None = None
    allocations: frozenset[Allocation] = field(default_factory=frozenset)
    confidence: float = 1.0
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _clean_identifier(self.case_id, "case_id"))
        outcome = _clean_identifier(self.outcome, "outcome")
        if outcome not in _OUTCOME_SET:
            raise ValueError(f"unknown outcome: {outcome!r}")
        object.__setattr__(self, "outcome", outcome)

        category = _optional_text(self.category, "category")
        if outcome == _EXCEPTION and category is None:
            raise ValueError("category is required when outcome is EXCEPTION")
        if outcome != _EXCEPTION and category is not None:
            raise ValueError("category is allowed only when outcome is EXCEPTION")
        object.__setattr__(self, "category", category)

        try:
            allocations = frozenset(
                _normalise_allocation(value) for value in self.allocations
            )
        except TypeError as exc:
            raise ValueError("allocations must be an iterable of allocation tuples") from exc
        object.__setattr__(self, "allocations", allocations)
        object.__setattr__(self, "confidence", _confidence(self.confidence))

        try:
            reasons = tuple(self.reasons)
        except TypeError as exc:
            raise ValueError("reasons must be an iterable of strings") from exc
        if not all(isinstance(reason, str) for reason in reasons):
            raise ValueError("reasons must contain only strings")
        object.__setattr__(self, "reasons", reasons)


@dataclass(frozen=True, slots=True, init=False)
class AgentOutput:
    """The complete set of decisions emitted by one scorer consumer.

    Duplicate case decisions fail at the boundary because choosing one by
    ordering would make the same logical output score differently after a
    harmless serialization change.  Unknown case identifiers are checked by
    :func:`score`, the first point where the answer key is available.
    """

    decisions: tuple[CaseDecision, ...]

    def __init__(self, decisions: Iterable[CaseDecision] = ()) -> None:
        values = tuple(decisions)
        if not all(isinstance(value, CaseDecision) for value in values):
            raise TypeError("decisions must contain only CaseDecision objects")
        case_ids = [value.case_id for value in values]
        duplicates = sorted(
            case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
        )
        if duplicates:
            raise ValueError(f"case decided more than once: {duplicates[0]!r}")
        object.__setattr__(self, "decisions", values)


@dataclass(frozen=True, slots=True)
class AnswerKeyCase:
    """Scoring columns for one case, with presentation-only notes discarded."""

    case_id: str
    scenario: str
    expected_outcome: str
    settlement_ids: frozenset[str] = field(default_factory=frozenset)
    bank_row_ids: frozenset[str] = field(default_factory=frozenset)
    event_ids: frozenset[str] = field(default_factory=frozenset)
    expected_exception_category: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _clean_identifier(self.case_id, "case_id"))
        object.__setattr__(self, "scenario", _clean_identifier(self.scenario, "scenario"))
        outcome = _clean_identifier(self.expected_outcome, "expected_outcome")
        if outcome not in _OUTCOME_SET:
            raise ValueError(f"unknown expected_outcome: {outcome!r}")
        object.__setattr__(self, "expected_outcome", outcome)

        for name in ("settlement_ids", "bank_row_ids", "event_ids"):
            try:
                values = frozenset(
                    _clean_identifier(value, name[:-1]) for value in getattr(self, name)
                )
            except TypeError as exc:
                raise ValueError(f"{name} must be an iterable of identifiers") from exc
            object.__setattr__(self, name, values)

        category = _optional_text(
            self.expected_exception_category, "expected_exception_category"
        )
        if outcome == _EXCEPTION and category is None:
            raise ValueError("EXCEPTION case has an empty expected_exception_category")
        is_reconciled_warning = (
            outcome == _RECONCILED
            and category is not None
            and category.endswith("_WARNING")
        )
        is_abstention_diagnostic = (
            outcome == _ABSTAIN
            and category is not None
            and category == self.scenario
        )
        if (
            outcome != _EXCEPTION
            and category is not None
            and not is_reconciled_warning
            and not is_abstention_diagnostic
        ):
            raise ValueError(
                "non-EXCEPTION case has an expected_exception_category"
            )
        object.__setattr__(self, "expected_exception_category", category)


@dataclass(frozen=True, slots=True)
class AnswerKeyAllocation:
    """One checkable event-to-settlement fact from the atomic key view."""

    event_id: str
    settlement_id: str
    bank_row_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _clean_identifier(self.event_id, "event_id"))
        object.__setattr__(
            self,
            "settlement_id",
            _clean_identifier(self.settlement_id, "settlement_id"),
        )
        object.__setattr__(
            self, "bank_row_id", _optional_text(self.bank_row_id, "bank_row_id")
        )

    @property
    def pair(self) -> Allocation:
        return self.event_id, self.settlement_id


@dataclass(frozen=True, slots=True, init=False)
class AnswerKey:
    """The case and atomic answer-key views validated as one unit.

    Cross-view validation happens before scoring so a stale or malformed key
    cannot quietly turn a schema mismatch into plausible-looking zeroes.
    """

    cases: tuple[AnswerKeyCase, ...]
    allocations: tuple[AnswerKeyAllocation, ...]

    def __init__(
        self,
        cases: Iterable[AnswerKeyCase],
        allocations: Iterable[AnswerKeyAllocation],
    ) -> None:
        case_values = tuple(cases)
        allocation_values = tuple(allocations)
        if not all(isinstance(value, AnswerKeyCase) for value in case_values):
            raise TypeError("cases must contain only AnswerKeyCase objects")
        if not all(
            isinstance(value, AnswerKeyAllocation) for value in allocation_values
        ):
            raise TypeError(
                "allocations must contain only AnswerKeyAllocation objects"
            )
        object.__setattr__(self, "cases", case_values)
        object.__setattr__(self, "allocations", allocation_values)
        self._validate()

    def _validate(self) -> None:
        case_ids = [case.case_id for case in self.cases]
        duplicates = sorted(
            case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
        )
        if duplicates:
            raise ValueError(f"duplicate case_id: {duplicates[0]!r}")

        known_settlements = {
            settlement_id
            for case in self.cases
            for settlement_id in case.settlement_ids
        }
        for allocation in self.allocations:
            if allocation.settlement_id not in known_settlements:
                raise ValueError(
                    "allocation cites a settlement_id that appears in no case: "
                    f"{allocation.settlement_id!r}"
                )

    @classmethod
    def load(cls, directory: str | Path) -> AnswerKey:
        """Load both views together so neither can be accidentally omitted."""

        root = Path(directory)
        case_path = root / "answer_key_cases.csv"
        allocation_path = root / "answer_key_allocations.csv"
        case_rows = _read_csv(
            case_path,
            (
                "case_id",
                "scenario",
                "expected_outcome",
                "settlement_ids",
                "bank_row_ids",
                "event_ids",
                "expected_exception_category",
            ),
        )
        allocation_rows = _read_csv(
            allocation_path, ("event_id", "settlement_id", "bank_row_id")
        )
        cases = (
            AnswerKeyCase(
                case_id=row["case_id"],
                scenario=row["scenario"],
                expected_outcome=row["expected_outcome"],
                settlement_ids=_split_identifiers(row["settlement_ids"]),
                bank_row_ids=_split_identifiers(row["bank_row_ids"]),
                event_ids=_split_identifiers(row["event_ids"]),
                expected_exception_category=row["expected_exception_category"],
            )
            for row in case_rows
        )
        allocations = (
            AnswerKeyAllocation(
                event_id=row["event_id"],
                settlement_id=row["settlement_id"],
                bank_row_id=row["bank_row_id"],
            )
            for row in allocation_rows
        )
        return cls(cases, allocations)

    @property
    def case_ids(self) -> frozenset[str]:
        return frozenset(case.case_id for case in self.cases)

    @property
    def allocation_pairs(self) -> frozenset[Allocation]:
        return frozenset(allocation.pair for allocation in self.allocations)

    def allocations_for(self, case_id: str) -> frozenset[Allocation]:
        """Expose case truth for auditable baseline adapters and test replays."""

        case = next((case for case in self.cases if case.case_id == case_id), None)
        if case is None:
            raise KeyError(case_id)
        return frozenset(
            allocation.pair
            for allocation in self.allocations
            if allocation.settlement_id in case.settlement_ids
        )


def _read_csv(path: Path, required_columns: Sequence[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            raise ValueError(f"{path!s} is missing required column {missing[0]!r}")
        return [
            {column: row.get(column, "") or "" for column in required_columns}
            for row in reader
        ]


def _split_identifiers(value: str) -> frozenset[str]:
    if not value.strip():
        return frozenset()
    return frozenset(
        _clean_identifier(part, "identifier")
        for part in value.split("|")
        if part.strip()
    )


@dataclass(frozen=True, slots=True)
class AllocationMetrics:
    """Integer accounting is kept beside ratios so every value is hand-checkable."""

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    """One-vs-rest counts prevent small-category ratios hiding their support."""

    true_positives: int
    false_positives: int
    false_negatives: int
    support: int
    predicted: int
    precision: float
    recall: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "support": self.support,
            "predicted": self.predicted,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """A deterministic report whose ratios retain their integer denominators."""

    total_cases: int
    correct_cases: int
    outcome_accuracy: float
    false_match_rate: float
    allocations: AllocationMetrics
    per_scenario: dict[str, tuple[int, int]]
    exception_categories: dict[str, CategoryMetrics]
    exception_confusion: dict[str, dict[str, int]]
    abstention_count: int
    correct_refusals: int
    missed_resolutions: int
    no_action_false_positives: int
    no_action_support: int
    no_action_false_positive_rate: float

    @property
    def allocation_precision(self) -> float:
        return self.allocations.precision

    @property
    def allocation_recall(self) -> float:
        return self.allocations.recall

    @property
    def allocation_f1(self) -> float:
        return self.allocations.f1

    @property
    def true_positives(self) -> int:
        return self.allocations.true_positives

    @property
    def false_positives(self) -> int:
        return self.allocations.false_positives

    @property
    def false_negatives(self) -> int:
        return self.allocations.false_negatives

    @property
    def precision(self) -> float:
        return self.allocations.precision

    @property
    def recall(self) -> float:
        return self.allocations.recall

    @property
    def f1(self) -> float:
        return self.allocations.f1

    @property
    def per_category_exception_metrics(self) -> dict[str, CategoryMetrics]:
        return self.exception_categories

    @property
    def not_settleable_false_positive_rate(self) -> float:
        return self.no_action_false_positive_rate

    def to_dict(self) -> dict[str, object]:
        """Return only JSON primitives in a fixed, human-auditable order."""

        return {
            "outcome_accuracy": self.outcome_accuracy,
            "false_match_rate": self.false_match_rate,
            "correct_cases": self.correct_cases,
            "total_cases": self.total_cases,
            "allocations": self.allocations.to_dict(),
            "per_scenario": {
                scenario: {"correct": counts[0], "total": counts[1]}
                for scenario, counts in sorted(self.per_scenario.items())
            },
            "exception_categories": {
                category: metrics.to_dict()
                for category, metrics in sorted(self.exception_categories.items())
            },
            "exception_confusion": {
                expected: {
                    predicted: count
                    for predicted, count in sorted(predictions.items())
                }
                for expected, predictions in sorted(self.exception_confusion.items())
            },
            "abstention": {
                "count": self.abstention_count,
                "correct_refusals": self.correct_refusals,
                "missed_resolutions": self.missed_resolutions,
            },
            "no_action": {
                "false_positives": self.no_action_false_positives,
                "support": self.no_action_support,
                "false_positive_rate": self.no_action_false_positive_rate,
            },
        }

    def format_text(self) -> str:
        """Put the two decision-critical rates before every diagnostic table."""

        asserted = self.allocations.true_positives + self.allocations.false_positives
        lines = [
            "Reconciliation score",
            (
                f"  Outcome accuracy    {self.correct_cases:>4}/{self.total_cases:<4} "
                f"({self.outcome_accuracy:.4f})"
            ),
            (
                f"  False-match rate    {self.allocations.false_positives:>4}/"
                f"{asserted:<4} ({self.false_match_rate:.4f})"
            ),
            "",
            "Allocation metrics",
            (
                f"  TP={self.allocations.true_positives} "
                f"FP={self.allocations.false_positives} "
                f"FN={self.allocations.false_negatives}"
            ),
            (
                f"  precision={self.allocations.precision:.4f} "
                f"recall={self.allocations.recall:.4f} "
                f"f1={self.allocations.f1:.4f}"
            ),
            "",
            "Per scenario (correct / total)",
        ]
        scenario_width = max((len(name) for name in self.per_scenario), default=8)
        lines.extend(
            f"  {scenario:<{scenario_width}}  {correct:>4}/{total}"
            for scenario, (correct, total) in sorted(self.per_scenario.items())
        )
        lines.extend(("", "Exception categories"))
        if self.exception_categories:
            category_width = max(len(name) for name in self.exception_categories)
            lines.extend(
                (
                    f"  {category:<{category_width}}  "
                    f"precision={metrics.precision:.4f} "
                    f"recall={metrics.recall:.4f} support={metrics.support}"
                )
                for category, metrics in sorted(self.exception_categories.items())
            )
        else:
            lines.append("  (none)")

        lines.extend(("", "Exception confusion (expected -> reported)"))
        if self.exception_confusion:
            for expected, predictions in sorted(self.exception_confusion.items()):
                cells = ", ".join(
                    f"{predicted}={count}"
                    for predicted, count in sorted(predictions.items())
                )
                lines.append(f"  {expected}: {cells}")
        else:
            lines.append("  (none)")

        lines.extend(
            (
                "",
                "Abstention accounting",
                f"  abstentions={self.abstention_count}",
                f"  correct_refusals={self.correct_refusals}",
                f"  missed_resolutions={self.missed_resolutions}",
                "",
                "NO_ACTION false positives",
                (
                    f"  exceptions_on_no_action={self.no_action_false_positives}/"
                    f"{self.no_action_support} "
                    f"({self.no_action_false_positive_rate:.4f})"
                ),
            )
        )
        return "\n".join(lines)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _decisions_by_case(
    agent_output: AgentOutput, key: AnswerKey
) -> dict[str, CaseDecision]:
    decisions = {decision.case_id: decision for decision in agent_output.decisions}
    unknown = sorted(set(decisions) - key.case_ids)
    if unknown:
        raise ValueError(f"decision cites unknown case_id: {unknown[0]!r}")
    return decisions


def _predicted_outcome(
    case_id: str, decisions: Mapping[str, CaseDecision]
) -> str:
    decision = decisions.get(case_id)
    return decision.outcome if decision is not None else _ABSTAIN


def _predicted_allocations(
    case_id: str, decisions: Mapping[str, CaseDecision]
) -> frozenset[Allocation]:
    decision = decisions.get(case_id)
    return decision.allocations if decision is not None else frozenset()


def _case_is_correct(
    case: AnswerKeyCase,
    decisions: Mapping[str, CaseDecision],
    expected_allocations: frozenset[Allocation],
) -> bool:
    return (
        _predicted_outcome(case.case_id, decisions) == case.expected_outcome
        and _predicted_allocations(case.case_id, decisions) == expected_allocations
    )


def score(agent_output: AgentOutput, key: AnswerKey) -> ScoreReport:
    """Score either consumer through the benchmark's single measurement path."""

    if not isinstance(agent_output, AgentOutput):
        raise TypeError("agent_output must be an AgentOutput")
    if not isinstance(key, AnswerKey):
        raise TypeError("key must be an AnswerKey")
    decisions = _decisions_by_case(agent_output, key)

    proposed_allocations = frozenset(
        allocation
        for decision in agent_output.decisions
        for allocation in decision.allocations
    )
    expected_allocations = key.allocation_pairs
    allocation_tp = len(proposed_allocations & expected_allocations)
    allocation_fp = len(proposed_allocations - expected_allocations)
    allocation_fn = len(expected_allocations - proposed_allocations)
    asserted = allocation_tp + allocation_fp
    allocation_metrics = AllocationMetrics(
        true_positives=allocation_tp,
        false_positives=allocation_fp,
        false_negatives=allocation_fn,
        precision=_ratio(allocation_tp, asserted),
        recall=_ratio(allocation_tp, allocation_tp + allocation_fn),
        f1=_ratio(2 * allocation_tp, 2 * allocation_tp + allocation_fp + allocation_fn),
    )

    correct_cases = 0
    scenario_counts: dict[str, list[int]] = {}
    for case in key.cases:
        correct = _case_is_correct(case, decisions, key.allocations_for(case.case_id))
        correct_cases += int(correct)
        counts = scenario_counts.setdefault(case.scenario, [0, 0])
        counts[0] += int(correct)
        counts[1] += 1
    per_scenario = {
        scenario: (counts[0], counts[1])
        for scenario, counts in sorted(scenario_counts.items())
    }

    expected_category_by_case = {
        case.case_id: case.expected_exception_category
        for case in key.cases
        if case.expected_outcome == _EXCEPTION
    }
    predicted_category_by_case = {
        case.case_id: (
            decisions[case.case_id].category
            if case.case_id in decisions
            and decisions[case.case_id].outcome == _EXCEPTION
            else None
        )
        for case in key.cases
    }
    categories = sorted(
        {
            category
            for category in expected_category_by_case.values()
            if category is not None
        }
        | {
            category
            for category in predicted_category_by_case.values()
            if category is not None
        }
    )
    category_metrics: dict[str, CategoryMetrics] = {}
    all_case_ids = {case.case_id for case in key.cases}
    for category in categories:
        expected_ids = {
            case_id
            for case_id, expected in expected_category_by_case.items()
            if expected == category
        }
        predicted_ids = {
            case_id
            for case_id, predicted in predicted_category_by_case.items()
            if predicted == category
        }
        true_positives = len(expected_ids & predicted_ids)
        false_positives = len((predicted_ids & all_case_ids) - expected_ids)
        false_negatives = len(expected_ids - predicted_ids)
        category_metrics[category] = CategoryMetrics(
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            support=len(expected_ids),
            predicted=len(predicted_ids),
            precision=_ratio(true_positives, len(predicted_ids)),
            recall=_ratio(true_positives, len(expected_ids)),
        )

    confusion: dict[str, dict[str, int]] = {}
    for case in key.cases:
        if case.expected_outcome != _EXCEPTION:
            continue
        expected = case.expected_exception_category
        if expected is None:  # Construction validation makes this unreachable.
            continue
        decision = decisions.get(case.case_id)
        reported = (
            decision.category
            if decision is not None and decision.outcome == _EXCEPTION
            else _predicted_outcome(case.case_id, decisions)
        )
        if reported is None:  # CaseDecision validation makes this unreachable.
            continue
        row = confusion.setdefault(expected, {})
        row[reported] = row.get(reported, 0) + 1
    confusion = {
        expected: dict(sorted(predictions.items()))
        for expected, predictions in sorted(confusion.items())
    }

    abstained_cases = [
        case
        for case in key.cases
        if _predicted_outcome(case.case_id, decisions) == _ABSTAIN
    ]
    correct_refusals = sum(
        case.expected_outcome == _ABSTAIN for case in abstained_cases
    )
    missed_resolutions = sum(
        case.expected_outcome != _ABSTAIN for case in abstained_cases
    )

    no_action_cases = [case for case in key.cases if case.expected_outcome == _NO_ACTION]
    no_action_false_positives = sum(
        _predicted_outcome(case.case_id, decisions) == _EXCEPTION
        for case in no_action_cases
    )

    return ScoreReport(
        total_cases=len(key.cases),
        correct_cases=correct_cases,
        outcome_accuracy=_ratio(correct_cases, len(key.cases)),
        false_match_rate=_ratio(allocation_fp, asserted),
        allocations=allocation_metrics,
        per_scenario=per_scenario,
        exception_categories=category_metrics,
        exception_confusion=confusion,
        abstention_count=len(abstained_cases),
        correct_refusals=correct_refusals,
        missed_resolutions=missed_resolutions,
        no_action_false_positives=no_action_false_positives,
        no_action_support=len(no_action_cases),
        no_action_false_positive_rate=_ratio(
            no_action_false_positives, len(no_action_cases)
        ),
    )


def precision_coverage_curve(
    agent_output: AgentOutput,
    key: AnswerKey,
    thresholds: Sequence[float],
) -> list[tuple[float, float, float, float]]:
    """Price confidence abstention at case level and false matches atomically.

    Coverage is the fraction of cases with a retained non-ABSTAIN decision;
    precision is exact case correctness among those retained decisions.  The
    false-match denominator remains all allocations asserted by retained
    decisions, matching the top-level definition rather than inventing a
    threshold-specific metric.
    """

    if not isinstance(agent_output, AgentOutput):
        raise TypeError("agent_output must be an AgentOutput")
    if not isinstance(key, AnswerKey):
        raise TypeError("key must be an AnswerKey")
    decisions = _decisions_by_case(agent_output, key)
    rows: list[tuple[float, float, float, float]] = []
    for raw_threshold in thresholds:
        threshold = _confidence(raw_threshold)
        retained = {
            case_id: decision
            for case_id, decision in decisions.items()
            if decision.confidence >= threshold and decision.outcome != _ABSTAIN
        }
        covered = len(retained)
        correct = sum(
            _case_is_correct(case, retained, key.allocations_for(case.case_id))
            for case in key.cases
            if case.case_id in retained
        )
        proposed = frozenset(
            allocation
            for decision in retained.values()
            for allocation in decision.allocations
        )
        false_matches = len(proposed - key.allocation_pairs)
        rows.append(
            (
                threshold,
                _ratio(covered, len(key.cases)),
                _ratio(correct, covered),
                _ratio(false_matches, len(proposed)),
            )
        )
    return rows
