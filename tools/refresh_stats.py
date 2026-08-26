"""Regenerate the README dataset table from the data actually on disk.

Hand-maintained metrics tables go stale the moment a config knob moves -- this
one went stale twice during the generator's own development, both times quoting
numbers from a superseded amount distribution, and a third time when the whole
schema moved to event rows.  A README whose headline figures disagree with
`make verify` is worse than one with no figures, so the table is generated
rather than typed.

The table leads with the difficulty floor D rather than with a row count.  A
row count says how big the dataset is; D says how much of it a plain exact-join
script already solves, which is the only context in which a later match rate
means anything.

Run via `make stats` (and it runs as part of `make verify`).
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ambiguity import measure  # noqa: E402

from recon.metrics.baselines import (  # noqa: E402
    Batch,
    lexical_hit_rate,
    run_b1,
    run_b2,
    run_b3,
    score,
)

START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"

# Every file in this repo is written and read as UTF-8.  Relying on the
# platform default made this script crash on Windows the moment a rupee sign
# entered the README, which is a silly way to lose a metrics table.
ENCODING = "utf-8"

# Three families are published side by side because a single blended number
# would let a hard family hide behind an easy one.  dev is the only surface any
# threshold may be tuned against; the other two are reported, never inspected
# while tuning.
DATASETS = (
    ("dev", "development, seed 42"),
    ("primary", "primary, seed 20260905"),
    ("stress", "stress, seed 101"),
)


def _collect(data_dir: pathlib.Path) -> dict[str, Any]:
    meta = json.loads((data_dir / "dataset_meta.json").read_text(encoding=ENCODING))
    ambiguity = measure(data_dir)
    batch = Batch.load(data_dir)
    baseline = score(batch, run_b1(batch))
    strengthened = score(batch, run_b2(batch))
    lexical = score(batch, run_b3(batch))
    hit = lexical_hit_rate(batch)
    return {
        "family": meta["family"],
        "events": meta["n_gateway_events"],
        "cases": meta["n_cases"],
        "settlements": meta["n_settlements"],
        "bank_rows": meta["n_bank_rows"],
        "detail_rows": meta["n_settlement_detail_rows"],
        "scenarios": meta["scenario_case_counts"],
        "outcomes": meta["expected_outcome_case_counts"],
        "amb_ambiguous": ambiguity["ambiguous"],
        "amb_examined": ambiguity["examined"],
        "b1_correct": baseline["b1_correct"],
        "b1_accuracy": baseline["b1_accuracy"],
        "floor": baseline["difficulty_floor_D"],
        "b2_correct": strengthened["b1_correct"],
        "b2_floor": strengthened["difficulty_floor_D"],
        "b3_correct": lexical["b1_correct"],
        "lexical_hits": int(hit["hits"]),
        "lexical_expected": hit["expected_by_chance"],
        "lexical_lines": int(hit["decidable_lines"]),
        "false_attributions": strengthened["false_attributions"],
    }


def _row(label: str, key: str, cols: list[dict[str, Any]], bold: bool = False) -> str:
    cells = "".join(f" {c[key]} |" for c in cols)
    name = f"**{label}**" if bold else label
    return f"| {name} |{cells}"


def build_table() -> str:
    cols = [_collect(ROOT / "data" / name) for name, _ in DATASETS]
    headers = "".join(f" {name} ({seed}) |" for name, seed in DATASETS)

    lines = [
        START,
        f"| |{headers}",
        "|---|" + "---|" * len(cols),
        _row("Gateway events", "events", cols),
        _row("Reconciliation cases", "cases", cols),
        _row("Settlements", "settlements", cols),
        _row("Settlement detail lines", "detail_rows", cols),
        _row("Bank credits", "bank_rows", cols),
    ]

    ambiguous = "".join(
        f" {c['amb_ambiguous']} / {c['amb_examined']} |" for c in cols
    )
    lines.append(f"| Refund deltas with >1 exact candidate |{ambiguous}")

    b1 = "".join(f" {c['b1_correct']} / {c['cases']} |" for c in cols)
    lines.append(f"| B1 — exact joins only |{b1}")

    b2 = "".join(f" {c['b2_correct']} / {c['cases']} |" for c in cols)
    lines.append(f"| B2 — B1 + amount lookup |{b2}")

    b3 = "".join(f" {c['b3_correct']} / {c['cases']} |" for c in cols)
    lines.append(f"| B3 — B2 + fuzzy string matching |{b3}")

    # The row that answers "you only needed string matching".  Hits against
    # chance, not accuracy: B3's case count moves for tie-break knock-on
    # reasons, the per-decision hit rate does not.
    lexical = "".join(
        f" {c['lexical_hits']} / {c['lexical_lines']} vs {c['lexical_expected']:.1f} |"
        for c in cols
    )
    lines.append(f"| B3 lexical hits vs chance |{lexical}")

    wrong = "".join(f" {c['false_attributions']} |" for c in cols)
    lines.append(f"| B2 false attributions |{wrong}")

    # D is published against B2, not B1.  B1 flatters the benchmark; B2 is the
    # attack a sceptical reviewer would actually run, so it is the honest
    # denominator and the one every later match rate is quoted against.
    floor = "".join(f" **{c['b2_floor']:.1%}** |" for c in cols)
    lines.append(f"| **Difficulty floor D (vs B2)** |{floor}")

    # The published case mix.  Generated rather than typed for the same reason
    # the counts are: a prevalence table that drifts from the generator turns
    # every per-class number quoted against it into a wrong number.
    scenarios = sorted({s for c in cols for s in c["scenarios"]})
    lines += [
        "",
        "Case mix by scenario:",
        "",
        f"| scenario |{headers}",
        "|---|" + "---|" * len(cols),
    ]
    for scenario in scenarios:
        counts = "".join(f" {c['scenarios'].get(scenario, 0)} |" for c in cols)
        lines.append(f"| `{scenario}` |{counts}")

    lines += [
        "",
        "<sub>Table generated by `make stats` from the data in `data/`. "
        "Do not edit by hand.</sub>",
        END,
    ]
    return "\n".join(lines)


def main() -> int:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding=ENCODING)
    if START not in text or END not in text:
        print(f"error: {START} / {END} markers missing from README.md", file=sys.stderr)
        return 1
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    readme.write_text(head + build_table() + tail, encoding=ENCODING)
    print("README stats table refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
