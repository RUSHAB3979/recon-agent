"""The holdout run: unseen seeds, one run, whatever it says.

WHY THIS EXISTS SEPARATELY FROM `make report`

    Every number this project publishes comes from three dataset directories
    that have been generated, scored, inspected and re-scored hundreds of times
    during development. Two of them -- `primary` and `stress` -- were never
    tuned against, and that discipline is real, but it is a discipline rather
    than a property: nothing in the repository could stop a threshold from
    having been nudged after a disappointing primary run, and "we didn't" is
    exactly the kind of assurance a reviewer is right to discount.

    A holdout is what turns that assurance into evidence. These seeds have
    never been generated, never been scored, and never been looked at, by any
    command in this repository or by its author, before the run that publishes
    them.

THE PROTOCOL, DECLARED BEFORE THE NUMBERS

    1. The seeds are the contiguous block 70001-70005. Contiguous and stated in
       advance, so they cannot have been selected for being kind: anyone can
       check that the block starts where it says and skips nothing.
    2. The family is PRIMARY -- the realistic-prevalence mix that produces the
       headline, not the enriched one.
    3. The ladder is DEFAULT_LADDER, frozen, with the evidence-reading rung off.
    4. The metrics are the ones already published: cases, outcome accuracy,
       false-match rate, allocation precision and recall, and D against B2.
    5. **It runs once.** If a number disappoints, it gets reported, not
       re-tuned. No threshold, tolerance, gate or scenario share may change in
       response to what this prints -- and this file is committed before the
       run, so the history shows which came first.

    Five seeds rather than one because a single draw is a point estimate with
    no spread, and reporting a band is more honest than reporting whichever
    single number arrived. Five *pre-declared* seeds, all reported, is not the
    same thing as running twenty and publishing the best.

WHAT IT STILL DOES NOT MEASURE

    The holdout is a different seed from the same generator. It measures
    whether the tolerances were fitted to the particular batches in `data/`.
    It does not measure robustness to real bank data, because the same code
    wrote both the defects and their labels. That limitation does not go away
    here, and this run must not be described as if it did.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon.datagen import Family, GenConfig, generate  # noqa: E402
from recon.datagen.io import write_dataset  # noqa: E402
from recon.match.controller import reconcile  # noqa: E402
from recon.match.passes import DEFAULT_LADDER  # noqa: E402
from recon.metrics import baselines  # noqa: E402
from recon.metrics.score import AnswerKey, ScoreReport, score  # noqa: E402

# Declared in advance. A contiguous block, so it cannot be a selection.
HOLDOUT_SEEDS: tuple[int, ...] = (70001, 70002, 70003, 70004, 70005)
HOLDOUT_FAMILY = Family.PRIMARY
RECORDS = 500

# Seeds that appear anywhere else in this repository. A holdout seed colliding
# with one of these would silently be a re-run of something already seen, which
# is the one way this measurement can be wrong without looking wrong.
SEEDS_ALREADY_SEEN = frozenset({7, 8, 42, 99, 101, 2026, 20260905})


def _rows(report: ScoreReport, b2: ScoreReport) -> dict[str, float | int | str]:
    return {
        "cases": f"{report.correct_cases}/{report.total_cases}",
        "outcome": report.outcome_accuracy,
        "false_match": report.false_match_rate,
        "alloc_p": report.allocations.precision,
        "alloc_r": report.allocations.recall,
        "alloc_fp": report.allocations.false_positives,
        "floor_d": 1 - b2.outcome_accuracy,
        "headroom": report.correct_cases - b2.correct_cases,
    }


def run_seed(seed: int, workdir: Path) -> dict[str, float | int | str]:
    """Generate one unseen batch, reconcile it, and score it. Once."""
    directory = workdir / f"holdout-{seed}"
    write_dataset(
        generate(GenConfig(n_records=RECORDS, seed=seed, family=HOLDOUT_FAMILY)),
        directory,
    )
    key = AnswerKey.load(directory)
    agent = score(reconcile(directory, DEFAULT_LADDER).to_agent_output(), key)
    batch = baselines.Batch.load(directory)
    b2 = baselines.score_shared(directory, baselines.run_b2(batch))
    return {"seed": seed, **_rows(agent, b2)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="*",
        default=list(HOLDOUT_SEEDS),
        help="override the declared seeds (for inspecting the harness only)",
    )
    args = parser.parse_args(argv)

    collisions = sorted(set(args.seeds) & SEEDS_ALREADY_SEEN)
    if collisions:
        parser.error(
            f"seed(s) {collisions} appear elsewhere in this repository; a holdout "
            "seed that has already been seen is not a holdout"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"holdout run  {stamp}")
    print(f"  family {HOLDOUT_FAMILY.value}, {RECORDS} records, seeds {args.seeds}")
    print("  ladder frozen, evidence-reading rung off\n")

    header = (
        f"  {'seed':>7} {'cases':>8} {'outcome':>9} {'false match':>12} "
        f"{'alloc P':>9} {'alloc R':>9} {'FP':>4} {'D vs B2':>9} {'headroom':>9}"
    )
    print(header)

    results = []
    with tempfile.TemporaryDirectory(prefix="recon-holdout-") as tmp:
        for seed in args.seeds:
            row = run_seed(seed, Path(tmp))
            results.append(row)
            print(
                f"  {row['seed']:>7} {row['cases']:>8} {row['outcome']:>8.1%} "
                f"{row['false_match']:>11.2%} {row['alloc_p']:>9.4f} "
                f"{row['alloc_r']:>9.4f} {row['alloc_fp']:>4} "
                f"{row['floor_d']:>8.1%} {row['headroom']:>+9d}"
            )

    accuracy = [float(row["outcome"]) for row in results]
    false_match = [float(row["false_match"]) for row in results]
    precision = [float(row["alloc_p"]) for row in results]
    print(
        f"\n  outcome accuracy   mean {statistics.mean(accuracy):.1%}   "
        f"min {min(accuracy):.1%}   max {max(accuracy):.1%}"
    )
    print(
        f"  false-match rate   max {max(false_match):.2%}   "
        f"allocation precision min {min(precision):.4f}"
    )
    print(
        "\n  This ran once. Whatever it says is what gets published; no "
        "threshold\n  changes in response to it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
