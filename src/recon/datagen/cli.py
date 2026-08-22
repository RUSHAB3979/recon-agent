"""CLI: python -m recon.datagen.cli --records 500 --seed 42 --out data/dev"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import GenConfig
from .generator import generate
from .io import write_dataset


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate a synthetic reconciliation dataset.")
    ap.add_argument("--records", type=int, default=500, help="gateway transactions to generate")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed; the dataset is a pure function of it")
    ap.add_argument("--defect-rate", type=float, default=0.30, help="fraction of cases carrying a defect")
    ap.add_argument("--out", type=Path, default=Path("data/dev"), help="output directory")
    ap.add_argument("--prefix", type=str, default="", help="filename prefix")
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = GenConfig(n_records=args.records, seed=args.seed, defect_rate=args.defect_rate)
    dataset = generate(cfg)
    paths = write_dataset(dataset, args.out, prefix=args.prefix)

    if not args.quiet:
        meta = dataset.config_meta
        print(f"seed={meta['seed']}  gateway={meta['n_gateway_rows']}  "
              f"bank={meta['n_bank_rows']}  links={meta['n_links']}  "
              f"truth_pairs={meta['n_truth_pairs']}")
        print("\nscenario mix:")
        total = sum(dataset.summary().values())
        for name, count in dataset.summary().items():
            print(f"  {name:<24} {count:>5}  ({count / total:5.1%} of cases)")
        print("\nexpected resolutions:")
        res: dict[str, int] = {}
        for link in dataset.links:
            res[link.resolution.value] = res.get(link.resolution.value, 0) + 1
        for name, count in sorted(res.items()):
            print(f"  {name:<34} {count:>5}")
        print(f"\nwrote {len(paths)} files to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
