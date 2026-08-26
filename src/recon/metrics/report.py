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

from recon.match.controller import RunResult, reconcile
from recon.match.passes import DEFAULT_LADDER, Pass
from recon.metrics import baselines
from recon.metrics.score import AnswerKey, ScoreReport, score

__all__ = ["compare", "render"]


def compare(
    directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER
) -> tuple[ScoreReport, ScoreReport, ScoreReport, RunResult]:
    """Score the agent, B1 and B2 over one dataset directory."""
    directory = Path(directory)
    key = AnswerKey.load(directory)
    run = reconcile(directory, ladder)
    agent = score(run.to_agent_output(), key)

    batch = baselines.Batch.load(directory)
    b1 = baselines.score_shared(directory, baselines.run_b1(batch))
    b2 = baselines.score_shared(directory, baselines.run_b2(batch))
    return agent, b1, b2, run


def _row(label: str, report: ScoreReport) -> str:
    allocations = report.allocations
    return (
        f"    {label:<8} {report.correct_cases:>3}/{report.total_cases:<4} "
        f"{report.outcome_accuracy:>8.1%} "
        f"{report.false_match_rate:>12.2%} "
        f"{allocations.precision:>10.4f} {allocations.recall:>8.4f} "
        f"{allocations.false_positives:>6}"
    )


def render(directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER) -> None:
    agent, b1, b2, run = compare(directory, ladder)

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
