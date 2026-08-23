"""Regression tests for the scorer that both agents and baselines must use."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import ALLOCATION_FIELDS, CASE_FIELDS, write_dataset
from recon.metrics import (
    AgentOutput,
    AnswerKey,
    CaseDecision,
    precision_coverage_curve,
    score,
)


def _case(
    case_id: str,
    outcome: str,
    *,
    scenario: str = "FUTURE_SCENARIO",
    settlement_ids: str = "",
    event_ids: str = "",
    category: str = "",
) -> dict[str, str]:
    return {
        "case_id": case_id,
        "scenario": scenario,
        "expected_outcome": outcome,
        "settlement_ids": settlement_ids,
        "bank_row_ids": "bank_1" if settlement_ids else "",
        "event_ids": event_ids,
        "expected_exception_category": category,
        "notes": "ignored by scoring",
    }


def _allocation(event_id: str, settlement_id: str) -> dict[str, str]:
    return {
        "event_id": event_id,
        "settlement_id": settlement_id,
        "bank_row_id": "bank_1",
    }


def _write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _key(
    directory: Path,
    cases: list[dict[str, str]],
    allocations: list[dict[str, str]] | None = None,
) -> AnswerKey:
    directory.mkdir()
    _write_rows(directory / "answer_key_cases.csv", CASE_FIELDS, cases)
    _write_rows(
        directory / "answer_key_allocations.csv",
        ALLOCATION_FIELDS,
        allocations or [],
    )
    return AnswerKey.load(directory)


def _perfect_output(key: AnswerKey) -> AgentOutput:
    return AgentOutput(
        CaseDecision(
            case_id=case.case_id,
            outcome=case.expected_outcome,
            category=(
                case.expected_exception_category
                if case.expected_outcome == "EXCEPTION"
                else None
            ),
            allocations=key.allocations_for(case.case_id),
        )
        for case in key.cases
    )


@pytest.fixture(scope="session")
def generated_primary(tmp_path_factory) -> tuple[AnswerKey, AgentOutput]:
    out = tmp_path_factory.mktemp("score-primary-seed-42")
    dataset = generate(
        GenConfig(n_records=500, seed=42, family=Family.PRIMARY)
    )
    write_dataset(dataset, out)
    key = AnswerKey.load(out)
    return key, _perfect_output(key)


def test_real_generated_primary_key_loads_and_perfect_agent_scores_one(
    generated_primary,
) -> None:
    """The suite must cross the real serialization boundary the old tests skipped."""

    key, output = generated_primary
    report = score(output, key)

    assert len(key.cases) > 0
    assert len(key.allocations) > 0
    assert report.outcome_accuracy == 1.0
    assert report.allocation_precision == 1.0
    assert report.allocation_recall == 1.0
    assert report.allocation_f1 == 1.0
    assert report.false_match_rate == 0.0
    assert report.no_action_false_positive_rate == 0.0
    assert report.missed_resolutions == 0
    assert all(correct == total for correct, total in report.per_scenario.values())
    assert all(
        metrics.precision == metrics.recall == 1.0
        for metrics in report.exception_categories.values()
        if metrics.support
    )
    assert json.loads(json.dumps(report.to_dict()))["outcome_accuracy"] == 1.0
    lines = report.format_text().splitlines()
    assert "Outcome accuracy" in lines[1]
    assert "False-match rate" in lines[2]


def test_outcome_right_but_one_wrong_allocation_is_scored_wrong(
    tmp_path: Path,
) -> None:
    """A false attribution cannot hide behind the correct RECONCILED label."""

    allocations = [_allocation(f"event_{index}", "settlement_1") for index in range(8)]
    key = _key(
        tmp_path / "key",
        [
            _case(
                "case_1",
                "RECONCILED",
                settlement_ids="settlement_1",
                event_ids="|".join(row["event_id"] for row in allocations),
            )
        ],
        allocations,
    )
    asserted = {(row["event_id"], "settlement_1") for row in allocations[:7]}
    asserted.add(("event_wrong", "settlement_1"))
    report = score(
        AgentOutput(
            [
                CaseDecision(
                    "case_1", "RECONCILED", allocations=frozenset(asserted)
                )
            ]
        ),
        key,
    )

    assert report.outcome_accuracy == 0.0
    assert report.correct_cases == 0
    assert report.allocations.true_positives == 7
    assert report.allocations.false_positives == 1
    assert report.allocations.false_negatives == 1
    assert report.allocation_precision == 7 / 8
    assert report.allocation_recall == 7 / 8
    assert report.false_match_rate == 1 / 8


def test_omitted_case_is_an_abstention_not_a_dropped_denominator(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [
            _case("case_answered", "NO_ACTION", scenario="ONE"),
            _case("case_omitted", "NO_ACTION", scenario="TWO"),
        ],
    )
    report = score(
        AgentOutput([CaseDecision("case_answered", "NO_ACTION")]), key
    )

    assert report.total_cases == 2
    assert report.correct_cases == 1
    assert report.outcome_accuracy == 0.5
    assert report.abstention_count == 1
    assert report.correct_refusals == 0
    assert report.missed_resolutions == 1


def test_correct_refusal_and_missed_resolution_are_counted_separately(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [
            _case("case_refuse", "ABSTAIN", scenario="AMBIGUOUS"),
            _case("case_missed", "RECONCILED", scenario="RESOLVABLE"),
        ],
    )

    report = score(AgentOutput(), key)

    assert report.abstention_count == 2
    assert report.correct_refusals == 1
    assert report.missed_resolutions == 1
    assert report.to_dict()["abstention"] == {
        "count": 2,
        "correct_refusals": 1,
        "missed_resolutions": 1,
    }


def test_exception_on_no_action_case_raises_false_positive_rate(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [_case("case_trap", "NO_ACTION", scenario="NOT_SETTLEABLE")],
    )
    report = score(
        AgentOutput(
            [CaseDecision("case_trap", "EXCEPTION", "CAPTURED_UNSETTLED")]
        ),
        key,
    )

    assert report.no_action_false_positives == 1
    assert report.no_action_support == 1
    assert report.no_action_false_positive_rate == 1.0


def test_exception_metrics_are_one_vs_rest_and_include_confusion(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [
            _case("case_a", "EXCEPTION", category="CATEGORY_A"),
            _case("case_b", "EXCEPTION", category="CATEGORY_B"),
            _case("case_n", "NO_ACTION"),
        ],
    )
    output = AgentOutput(
        [
            CaseDecision("case_a", "EXCEPTION", "CATEGORY_A"),
            CaseDecision("case_b", "EXCEPTION", "CATEGORY_A"),
            CaseDecision("case_n", "EXCEPTION", "CATEGORY_NEW"),
        ]
    )

    report = score(output, key)

    category_a = report.exception_categories["CATEGORY_A"]
    assert (category_a.true_positives, category_a.false_positives) == (1, 1)
    assert category_a.precision == 0.5
    assert category_a.recall == 1.0
    assert category_a.support == 1
    assert report.exception_categories["CATEGORY_B"].recall == 0.0
    assert report.exception_categories["CATEGORY_NEW"].support == 0
    assert report.exception_confusion == {
        "CATEGORY_A": {"CATEGORY_A": 1},
        "CATEGORY_B": {"CATEGORY_A": 1},
    }


def test_precision_coverage_curve_coverage_is_monotone_as_threshold_rises(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [
            _case("case_high", "RECONCILED"),
            _case("case_mid", "RECONCILED"),
            _case("case_low", "RECONCILED"),
        ],
    )
    output = AgentOutput(
        [
            CaseDecision("case_high", "RECONCILED", confidence=0.9),
            CaseDecision("case_mid", "RECONCILED", confidence=0.6),
            CaseDecision("case_low", "NO_ACTION", confidence=0.2),
        ]
    )

    rows = precision_coverage_curve(output, key, [0.0, 0.5, 0.8, 1.0])

    assert [row[1] for row in rows] == pytest.approx([1.0, 2 / 3, 1 / 3, 0.0])
    assert [row[2] for row in rows] == pytest.approx([2 / 3, 1.0, 1.0, 0.0])
    assert all(
        earlier[1] >= later[1] for earlier, later in zip(rows, rows[1:])
    )


def test_duplicate_case_id_raises_at_load(tmp_path: Path) -> None:
    cases = [_case("duplicate", "NO_ACTION"), _case("duplicate", "NO_ACTION")]
    with pytest.raises(ValueError, match="duplicate case_id"):
        _key(tmp_path / "key", cases)


def test_unknown_expected_outcome_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown expected_outcome"):
        _key(tmp_path / "key", [_case("case_1", "AUTO_MATCH")])


def test_exception_without_category_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty expected_exception_category"):
        _key(tmp_path / "key", [_case("case_1", "EXCEPTION")])


def test_non_exception_with_category_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-EXCEPTION"):
        _key(
            tmp_path / "key",
            [_case("case_1", "RECONCILED", category="SHOULD_BE_EMPTY")],
        )


def test_reconciled_warning_in_current_generated_schema_loads(
    tmp_path: Path,
) -> None:
    key = _key(
        tmp_path / "key",
        [
            _case(
                "case_1",
                "RECONCILED",
                category="FUTURE_DIAGNOSTIC_WARNING",
            )
        ],
    )
    assert key.cases[0].expected_exception_category == "FUTURE_DIAGNOSTIC_WARNING"


def test_allocation_with_unknown_settlement_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="appears in no case"):
        _key(
            tmp_path / "key",
            [_case("case_1", "RECONCILED", settlement_ids="settlement_known")],
            [_allocation("event_1", "settlement_unknown")],
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
def test_confidence_must_be_finite_and_in_unit_interval(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        CaseDecision("case_1", "RECONCILED", confidence=confidence)


def test_case_cannot_be_decided_twice() -> None:
    with pytest.raises(ValueError, match="more than once"):
        AgentOutput(
            [
                CaseDecision("case_1", "RECONCILED"),
                CaseDecision("case_1", "NO_ACTION"),
            ]
        )


def test_unknown_case_id_is_an_error_when_scored(tmp_path: Path) -> None:
    key = _key(tmp_path / "key", [_case("case_known", "NO_ACTION")])
    with pytest.raises(ValueError, match="unknown case_id"):
        score(AgentOutput([CaseDecision("case_unknown", "NO_ACTION")]), key)


def test_to_dict_is_byte_identical_across_runs(generated_primary) -> None:
    key, output = generated_primary

    first = json.dumps(score(output, key).to_dict(), separators=(",", ":")).encode()
    second = json.dumps(score(output, key).to_dict(), separators=(",", ":")).encode()

    assert first == second
