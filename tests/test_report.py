"""Tests for the rendered report, and for the abstention dial it publishes.

WHY THE CURVE IS A DELIVERABLE AND NOT A CHART

    Coverage and correctness trade off, and where to sit on that trade is the
    operator's call: a team that must justify every automated close to an
    auditor sits at the certain end and works a longer queue, and a team paid
    on throughput sits lower. A single accuracy figure hides the whole
    argument, so the dial is swept and every row is printed.

WHY IT IS FLAT ON THE PUBLISHED NUMBERS

    The deterministic ladder is certain or absent -- nine gates leaving one
    survivor, or an abstention -- so no threshold moves a decision. That is
    what "proof or abstain" looks like when you measure it rather than assert
    it, and the note that says so is computed from the run. The tests below
    hold both halves: the note appears when the confidences really are
    degenerate, and it disappears when a rung supplies a graded one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.adjudicator import Adjudication, AdjudicationPass, Usage
from recon.match.controller import reconcile
from recon.match.passes import DEFAULT_LADDER
from recon.metrics.report import CURVE_THRESHOLDS, compare, render
from recon.metrics.score import AnswerKey, precision_coverage_curve


@pytest.fixture(scope="module")
def directory(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("report")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


class _GradedReader:
    """Answers every residual line, at confidences that differ from each other.

    The point is not the accuracy -- it names the first candidate, so it is
    wrong about half the time by construction. The point is that the
    confidences span the threshold grid, which is the only condition under
    which a precision/coverage curve can be shown to move at all.
    """

    model = "graded"

    def __init__(self) -> None:
        self._answered = 0

    def read(self, request) -> Adjudication:
        self._answered += 1
        confidence = 0.60 + 0.12 * (self._answered % 4)
        return Adjudication(
            detail_id=request.detail_id,
            label=request.candidates[0].label,
            confidence=min(confidence, 0.99),
            reasoning="graded, for the curve test",
            model=self.model,
            usage=Usage(calls=1),
        )


# --------------------------------------------------------------------------
# the rendered surface
# --------------------------------------------------------------------------


def test_the_report_prints_a_row_for_every_declared_threshold(directory, capsys):
    render(directory)
    printed = capsys.readouterr().out
    assert "precision / coverage across abstention thresholds" in printed
    for threshold in CURVE_THRESHOLDS:
        assert f"{threshold:>9.2f}" in printed


def test_compare_returns_the_key_every_figure_was_scored_against(directory):
    """One key per family, threaded rather than re-loaded.

    Two loads would agree almost always, and the once they did not the report
    would disagree with itself without saying so.
    """
    _agent, _b1, _b2, run, key = compare(directory)
    assert isinstance(key, AnswerKey)
    assert {case.case_id for case in key.cases} == {
        verdict.case_id for verdict in run.verdicts
    }


# --------------------------------------------------------------------------
# the dial itself
# --------------------------------------------------------------------------


def test_the_deterministic_curve_is_flat_and_says_so(directory, capsys):
    render(directory)
    printed = capsys.readouterr().out
    assert "flat by construction" in printed
    assert "confidence 1.00" in printed


def test_a_flat_curve_is_flat_in_the_numbers_and_not_only_in_the_prose(directory):
    """The note is a claim about the rows above it, so check the rows."""
    run = reconcile(directory, DEFAULT_LADDER)
    rows = precision_coverage_curve(
        run.to_agent_output(), AnswerKey.load(directory), CURVE_THRESHOLDS
    )
    assert len({row[1] for row in rows}) == 1
    assert {row[2] for row in rows} == {1.0}
    assert {row[3] for row in rows} == {0.0}


def test_coverage_at_zero_is_the_batch_less_its_abstentions(directory):
    """Ties the curve to the abstention count printed a few lines above it."""
    run = reconcile(directory, DEFAULT_LADDER)
    output = run.to_agent_output()
    rows = precision_coverage_curve(output, AnswerKey.load(directory), [0.0])
    retained = sum(1 for decision in output.decisions if decision.outcome != "ABSTAIN")
    assert rows[0][1] == pytest.approx(retained / len(output.decisions))


def test_a_graded_rung_makes_the_dial_do_something(directory):
    """The curve moves once a rung supplies something to rank.

    Raising the threshold must buy precision with coverage. If it ever bought
    both, the confidences would not be ordering anything and the dial would be
    decoration.
    """
    ladder = (*DEFAULT_LADDER, AdjudicationPass(_GradedReader(), min_confidence=0.5))
    run = reconcile(directory, ladder)
    rows = precision_coverage_curve(
        run.to_agent_output(), AnswerKey.load(directory), CURVE_THRESHOLDS
    )

    coverage = [row[1] for row in rows]
    precision = [row[2] for row in rows]
    assert len(set(coverage)) > 1, "the dial did not move"
    assert coverage == sorted(coverage, reverse=True)
    assert precision[-1] > precision[0]
    assert rows[-1][3] == 0.0 and rows[0][3] > 0.0


def test_the_flatness_note_retires_itself_when_the_curve_stops_being_flat(
    directory, capsys
):
    """The note is computed from the run, not remembered by a maintainer.

    A rung that starts emitting graded confidence must not leave a printed
    sentence behind claiming the engine only ever says 1.00.
    """
    ladder = (*DEFAULT_LADDER, AdjudicationPass(_GradedReader(), min_confidence=0.5))
    render(directory, ladder)
    printed = capsys.readouterr().out
    assert "precision / coverage across abstention thresholds" in printed
    assert "flat by construction" not in printed
