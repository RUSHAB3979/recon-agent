"""Synthetic benchmark generation with constructive ground truth."""

from .config import Family, GenConfig, Resolution, Scenario
from .entities import round_half_up
from .generator import Generator, generate
from .io import write_dataset

__all__ = [
    "Family",
    "GenConfig",
    "Resolution",
    "Scenario",
    "Generator",
    "generate",
    "round_half_up",
    "write_dataset",
]
