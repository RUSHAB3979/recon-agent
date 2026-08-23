"""Command-line generation exposes only knobs that define benchmark identity."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import Family, GenConfig
from .generator import generate
from .io import write_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a reconciliation benchmark batch.")
    parser.add_argument("--records", type=int, default=500, help="gateway events to generate")
    parser.add_argument("--seed", type=int, default=42, help="seed for every random draw")
    parser.add_argument(
        "--family",
        choices=[family.value for family in Family],
        default=Family.PRIMARY.value,
        help="case-prevalence family",
    )
    parser.add_argument("--out", type=Path, default=Path("data/dev"), help="output directory")
    # Kept because the existing Makefile uses it; it changes presentation only.
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = GenConfig(
        n_records=args.records,
        seed=args.seed,
        family=Family(args.family),
    )
    dataset = generate(config)
    paths = write_dataset(dataset, args.out)

    if not args.quiet:
        meta = dataset.config_meta
        print(
            f"seed={meta['seed']}  family={meta['family']}  "
            f"gateway_events={meta['n_gateway_events']}  cases={meta['n_cases']}  "
            f"settlements={meta['n_settlements']}  bank_rows={meta['n_bank_rows']}"
        )
        print("\nscenario mix by case:")
        total = len(dataset.cases)
        for name, count in dataset.summary().items():
            print(f"  {name:<26} {count:>5}  ({count / total:5.1%})")
        print(f"\nwrote {len(paths)} files to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
