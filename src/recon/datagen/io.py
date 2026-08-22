"""Serialisation of a generated Dataset to disk.

Five files per dataset:

  gateway_ledger.csv     source A, shuffled
  bank_settlement.csv    source B, shuffled
  answer_key_links.csv   semantic ground truth: one row per relationship
  answer_key_pairs.csv   atomic ground truth: one row per (txn_id, utr)
  dataset_meta.json      seed, counts, defect mix -- the reproducibility record

The two answer-key views exist because they answer different questions.  Pairs
give honest partial credit on batch matches when computing precision/recall;
links are what exception classification and duplicate detection are scored on.
Reporting only one of them would let a weak result hide.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .entities import Dataset

GATEWAY_FIELDS = [
    "txn_id", "order_id", "amount", "currency", "status",
    "created_at", "method", "fee", "tax", "net_amount", "refund_amount",
]
BANK_FIELDS = ["utr", "settlement_date", "credit_amount", "narration", "bank_ref"]
LINK_FIELDS = [
    "link_id", "scenario", "expected_resolution", "txn_ids", "utrs",
    "n_txns", "n_credits", "amount_basis", "expected_inr_total",
    "actual_inr_total", "notes",
]
PAIR_FIELDS = ["txn_id", "utr", "link_id", "scenario"]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_dataset(dataset: Dataset, outdir: Path, prefix: str = "") -> dict[str, Path]:
    """Write all five files.  `prefix` distinguishes dev/ from holdout/ runs."""
    outdir.mkdir(parents=True, exist_ok=True)
    p = lambda name: outdir / f"{prefix}{name}"  # noqa: E731

    paths = {
        "gateway": p("gateway_ledger.csv"),
        "bank": p("bank_settlement.csv"),
        "links": p("answer_key_links.csv"),
        "pairs": p("answer_key_pairs.csv"),
        "meta": p("dataset_meta.json"),
    }

    _write_csv(paths["gateway"], GATEWAY_FIELDS, [t.to_row() for t in dataset.gateway])
    _write_csv(paths["bank"], BANK_FIELDS, [c.to_row() for c in dataset.bank])
    _write_csv(paths["links"], LINK_FIELDS, [l.to_row() for l in dataset.links])
    _write_csv(paths["pairs"], PAIR_FIELDS, [pr.to_row() for pr in dataset.pairs])

    meta = dict(dataset.config_meta)
    meta["scenario_counts"] = dataset.summary()
    meta["resolution_counts"] = _resolution_counts(dataset)
    paths["meta"].write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    return paths


def _resolution_counts(dataset: Dataset) -> dict[str, int]:
    counts: dict[str, int] = {}
    for link in dataset.links:
        counts[link.resolution.value] = counts.get(link.resolution.value, 0) + 1
    return dict(sorted(counts.items()))
