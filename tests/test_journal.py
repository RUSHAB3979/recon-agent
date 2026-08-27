"""What the decision journal must guarantee.

The audit module's own tests prove the chain detects tampering. These prove
something different and, for project rule 5, more important: that the pipeline
actually EMITS decisions, that it emits one for every decision it took, and
that the ones it emits carry enough to re-check by hand.

A module that can log while the pipeline never does is a library, not an audit
trail, and that gap is exactly what these tests close.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.audit import AuditChainError, AuditLog, summarise
from recon.match.controller import reconcile
from recon.match.journal import (
    DISPOSITION_STAGE,
    STAGE_BY_PASS,
    UnknownStageError,
    build_journal,
    stage_for_pass,
    write_journal,
)
from recon.match.passes import Abstention, Claim, ClaimTier, PassResult


PINNED = datetime(2026, 8, 27, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def directory(tmp_path_factory) -> Path:
    """One development batch. Dev is the only surface anything may be tuned on."""

    out = tmp_path_factory.mktemp("journal")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


@pytest.fixture(scope="module")
def run(directory):
    return reconcile(directory)


@pytest.fixture(scope="module")
def log(run) -> AuditLog:
    return build_journal(run, built_at=PINNED)


# --------------------------------------------------------------------------
# coverage: one record per decision, none missing
# --------------------------------------------------------------------------


def test_every_accepted_claim_is_logged(run, log):
    """An attribution nobody can look up afterwards is not auditable."""

    logged = {
        tuple(decision.subject)
        for decision in log
        if decision.action == "match" and decision.stage != DISPOSITION_STAGE
    }
    # A multi-item subject is a SET in the audit vocabulary -- sorted and
    # deduplicated -- so identity does not depend on the order the journal
    # happened to write the three ids in. The expectation is built the same way.
    expected = {
        tuple(sorted({claim.settlement_id, claim.detail_id or "-", claim.event_id}))
        for claim in run.ladder.accepted
    }
    assert logged == expected
    assert expected, "the dev batch must produce attributions for this to mean anything"


def test_every_abstention_is_logged(run, log):
    """Rule 4 of the project: never hide an unmatched row."""

    logged = {
        tuple(decision.subject)
        for decision in log
        if decision.action == "abstain" and decision.stage != DISPOSITION_STAGE
    }
    expected = {
        tuple(sorted({abstention.settlement_id, abstention.detail_id}))
        for result in run.ladder.per_pass
        for abstention in result.abstentions
    }
    assert logged == expected
    assert expected, "the dev residual is non-empty; an empty set would prove nothing"


def test_every_case_gets_exactly_one_disposition(run, log):
    dispositions = [
        decision for decision in log if decision.stage == DISPOSITION_STAGE
    ]
    assert len(dispositions) == len(run.verdicts)
    assert {decision.subject for decision in dispositions} == {
        verdict.case_id for verdict in run.verdicts
    }


def test_record_count_is_the_sum_of_its_parts(run, log):
    claims = sum(len(result.claims) for result in run.ladder.per_pass)
    abstentions = sum(len(result.abstentions) for result in run.ladder.per_pass)
    expected = claims + abstentions + len(run.ladder.rejected) + len(run.verdicts)
    assert len(log) == expected


# --------------------------------------------------------------------------
# content: enough to re-check the decision by hand
# --------------------------------------------------------------------------


def test_an_attribution_carries_both_amounts(log):
    """The evidence, not just the verdict.

    A record saying "matched" without the two numbers that were compared cannot
    be re-checked by an operator, which is the only reason the log exists.
    """

    matches = [
        decision
        for decision in log
        if decision.action == "match" and decision.stage != DISPOSITION_STAGE
    ]
    assert matches
    for decision in matches:
        assert "event_amount_paise" in decision.inputs
        assert isinstance(decision.inputs["event_amount_paise"], int)
        assert decision.reasoning


def test_an_abstention_carries_the_shortlist_it_could_not_separate(log):
    abstentions = [
        decision
        for decision in log
        if decision.action == "abstain" and decision.stage != DISPOSITION_STAGE
    ]
    assert abstentions
    for decision in abstentions:
        candidates = decision.inputs["candidate_event_ids"]
        assert len(candidates) >= 2, "abstaining with one candidate would be a bug"
        assert decision.result["candidates"] == candidates
        assert decision.confidence == 0.0


def test_no_record_has_empty_reasoning(log):
    assert all(decision.reasoning for decision in log)


def test_a_disposition_records_outcome_and_category(run, log):
    by_case = {
        decision.subject: decision
        for decision in log
        if decision.stage == DISPOSITION_STAGE
    }
    for verdict in run.verdicts:
        decision = by_case[verdict.case_id]
        assert decision.result["outcome"] == verdict.outcome
        assert decision.result["category"] == verdict.category
        assert decision.inputs["allocation_count"] == len(verdict.allocations)


def test_abstained_cases_are_logged_as_abstentions_not_matches(run, log):
    by_case = {
        decision.subject: decision
        for decision in log
        if decision.stage == DISPOSITION_STAGE
    }
    abstained = [v for v in run.verdicts if v.outcome == "ABSTAIN"]
    assert abstained, "the dev batch abstains; this test needs at least one"
    for verdict in abstained:
        assert by_case[verdict.case_id].action == "abstain"


# --------------------------------------------------------------------------
# overridability
# --------------------------------------------------------------------------


def test_attributions_abstentions_and_dispositions_are_overridable(log):
    """Rule 5: every decision must be human-overridable."""

    for decision in log:
        if decision.rule == "runner/consumption":
            continue
        assert decision.overridable, decision.rule


def test_a_runner_rejection_is_not_overridable():
    """Reinstating a rejected claim would allocate one event twice.

    The runner refuses a claim precisely because the event or the line is
    already spoken for. An override layer able to undo that could break the one
    invariant the runner exists to hold, so this decision is sealed as final.
    """

    from recon.match.journal import _rejection_decision

    claim = Claim(
        settlement_id="setl_1",
        event_id="evt_1",
        detail_id="dtl_1",
        pass_name="exact_join",
        tier=ClaimTier.CONFIRMED,
        confidence=1.0,
        reasons=("proposed second",),
    )
    decision = _rejection_decision(claim, "event evt_1 already consumed", PINNED)
    assert decision.overridable is False
    assert decision.action == "no_action"
    assert "already consumed" in decision.reasoning


# --------------------------------------------------------------------------
# stage mapping
# --------------------------------------------------------------------------


def test_every_shipped_rung_has_a_stage(run):
    for name in run.per_pass_names:
        assert name in STAGE_BY_PASS


def test_an_unmapped_pass_raises_rather_than_guessing():
    """Silently filing a new rung under the wrong stage corrupts every count."""

    with pytest.raises(UnknownStageError):
        stage_for_pass("some_rung_added_later")


def test_a_journal_over_an_unmapped_rung_fails_loudly(run):
    unmapped = PassResult(pass_name="not_in_the_map", examined=1)
    unmapped.abstentions.append(
        Abstention(
            settlement_id="setl_1",
            detail_id="dtl_1",
            pass_name="not_in_the_map",
            candidate_event_ids=("evt_1", "evt_2"),
            reason="two candidates",
        )
    )
    doctored = replace(
        run, ladder=replace(run.ladder, per_pass=run.ladder.per_pass + (unmapped,))
    )
    with pytest.raises(UnknownStageError):
        build_journal(doctored, built_at=PINNED)


# --------------------------------------------------------------------------
# the chain, end to end
# --------------------------------------------------------------------------


def test_the_written_log_verifies_on_read(run, tmp_path):
    path = tmp_path / "runs" / "dev" / "audit.jsonl"
    written = write_journal(run, path, built_at=PINNED)
    assert path.exists()
    reloaded = AuditLog.read(path)
    assert reloaded.head_hash == written.head_hash
    assert len(reloaded) == len(written)


def test_editing_one_record_breaks_the_chain(run, tmp_path):
    """The whole point of sealing the complete record, exercised on real output."""

    path = tmp_path / "audit.jsonl"
    write_journal(run, path, built_at=PINNED)
    lines = path.read_text(encoding="utf-8").splitlines()
    target = next(
        index for index, line in enumerate(lines) if '"action":"match"' in line
    )
    lines[target] = lines[target].replace('"overridable":true', '"overridable":false')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AuditChainError):
        AuditLog.read(path)


def test_the_same_run_at_the_same_instant_seals_identically(directory):
    """Determinism given a fixed clock, so a diff means the decisions changed."""

    first = build_journal(reconcile(directory), built_at=PINNED)
    second = build_journal(reconcile(directory), built_at=PINNED)
    assert first.head_hash == second.head_hash


def test_a_later_run_seals_differently(run):
    """A log that hashed the same yesterday and today is a checksum, not a record."""

    earlier = build_journal(run, built_at=PINNED)
    later = build_journal(
        run, built_at=datetime(2026, 8, 28, 9, 30, tzinfo=timezone.utc)
    )
    assert earlier.head_hash != later.head_hash


def test_the_summary_counts_every_stage_the_ladder_used(run, log):
    summary = summarise(log)
    for name in run.per_pass_names:
        assert summary["by_stage"].get(STAGE_BY_PASS[name], 0) > 0
    assert summary["by_stage"][DISPOSITION_STAGE] == len(run.verdicts)
    assert sum(summary["by_action"].values()) == len(log)


# --------------------------------------------------------------------------
# the CLI that gates the build
# --------------------------------------------------------------------------


def test_the_verifier_expands_a_directory(run, tmp_path, capsys):
    from recon.match.audit import main

    write_journal(run, tmp_path / "dev" / "audit.jsonl", built_at=PINNED)
    write_journal(run, tmp_path / "primary" / "audit.jsonl", built_at=PINNED)
    assert main([str(tmp_path)]) == 0
    assert capsys.readouterr().out.count("OK") == 2


def test_the_verifier_fails_when_there_is_nothing_to_verify(tmp_path, capsys):
    """A check that passes over an empty set is not a check.

    This is the bug it guards against: `make verify-audit` once printed a
    reassuring message and exited zero because its file list had been expanded
    before the run that produced the files.
    """

    from recon.match.audit import main

    assert main([str(tmp_path / "absent")]) == 1
    assert "no audit logs found" in capsys.readouterr().out
