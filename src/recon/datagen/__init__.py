"""Synthetic dataset generation with ground truth."""

from .config import AmountBasis, GenConfig, Resolution, Scenario
from .generator import Generator, generate
from .io import write_dataset

__all__ = [
    "AmountBasis", "GenConfig", "Resolution", "Scenario",
    "Generator", "generate", "write_dataset",
]
