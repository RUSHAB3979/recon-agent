import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recon.datagen import Family, GenConfig, generate  # noqa: E402


SEEDS = [42, 7, 99, 2026]


@pytest.fixture(scope="session", params=SEEDS, ids=lambda seed: f"seed{seed}")
def dataset(request):
    """Primary structural claims must survive more than one lucky random draw."""
    return generate(
        GenConfig(n_records=500, seed=request.param, family=Family.PRIMARY)
    )


@pytest.fixture(scope="session", params=SEEDS, ids=lambda seed: f"stress-seed{seed}")
def stress_dataset(request):
    """Stress-only classes receive the same four-seed integrity treatment."""
    return generate(
        GenConfig(n_records=500, seed=request.param, family=Family.STRESS)
    )
