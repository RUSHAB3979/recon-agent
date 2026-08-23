"""Measure the recovery ambiguity the benchmark actually asks about.

Anonymous refund lines create a settlement-level unexplained refund delta. The
benchmark needs both the raw amount collision and the multiplicity left after
consumption, date, and lineage evidence: their difference proves whether those
gates are doing work instead of merely restating an already-unique amount.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

WINDOW_DAYS = 4


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def measure(data_dir: Path) -> dict[str, float | int | dict[int, int]]:
    """Keep legacy gated metrics while exposing the amount-only difficulty."""
    gateway = _read(data_dir / "gateway_ledger.csv")
    detail_rows = _read(data_dir / "settlement_detail.csv")
    summaries = _read(data_dir / "settlement_summary.csv")

    # Duplicate-export rows are not additional economic lines.
    unique_details: list[dict[str, str]] = []
    seen_detail_ids: set[str] = set()
    for row in detail_rows:
        if row["detail_id"] not in seen_detail_ids:
            seen_detail_ids.add(row["detail_id"])
            unique_details.append(row)

    details_by_settlement: dict[str, list[dict[str, str]]] = defaultdict(list)
    referenced_event_ids: set[str] = set()
    for row in unique_details:
        details_by_settlement[row["settlement_id"]].append(row)
        if row["event_id"]:
            referenced_event_ids.add(row["event_id"])

    payments_by_txn: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in gateway:
        if row["event_type"] == "PAYMENT":
            payments_by_txn[row["txn_id"]].append(row)

    amount_alone_multiplicities: list[int] = []
    after_gates_multiplicities: list[int] = []
    for summary in summaries:
        details = details_by_settlement[summary["settlement_id"]]
        identified_refund = -sum(
            int(row["gross_effect_paise"])
            for row in details
            if row["line_type"] == "REFUND" and row["event_id"]
        )
        delta = int(summary["refund_paise"]) - identified_refund
        if delta <= 0:
            continue

        settlement_date = date.fromisoformat(summary["settlement_date"])
        amount_candidates = [
            event
            for event in gateway
            if event["event_type"] == "REFUND"
            and int(event["amount_paise"]) == delta
        ]
        amount_alone_multiplicities.append(len(amount_candidates))

        candidates_after_gates = 0
        for event in amount_candidates:
            if event["event_id"] in referenced_event_ids:
                continue
            age = (settlement_date - datetime.fromisoformat(event["created_at"]).date()).days
            if not 0 <= age <= WINDOW_DAYS:
                continue
            parent_settled = any(
                parent["event_id"] in referenced_event_ids
                and parent["status"] == "PROCESSED"
                for parent in payments_by_txn[event["txn_id"]]
            )
            if parent_settled:
                candidates_after_gates += 1
        after_gates_multiplicities.append(candidates_after_gates)

    examined = len(after_gates_multiplicities)
    ambiguous = sum(count > 1 for count in after_gates_multiplicities)
    rate = ambiguous / examined if examined else 0.0
    amount_alone_ambiguous = sum(count > 1 for count in amount_alone_multiplicities)
    amount_alone_rate = amount_alone_ambiguous / examined if examined else 0.0
    amount_histogram = dict(sorted(Counter(amount_alone_multiplicities).items()))
    after_gates_histogram = dict(sorted(Counter(after_gates_multiplicities).items()))
    return {
        "examined": examined,
        "ambiguous": ambiguous,
        "ambiguity_rate": rate,
        "ambiguous_refund_rate": rate,
        "candidate_multiplicities": after_gates_histogram,
        "amount_alone_ambiguous": amount_alone_ambiguous,
        "amount_alone_ambiguity_rate": amount_alone_rate,
        "amount_alone_candidate_multiplicities": amount_histogram,
        "after_gates_candidate_multiplicities": after_gates_histogram,
        "gates_eliminated": sum(amount_alone_multiplicities)
        - sum(after_gates_multiplicities),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", type=Path, nargs="?", default=Path("data/dev"))
    args = parser.parse_args()
    result = measure(args.data_dir)
    print(f"{args.data_dir}:")
    print(f"  settlement refund deltas examined         {result['examined']:>6}")
    print(
        f"  ambiguous after gates 1, 3, and 5        {result['ambiguous']:>6}  "
        f"({result['ambiguous_refund_rate']:.1%})"
    )
    print(
        "  amount-alone candidate histogram          "
        f"{result['amount_alone_candidate_multiplicities']}"
    )
    print(
        "  after-gates candidate histogram           "
        f"{result['after_gates_candidate_multiplicities']}"
    )
    print(f"  candidates eliminated by gates            {result['gates_eliminated']:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
