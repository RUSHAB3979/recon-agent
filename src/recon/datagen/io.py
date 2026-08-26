"""Stable serialization is part of the held-out-seed claim.

Explicit field lists prevent dataclass refactors from silently changing the
public schema, while sorted metadata keys and a deterministic generated_at make
two writes of the same dataset byte-for-byte comparable.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .entities import Dataset

BATCH_CONFIG_FIELDS = [
    "batch_id", "seed", "family", "n_gateway_events", "n_cases", "generated_at",
]
PRICING_RULE_FIELDS = ["method", "fee_rate_bps", "gst_rate_bps"]
GATEWAY_FIELDS = [
    "event_id", "event_type", "txn_id", "order_id", "amount_paise",
    "currency", "status", "created_at", "method", "description",
]
DETAIL_FIELDS = [
    "detail_id", "settlement_id", "event_id", "line_type", "gross_effect_paise",
    "fee_paise", "tax_paise", "net_effect_paise", "settled_at", "currency",
    "reference_text",
]
SUMMARY_FIELDS = [
    "settlement_id", "utr", "settlement_date", "gross_payment_paise",
    "refund_paise", "fee_paise", "tax_paise", "net_amount_paise", "line_count",
    "currency", "status",
]
BANK_FIELDS = [
    "bank_row_id", "utr", "posted_at", "credit_amount_paise", "currency",
    "narration", "bank_ref",
]
CASE_FIELDS = [
    "case_id", "scenario", "expected_outcome", "settlement_ids", "bank_row_ids",
    "event_ids", "expected_exception_category", "notes",
]
ALLOCATION_FIELDS = ["event_id", "settlement_id", "bank_row_id"]

# A rewrite into an existing output directory must not leave obsolete CSVs that
# look like additional benchmark inputs. These names are narrowly scoped to the
# superseded generator and are regenerated from source if ever needed.
LEGACY_ARTIFACTS = (
    "bank_settlement.csv",
    "answer_key_links.csv",
    "answer_key_pairs.csv",
)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_dataset(dataset: Dataset, outdir: Path) -> dict[str, Path]:
    """Write the six inputs, two answer keys, and reproducibility metadata."""
    outdir.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_ARTIFACTS:
        legacy_path = outdir / name
        if legacy_path.is_file():
            legacy_path.unlink()
    paths = {
        "batch_config": outdir / "batch_config.csv",
        "pricing_rules": outdir / "pricing_rules.csv",
        "gateway": outdir / "gateway_ledger.csv",
        "details": outdir / "settlement_detail.csv",
        "summaries": outdir / "settlement_summary.csv",
        "bank": outdir / "bank_statement.csv",
        "cases": outdir / "answer_key_cases.csv",
        "allocations": outdir / "answer_key_allocations.csv",
        "meta": outdir / "dataset_meta.json",
    }

    _write_csv(
        paths["batch_config"],
        BATCH_CONFIG_FIELDS,
        [row.to_row() for row in dataset.batch_config],
    )
    _write_csv(
        paths["pricing_rules"],
        PRICING_RULE_FIELDS,
        [row.to_row() for row in dataset.pricing_rules],
    )
    _write_csv(paths["gateway"], GATEWAY_FIELDS, [row.to_row() for row in dataset.gateway])
    _write_csv(paths["details"], DETAIL_FIELDS, [row.to_row() for row in dataset.details])
    _write_csv(paths["summaries"], SUMMARY_FIELDS, [row.to_row() for row in dataset.summaries])
    _write_csv(paths["bank"], BANK_FIELDS, [row.to_row() for row in dataset.bank])
    _write_csv(paths["cases"], CASE_FIELDS, [row.to_row() for row in dataset.cases])
    _write_csv(
        paths["allocations"],
        ALLOCATION_FIELDS,
        [row.to_row() for row in dataset.allocations],
    )
    paths["meta"].write_text(
        json.dumps(dataset.config_meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths
