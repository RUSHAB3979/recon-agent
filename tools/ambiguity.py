"""Measure the intrinsic ambiguity floor of a generated dataset.

This is the ceiling on what amount+date matching alone can achieve.  Any credit
with more than one indistinguishable gateway candidate CANNOT be resolved by
arithmetic -- narration evidence is mandatory for those, which is the honest
justification for having an LLM in the pipeline at all.

Reporting this number alongside the match rate is what separates "our matcher
scored 97%" from "our matcher scored 97% against a 91% deterministic ceiling".
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

TOLERANCE_PAISE = 5
WINDOW_DAYS = 4


def measure(data_dir: Path) -> dict[str, float | int]:
    gateway = list(csv.DictReader((data_dir / "gateway_ledger.csv").open()))
    bank = {r["utr"]: r for r in csv.DictReader((data_dir / "bank_settlement.csv").open())}
    links = list(csv.DictReader((data_dir / "answer_key_links.csv").open()))

    by_amount: dict[Decimal, list[date]] = defaultdict(list)
    for r in gateway:
        if r["currency"] != "INR":
            continue
        by_amount[Decimal(r["net_amount"])].append(
            datetime.fromisoformat(r["created_at"]).date()
        )

    deltas = [Decimal(d) / 100 for d in range(-TOLERANCE_PAISE, TOLERANCE_PAISE + 1)]
    examined = ambiguous = 0

    for link in links:
        if link["n_credits"] != "1" or link["n_txns"] != "1":
            continue
        credit = bank[link["utrs"]]
        amount = Decimal(credit["credit_amount"])
        settled = date.fromisoformat(credit["settlement_date"])

        candidates = sum(
            1
            for d in deltas
            for created in by_amount.get(amount + d, [])
            if 0 <= (settled - created).days <= WINDOW_DAYS
        )
        examined += 1
        ambiguous += candidates > 1

    return {
        "examined": examined,
        "ambiguous": ambiguous,
        "ambiguity_rate": ambiguous / examined if examined else 0.0,
        "deterministic_ceiling": 1 - (ambiguous / examined) if examined else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path, nargs="?", default=Path("data/dev"))
    args = ap.parse_args()
    m = measure(args.data_dir)
    print(f"{args.data_dir}:")
    print(f"  1:1 settled credits examined              {m['examined']:>6}")
    print(f"  ambiguous on amount+date alone            {m['ambiguous']:>6}  ({m['ambiguity_rate']:.1%})")
    print(f"  deterministic ceiling (amount+date only)  {m['deterministic_ceiling']:>6.1%}")
    print(f"  -> narration evidence is mandatory for {m['ambiguous']} credits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
