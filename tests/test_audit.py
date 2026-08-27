from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from recon.match.audit import (
    GENESIS_HASH,
    AuditChainError,
    AuditLog,
    Decision,
    DuplicateDecisionError,
    NonOverridableDecisionError,
    Override,
    OverrideSet,
    UnknownDecisionError,
    main,
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


# --------------------------------------------------------------------------
# the tamper-evident chain
#
# decision_id is an identity and covers stage/subject/action only. record_hash
# is a seal and covers everything. These tests exist because the difference is
# the whole point: a log whose seal ignored the amount, the rule or the
# reasoning would validate happily after someone rewrote them.
# --------------------------------------------------------------------------


def _rewrite(path, line_number, mutate):
    """Rewrite one JSONL record in place, leaving every other byte untouched."""

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[line_number])
    mutate(record)
    lines[line_number] = json.dumps(
        record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _three_line_log(tmp_path):
    log = AuditLog(
        [
            make_decision(subject="UTR-100"),
            make_decision(subject="UTR-200", amount=Decimal("42.00")),
            make_decision(subject="UTR-300", action="abstain", confidence=None),
        ]
    )
    path = tmp_path / "audit.jsonl"
    log.write(path)
    return log, path


def test_chain_verifies_on_an_untouched_log(tmp_path) -> None:
    log, path = _three_line_log(tmp_path)
    reloaded = AuditLog.read(path)
    assert [d.decision_id for d in reloaded] == [d.decision_id for d in log]
    assert reloaded.head_hash == log.head_hash


def test_first_record_chains_to_genesis(tmp_path) -> None:
    _, path = _three_line_log(tmp_path)
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["previous_hash"] == GENESIS_HASH


def test_head_hash_of_an_empty_log_is_genesis() -> None:
    assert AuditLog().head_hash == GENESIS_HASH


def test_chain_links_each_record_to_its_predecessor(tmp_path) -> None:
    log, path = _three_line_log(tmp_path)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["record_hash"] for r in records] == list(log.chain())
    for earlier, later in zip(records, records[1:]):
        assert later["previous_hash"] == earlier["record_hash"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("rule", "something_else_entirely"),
        ("reasoning", "A different story about the same decision."),
        ("confidence", 0.99),
        ("overridable", False),
        ("timestamp", "2030-01-01T00:00:00+00:00"),
    ],
)
def test_editing_any_sealed_field_breaks_the_chain(tmp_path, field, value) -> None:
    """The fields decision_id does NOT cover are exactly the ones at risk.

    None of these change stage, subject or action, so the identity hash is
    unmoved and the old integrity check passed every one of them.
    """

    _, path = _three_line_log(tmp_path)
    _rewrite(path, 1, lambda record: record.__setitem__(field, value))
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_editing_a_nested_amount_breaks_the_chain(tmp_path) -> None:
    _, path = _three_line_log(tmp_path)

    def inflate(record):
        record["inputs"]["amount"] = "999999.00"
        record["result"]["settled_amount"] = "999999.00"

    _rewrite(path, 1, inflate)
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_deleting_a_record_breaks_the_chain(tmp_path) -> None:
    _, path = _three_line_log(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_reordering_records_breaks_the_chain(tmp_path) -> None:
    _, path = _three_line_log(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8"
    )
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_resealing_only_the_edited_record_still_breaks_downstream(tmp_path) -> None:
    """A tamperer who recomputes one hash does not get away with it.

    This is what chaining buys over per-record hashing: the edited record can be
    made self-consistent, but every record after it still points at the old one.
    """

    from recon.match.audit import _record_hash

    _, path = _three_line_log(tmp_path)

    def edit_and_reseal(record):
        record["rule"] = "quietly_changed"
        record["record_hash"] = _record_hash(record["previous_hash"], record)

    _rewrite(path, 0, edit_and_reseal)
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_stripping_the_chain_fields_is_refused_by_default(tmp_path) -> None:
    _, path = _three_line_log(tmp_path)

    def strip(record):
        record.pop("record_hash")
        record.pop("previous_hash")

    _rewrite(path, 1, strip)
    with pytest.raises(AuditChainError):
        AuditLog.read(path)
    # ...but an explicitly unverified read still parses it, which is the escape
    # hatch for logs written before the chain existed.
    assert len(AuditLog.read(path, verify=False)) == 3


def test_decision_id_stays_an_identity_and_ignores_the_seal(tmp_path) -> None:
    """Two decisions differing only in reasoning share an id but not a seal."""

    first = make_decision()
    second = replace(first, reasoning="Reworded, same verdict.")
    assert first.decision_id == second.decision_id
    assert AuditLog([first]).head_hash != AuditLog([second]).head_hash


def test_cli_reports_ok_then_fails_after_tampering(tmp_path, capsys) -> None:
    _, path = _three_line_log(tmp_path)
    assert main([str(path)]) == 0
    assert "OK" in capsys.readouterr().out

    _rewrite(path, 2, lambda record: record.__setitem__("rule", "tampered"))
    assert main([str(path)]) == 1
    assert "FAIL" in capsys.readouterr().out
