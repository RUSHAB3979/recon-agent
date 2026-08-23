from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from recon.match.audit import (
    AuditLog,
    Decision,
    DuplicateDecisionError,
    NonOverridableDecisionError,
    Override,
    OverrideSet,
    UnknownDecisionError,
    summarise,
)


NOW = datetime(2025, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)


def make_decision(
    *,
    stage: str = "S3",
    subject: str | tuple[str, ...] = "UTR-100",
    action: str = "match",
    amount: Decimal = Decimal("125.40"),
    confidence: float | None = 0.91,
    overridable: bool = True,
) -> Decision:
    return Decision(
        stage=stage,
        subject=subject,
        action=action,
        inputs={"amount": amount, "candidates": ["TXN-7", "TXN-8"]},
        rule="exact_amount_and_reference",
        result={"txn_id": "TXN-7", "settled_amount": amount},
        confidence=confidence,
        reasoning="The normalized reference and amount agree.",
        timestamp=NOW,
        overridable=overridable,
    )


def make_override(decision: Decision, new_action: str = "exception") -> Override:
    return Override(
        decision_id=decision.decision_id,
        new_action=new_action,
        reason="Bank evidence contradicts the automatic match.",
        operator="analyst@example.com",
        timestamp="2025-01-02T04:00:00Z",
    )


def test_decision_round_trips_decimal_and_confidence_exactly(tmp_path) -> None:
    decision = make_decision(confidence=0.1)
    log = AuditLog([decision])
    path = tmp_path / "audit.jsonl"
    second_path = tmp_path / "audit-second-run.jsonl"

    log.write(path)
    log.write(second_path)
    raw_record = json.loads(path.read_text(encoding="utf-8"))
    restored = AuditLog.read(path)[0]

    assert path.read_bytes() == second_path.read_bytes()
    assert raw_record["inputs"]["amount"] == "125.40"
    assert raw_record["result"]["settled_amount"] == "125.40"
    assert isinstance(restored.inputs["amount"], Decimal)
    assert restored.inputs["amount"] == Decimal("125.40")
    assert restored.result["settled_amount"] == Decimal("125.40")
    assert restored.confidence is not None
    assert restored.confidence.hex() == decision.confidence.hex()
    assert restored == decision


def test_decision_id_is_stable_and_changes_with_identity_content() -> None:
    first_run = make_decision(subject=("UTR-2", "TXN-1"))
    second_run = make_decision(subject=("TXN-1", "UTR-2"))
    different = make_decision(subject="UTR-3")

    assert first_run.decision_id == second_run.decision_id
    assert first_run.decision_id != different.decision_id


def test_append_does_not_allow_earlier_entries_to_be_mutated() -> None:
    mutable_inputs = {"amount": Decimal("10.00"), "references": ["A"]}
    first = Decision(
        stage="S1",
        subject="UTR-1",
        action="no_action",
        inputs=mutable_inputs,
        rule="normalise",
        result={"normalised": "UTR-1"},
        confidence=None,
        reasoning="Normalization is deterministic.",
        timestamp=NOW,
        overridable=False,
    )
    log = AuditLog([first])

    mutable_inputs["amount"] = Decimal("999.00")
    mutable_inputs["references"].append("B")

    assert log[0].inputs["amount"] == Decimal("10.00")
    assert log[0].inputs["references"] == ("A",)
    with pytest.raises(TypeError):
        log[0].inputs["amount"] = Decimal("20.00")
    with pytest.raises(DuplicateDecisionError):
        log.append(replace(first, reasoning="Attempted replacement."))


def test_overriding_non_overridable_decision_raises() -> None:
    decision = make_decision(overridable=False)

    with pytest.raises(NonOverridableDecisionError, match="not overridable"):
        OverrideSet([make_override(decision)]).apply(AuditLog([decision]))


def test_overriding_unknown_decision_id_raises() -> None:
    override = Override(
        decision_id="dec_missing",
        new_action="exception",
        reason="Manual investigation found a mismatch.",
        operator="analyst@example.com",
        timestamp=NOW,
    )

    with pytest.raises(UnknownDecisionError, match="unknown decision_id"):
        OverrideSet([override]).apply(AuditLog())


def test_effective_decision_retains_original_and_override_provenance() -> None:
    original = make_decision(action="match")
    override = make_override(original, new_action="abstain")
    log = AuditLog([original])

    effective = OverrideSet([override]).apply(log).get(original.decision_id)

    assert effective.action == "abstain"
    assert effective.original is original
    assert effective.original_action == "match"
    assert effective.original.action == "match"
    assert effective.overridden is True
    assert effective.overridden_by == "analyst@example.com"
    assert effective.override_reason == override.reason
    assert effective.override_timestamp == override.timestamp
    assert log.get(original.decision_id).action == "match"


def test_summarise_counts_original_and_effective_actions() -> None:
    matched = make_decision(
        stage="S3", subject="UTR-1", action="match", confidence=0.95
    )
    abstained = make_decision(
        stage="S4", subject="UTR-2", action="abstain", confidence=0.65
    )
    deterministic = make_decision(
        stage="S0", subject="UTR-3", action="no_action", confidence=None
    )
    log = AuditLog([matched, abstained, deterministic])

    without_override = summarise(log, OverrideSet())
    with_override = summarise(log, OverrideSet([make_override(matched)]))

    assert without_override == {
        "by_stage": {"S0": 1, "S3": 1, "S4": 1},
        "by_action": {"abstain": 1, "match": 1, "no_action": 1},
        "by_confidence_band": {"deterministic": 1, "medium": 1, "high": 1},
        "overrides_applied": 0,
    }
    assert with_override == {
        "by_stage": {"S0": 1, "S3": 1, "S4": 1},
        "by_action": {"abstain": 1, "exception": 1, "no_action": 1},
        "by_confidence_band": {"deterministic": 1, "medium": 1, "high": 1},
        "overrides_applied": 1,
    }
