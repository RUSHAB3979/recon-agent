"""Stable public API shared by reconciliation agents and published baselines."""

from __future__ import annotations

from .score import (
    AgentOutput,
    AnswerKey,
    CaseDecision,
    ScoreReport,
    precision_coverage_curve,
    score,
)

__all__ = [
    "AnswerKey",
    "CaseDecision",
    "AgentOutput",
    "ScoreReport",
    "score",
    "precision_coverage_curve",
]
