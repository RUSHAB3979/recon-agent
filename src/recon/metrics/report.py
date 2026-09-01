"""The agent scored against the published floor, through one shared instrument.

ONE SCORER, TWO CONSUMERS. The floor and the agent are measured by the same
code in ``recon.metrics.score``, called from the same place. If the floor were
computed by one scorer and the agent by another, the difference between them
would not be attributable to capability, and the headline claim of this project
-- that the agent beats a measured floor -- would stop meaning anything.

Three numbers are printed for every family and never averaged across them. A
single blended figure would let a hard family hide behind an easy one, which is
the exact form of dishonesty the difficulty floor exists to prevent.

WHAT TO READ FIRST

    Not the outcome accuracy. Read false-match rate and allocation precision
    beside it, because outcome accuracy alone rewards guessing: a contested
    refund charged to the wrong event still produces the expected RECONCILED.
    B2 demonstrates that in public -- it posts a higher outcome accuracy than
    the join-only agent while booking false attributions, and the columns here
    are ordered so that trade is visible rather than buried.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from recon.match.controller import ABSTAIN, RunResult, reconcile
from recon.match.exceptions import build_exception_list, format_text
from recon.match.passes import DEFAULT_LADDER, Pass
from recon.metrics import baselines
from recon.metrics.score import (
    AnswerKey,
    ScoreReport,
    precision_coverage_curve,
    score,
)

__all__ = ["CURVE_THRESHOLDS", "compare", "render"]

# The abstention dial, swept from "keep everything" to "keep only certainty".
# Published as a curve rather than as a single operating point because coverage
# and correctness trade off, and which end of that trade is right is the
# operator's call, not the agent's.
CURVE_THRESHOLDS = (0.00, 0.25, 0.50, 0.75, 0.90, 0.95, 1.00)


def compare(
    directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER
) -> tuple[ScoreReport, ScoreReport, ScoreReport, RunResult, AnswerKey]:
    """Score the agent, B1 and B2 over one dataset directory.

    The answer key is returned rather than re-loaded by the caller so that
    every figure in one rendered family is scored against the same key object.
    Two loads would almost always agree, and the one time they did not the
    report would disagree with itself without saying so.
    """
    directory = Path(directory)
    key = AnswerKey.load(directory)
    run = reconcile(directory, ladder)
    agent = score(run.to_agent_output(), key)

    batch = baselines.Batch.load(directory)
    b1 = baselines.score_shared(directory, baselines.run_b1(batch))
    b2 = baselines.score_shared(directory, baselines.run_b2(batch))
    return agent, b1, b2, run, key


def _row(label: str, report: ScoreReport) -> str:
    allocations = report.allocations
    return (
        f"    {label:<8} {report.correct_cases:>3}/{report.total_cases:<4} "
        f"{report.outcome_accuracy:>8.1%} "
        f"{report.false_match_rate:>12.2%} "
        f"{allocations.precision:>10.4f} {allocations.recall:>8.4f} "
        f"{allocations.false_positives:>6}"
    )


def _print_classification(agent: ScoreReport) -> None:
    """Print the exception breakdown by category, and the two rates beside it.

    The project brief asks for a breakdown by category rather than a total, and
    for good reason: a single exception count says nothing about whether the
    agent can tell one kind of break from another. Precision and recall are
    printed per category, and the confusion matrix underneath shows where a
    misclassification actually went -- a category that is wrong in a consistent
    direction is a fixable rule, and a category that scatters is a design flaw.

    Two numbers follow that are easy to omit and change how the rest reads. The
    abstention split separates a refusal that was RIGHT (the case was genuinely
    unresolvable) from one that cost a resolution the agent could have made;
    without it, abstention looks free. And the NO_ACTION false-positive rate
    measures the trap class -- terminal payments that were never going to
    settle. An agent that files those as exceptions inflates its exception list
    with work that does not exist, so the rate is published whether or not it
    flatters the run.
    """
    print("\n    exception classification (precision / recall / support):")
    if agent.exception_categories:
        width = max(len(name) for name in agent.exception_categories)
        for category, metrics in sorted(agent.exception_categories.items()):
            print(
                f"      {category:<{width}}  {metrics.precision:>7.4f} / "
                f"{metrics.recall:>6.4f} / {metrics.support:>3}"
            )
    else:
        print("      (no exceptions expected in this family)")

    print("\n    confusion (expected -> reported):")
    if agent.exception_confusion:
        for expected, predictions in sorted(agent.exception_confusion.items()):
            cells = ", ".join(
                f"{predicted}={count}" for predicted, count in sorted(predictions.items())
            )
            print(f"      {expected}: {cells}")
    else:
        print("      (none)")

    print(
        f"\n    abstentions {agent.abstention_count:>4}  "
        f"(correct refusals {agent.correct_refusals}, "
        f"missed resolutions {agent.missed_resolutions})"
    )
    print(
        f"    no_action false positives "
        f"{agent.no_action_false_positives}/{agent.no_action_support} "
        f"({agent.no_action_false_positive_rate:.4f})"
    )


def _print_curve(run: RunResult, key: AnswerKey) -> None:
    """Print precision and coverage as the abstention threshold is swept.

    A single accuracy figure hides the trade every reconciliation team actually
    argues about: how much of the batch the machine is allowed to close against
    how often it may be wrong. So the dial is swept and the whole curve is
    published, and the operator picks the row they can defend to their auditor.

    THE CURVE IS FLAT HERE, AND THAT IS THE FINDING. The deterministic ladder
    emits exactly two confidences -- 1.0 when all nine gates leave one survivor,
    0.0 when it abstains -- so no threshold in (0, 1] moves a single decision
    and there is nothing to trade. That is not a defect in the instrument, it is
    what "proof or abstain" means when you measure it: the engine has no ranked
    middle to spend, because it never ranks. The dial is real only on the
    evidence-reading rung, where ``--min-confidence`` decides how sure the model
    must be before its reading is allowed to stand.

    The degeneracy note below is computed from the run rather than asserted in
    prose, so a rung that later emits graded confidence retires the note by
    changing the data, not by somebody remembering to delete a paragraph.
    """
    output = run.to_agent_output()
    rows = precision_coverage_curve(output, key, CURVE_THRESHOLDS)

    print("\n    precision / coverage across abstention thresholds:")
    print(
        f"      {'threshold':>9} {'coverage':>9} {'precision':>10} "
        f"{'false match':>12}"
    )
    for threshold, coverage, precision, false_match in rows:
        print(
            f"      {threshold:>9.2f} {coverage:>9.4f} {precision:>10.4f} "
            f"{false_match:>12.4f}"
        )

    retained = sorted(
        {
            decision.confidence
            for decision in output.decisions
            if decision.outcome != ABSTAIN
        }
    )
    if len(retained) <= 1:
        held = ", ".join(f"{value:.2f}" for value in retained) or "none"
        print(
            f"      flat by construction: every retained decision carries "
            f"confidence {held}, so no threshold moves one."
        )


def _print_queue(run: RunResult) -> None:
    """Print the head of the operator queue this run would hand to a human.

    Printed in the metrics report on purpose. The exception list is the
    deliverable the brief calls for -- "the exceptions it could not resolve" --
    and keeping it in a file nobody opens while the report prints only rates
    would make the honest part of this project the easy part to miss.
    """
    items = build_exception_list(run)
    print("\n    operator queue:")
    for line in format_text(items, limit=5, compact=True).splitlines():
        print(f"      {line}" if line else "")


def render(directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER) -> None:
    agent, b1, b2, run, key = compare(directory, ladder)

    print(f"\n{directory}")
    print(
        f"    {'':8} {'cases':>8} {'outcome':>8} {'false match':>12} "
        f"{'alloc P':>10} {'alloc R':>8} {'FP':>6}"
    )
    print(_row("B1", b1))
    print(_row("B2", b2))
    print(_row("agent", agent))

    # D is quoted against B2, not B1. B1 is forbidden from refund recovery, and
    # a floor built on a restriction rather than on the data would be the easy
    # number to beat rather than the honest one.
    print(
        f"\n    difficulty floor D = {1 - b2.outcome_accuracy:.1%} "
        f"(vs B2)   headroom over B2 = "
        f"{agent.correct_cases - b2.correct_cases:+d} cases"
    )

    print("\n    per-pass yield (examined / claimed / abstained):")
    for result in run.ladder.per_pass:
        print(
            f"      {result.pass_name:<20} {result.examined:>5} / "
            f"{len(result.claims):>5} / {len(result.abstentions):>5}"
        )
    # Per-gate elimination counts, printed even when zero. A gate that does no
    # work on this data is a fact about the data, and publishing the zero is the
    # only honest way to keep the gate: it lets a reader judge whether it earns
    # its place instead of taking the count on trust.
    for result in run.ladder.per_pass:
        gates = {k: v for k, v in result.counters.items() if k.startswith("gate_")}
        if not gates:
            continue
        shortlisted = result.counters.get("candidates_by_amount", 0)
        print(
            f"\n    {result.pass_name} -- {shortlisted} amount-matched "
            f"candidate(s) across {result.examined} line(s), eliminated per gate:"
        )
        for name, count in gates.items():
            if name.startswith("gate_2"):
                # Applied as an index lookup, so it has no elimination count by
                # construction: the shortlist above IS what survived it.
                print(f"      {name:<28} {chr(45)*2:>5}   (applied as the index above)")
                continue
            mark = "" if count else "   (no effect on this data)"
            print(f"      {name:<28} {count:>5}{mark}")

    _print_classification(agent)
    _print_curve(run, key)
    _print_queue(run)

    print(
        f"\n    throughput  {run.throughput:>9.0f} records/sec "
        f"({run.record_count} records in {run.elapsed_seconds:.3f}s)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the agent against the published baselines, per family."
    )
    parser.add_argument(
        "data_dir",
        type=Path,
        nargs="*",
        default=[Path("data/dev"), Path("data/primary"), Path("data/stress")],
    )
    args = parser.parse_args(argv)
    for directory in args.data_dir:
        render(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
