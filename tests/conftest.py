import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recon.datagen import GenConfig, generate  # noqa: E402


SEEDS = [42, 7, 99, 2026]


@pytest.fixture(scope="session", params=SEEDS, ids=lambda s: f"seed{s}")
def dataset(request):
    """The dataset every structural test runs against.

    Parameterised over several seeds on purpose.  A single-seed fixture let a
    real labelling bug pass unnoticed once already: the defective case simply
    did not occur in seed 42.  Structural claims have to hold for every dataset
    the generator can produce, not one lucky draw.
    """
    return generate(GenConfig(n_records=500, seed=request.param))


@pytest.fixture(scope="session")
def by_scenario(dataset):
    """Links bucketed by scenario, with their records attached."""
    txns = {t.txn_id: t for t in dataset.gateway}
    credits = {c.utr: c for c in dataset.bank}
    buckets: dict[str, list[tuple]] = {}
    for link in dataset.links:
        buckets.setdefault(link.scenario.value, []).append(
            (link, [txns[t] for t in link.txn_ids], [credits[u] for u in link.utrs])
        )
    return buckets
