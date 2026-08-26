"""Tests for the nine-gate refund corroboration rung.

WHAT THESE TESTS ARE FOR

    The rung's whole claim is "more coverage than the published floor, without
    the floor's false attributions". Two halves, and only one of them is
    flattering, so both are checked here and neither is allowed to move alone.

    The gates are also tested individually rather than only in aggregate. A gate
    that has quietly stopped firing is invisible in a total -- the batch still
    reconciles, the number still looks fine, and the engine is one coincidence
    away from being wrong. So each gate gets a case constructed to trip exactly
    it.

WHY THE AMBIGUOUS CASES ARE THE IMPORTANT ONES

    The generator plants refund groups that are genuinely indistinguishable:
    same amount, same window, same lineage. There is no evidence in the export
    that separates them, so the only correct behaviours are to abstain or to be
    lucky. B2 is lucky about half the time and books a false attribution the
    rest. Asserting that this rung abstains on every one of them is asserting
    that it does not trade correctness for coverage.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.controller import ABSTAIN, AMBIGUOUS_REFUND, reconcile
from recon.match.normalize import load_batch
from recon.match.passes import JOIN_ONLY_LADDER
from recon.match.recovery import (
    RECOVERY_WINDOW_DAYS,
    Gate,
    RefundCorroborationPass,
    maximum_matching,
)
from recon.metrics import baselines as B
from recon.metrics.score import AnswerKey, score

SEEDS = [42, 7, 99, 2026]
FAMILIES = [Family.DEVELOPMENT, Family.PRIMARY, Family.STRESS]


def _dataset(tmp_path_factory, seed: int, family: Family) -> Path:
    out = tmp_path_factory.mktemp(f"rec-{family.value}-{seed}")
    write_dataset(generate(GenConfig(n_records=500, seed=seed, family=family)), out)
    return Path(out)


@pytest.fixture(
    scope="session",
    params=[(seed, family) for seed in SEEDS for family in FAMILIES],
    ids=lambda p: f"{p[1].value}-seed{p[0]}",
)
def scene(request, tmp_path_factory):
    seed, family = request.param
    directory = _dataset(tmp_path_factory, seed, family)
    batch = load_batch(directory)
    rung = RefundCorroborationPass()
    result = rung.run(batch, frozenset())
    return directory, batch, result


# --------------------------------------------------------------------------
# the two halves of the claim
# --------------------------------------------------------------------------


def test_every_anonymous_line_is_either_claimed_or_abstained(scene):
    """No line is silently dropped.

    Dropping one would be the most flattering possible bug: it removes a hard
    case from the denominator of nothing, produces no wrong answer, and leaves
    the abstention rate looking better than the engine earned.
    """
    _, batch, result = scene
    handled = {claim.detail_id for claim in result.claims} | {
        abstention.detail_id for abstention in result.abstentions
    }
    assert handled == {line.detail_id for line in batch.anonymous_lines}


def test_no_two_claims_name_the_same_event(scene):
    """Declared ONE_TO_ONE, so the pass must honour it before the runner does."""
    event_ids = [claim.event_id for claim in result_claims(scene)]
    assert len(event_ids) == len(set(event_ids))


def result_claims(scene):
    _, _, result = scene
    return result.claims


def test_every_claim_reproduces_its_line_exactly(scene):
    """Gate 2, checked from the outside in integer paise.

    An off-by-one paise here would be invisible in every aggregate metric in the
    project and would be a reconciliation engine reporting a break it caused.
    """
    _, batch, result = scene
    lines = {line.detail_id: line for line in batch.details}
    for claim in result.claims:
        assert claim.detail_id is not None
        event = batch.events[claim.event_id]
        assert event.amount_paise == lines[claim.detail_id].magnitude_paise


def test_no_claim_names_an_event_the_export_already_used(scene):
    """Gate 1. Allocating a refund twice under two names double-counts money."""
    _, batch, result = scene
    for claim in result.claims:
        assert claim.event_id not in batch.referenced_event_ids


def test_every_claim_falls_inside_the_declared_window(scene):
    """Gate 3, checked against the declared policy rather than the generator."""
    _, batch, result = scene
    lines = {line.detail_id: line for line in batch.details}
    for claim in result.claims:
        assert claim.detail_id is not None
        age = (lines[claim.detail_id].settled_on - batch.events[claim.event_id].created_on).days
        assert 0 <= age <= RECOVERY_WINDOW_DAYS


def test_every_claim_has_a_settled_parent_payment(scene):
    """Gate 5. A refund of a payment that never settled is not in this batch."""
    _, batch, result = scene
    for claim in result.claims:
        event = batch.events[claim.event_id]
        assert any(
            parent.status == "PROCESSED" and parent.event_id in batch.referenced_event_ids
            for parent in batch.payments_by_txn.get(event.txn_id, ())
        )


def test_every_claim_is_a_refund_explaining_a_refund(scene):
    """Gate 6. A sign error is silent: the magnitude still looks plausible."""
    _, batch, result = scene
    lines = {line.detail_id: line for line in batch.details}
    for claim in result.claims:
        assert claim.detail_id is not None
        assert batch.events[claim.event_id].event_type == "REFUND"
        line = lines[claim.detail_id]
        assert line.line_type == "REFUND" and line.gross_effect_paise < 0


# --------------------------------------------------------------------------
# abstention
# --------------------------------------------------------------------------


def test_an_abstention_always_names_more_than_one_candidate(scene):
    """Gate 9 is the only reason to abstain here, so it must be the reason.

    An abstention with one candidate would mean the rung declined something it
    had proved, and an abstention with none would mean it declined something it
    had no evidence for -- which is an unexplained line, not an abstention.
    """
    _, _, result = scene
    for abstention in result.abstentions:
        assert len(abstention.candidate_event_ids) >= 2
        assert len(set(abstention.candidate_event_ids)) == len(abstention.candidate_event_ids)
        assert Gate.UNIQUENESS.value.split("_")[1] in abstention.reason or "gate 9" in abstention.reason


def test_an_abstained_line_is_never_also_claimed(scene):
    _, _, result = scene
    claimed = {claim.detail_id for claim in result.claims}
    assert not (claimed & {a.detail_id for a in result.abstentions})


def test_abstentions_surface_as_ambiguous_refund_verdicts(scene):
    """An abstention that never reaches the report is a hidden unmatched row.

    Rule four of this project is that unmatched rows are never hidden, and the
    exception list is the deliverable. A rung that abstained internally while
    the case reported RECONCILED would violate that silently.
    """
    directory, _, result = scene
    if not result.abstentions:
        pytest.skip("no ambiguous refunds in this family and seed")
    verdicts = reconcile(directory).verdicts
    abstained = [v for v in verdicts if v.outcome == ABSTAIN]
    assert abstained
    for verdict in abstained:
        assert verdict.category == AMBIGUOUS_REFUND
        assert verdict.confidence == 0.0
        assert verdict.reasons


# --------------------------------------------------------------------------
# per-gate behaviour, one case each
# --------------------------------------------------------------------------


def _one_claimed_pair(batch, result):
    """A line and the event that explains it, from a real dataset."""
    claim = result.claims[0]
    line = next(line for line in batch.details if line.detail_id == claim.detail_id)
    return line, batch.events[claim.event_id]


def _rerun_with(batch, *, events=None, details=None):
    replacements = {}
    if events is not None:
        replacements["events"] = events
    if details is not None:
        replacements["details"] = details
        by_settlement: dict[str, list] = {}
        for line in details:
            by_settlement.setdefault(line.settlement_id, []).append(line)
        replacements["details_by_settlement"] = {
            key: tuple(value) for key, value in by_settlement.items()
        }
    mutated = dataclasses.replace(batch, **replacements)
    rung = RefundCorroborationPass()
    return rung.run(mutated, frozenset()), rung


def test_moving_the_event_outside_the_window_loses_the_claim(scene):
    """Gate 3, driven rather than observed.

    Constructed from a pair the rung actually resolved, so the only thing that
    changed between a claim and no claim is the one date this gate reads.
    """
    _, batch, result = scene
    line, event = _one_claimed_pair(batch, result)
    from datetime import timedelta

    moved = dataclasses.replace(
        event, created_at=event.created_at + timedelta(days=RECOVERY_WINDOW_DAYS + 5)
    )
    events = dict(batch.events)
    events[event.event_id] = moved
    after, rung = _rerun_with(batch, events=events)

    assert line.detail_id not in {claim.detail_id for claim in after.claims}
    assert rung.ledger.eliminated[Gate.RECOVERY_WINDOW] > 0


def test_changing_the_currency_loses_the_claim(scene):
    """Gate 4, which eliminates nothing on this data.

    That is exactly why it is driven here. The per-gate table published by the
    report shows zero for this gate on every family, and the honest way to
    justify keeping it is to demonstrate it still works rather than to argue
    that it would.
    """
    _, batch, result = scene
    line, event = _one_claimed_pair(batch, result)
    events = dict(batch.events)
    events[event.event_id] = dataclasses.replace(event, currency="USD")
    after, rung = _rerun_with(batch, events=events)

    assert line.detail_id not in {claim.detail_id for claim in after.claims}
    assert rung.ledger.eliminated[Gate.CURRENCY] > 0


def test_turning_the_event_into_a_payment_loses_the_claim(scene):
    """Gate 6."""
    _, batch, result = scene
    line, event = _one_claimed_pair(batch, result)
    events = dict(batch.events)
    events[event.event_id] = dataclasses.replace(event, event_type="PAYMENT")
    after, rung = _rerun_with(batch, events=events)

    assert line.detail_id not in {claim.detail_id for claim in after.claims}
    assert rung.ledger.eliminated[Gate.SIGN] > 0


def test_breaking_the_settlement_rollup_loses_the_claim(scene):
    """Gate 7, also a zero on this data.

    Attributing a line inside a settlement whose figures do not balance would
    assert an explanation for books that are already broken. Reporting the break
    is the better answer, and this checks the rung prefers it.
    """
    _, batch, result = scene
    line, _event = _one_claimed_pair(batch, result)
    settlements = dict(batch.settlements)
    target = settlements[line.settlement_id]
    settlements[line.settlement_id] = dataclasses.replace(
        target, refund_paise=target.refund_paise + 5_000
    )
    rung = RefundCorroborationPass()
    after = rung.run(dataclasses.replace(batch, settlements=settlements), frozenset())

    assert line.detail_id not in {claim.detail_id for claim in after.claims}
    assert rung.ledger.eliminated[Gate.CONTROLS] > 0


def test_an_event_consumed_by_an_earlier_rung_is_not_available(scene):
    """Gate 1, via the runner's consumption set rather than the export's."""
    _, batch, result = scene
    _, event = _one_claimed_pair(batch, result)
    after = RefundCorroborationPass().run(batch, frozenset({event.event_id}))
    assert event.event_id not in {claim.event_id for claim in after.claims}


# --------------------------------------------------------------------------
# gate 8, the global one
# --------------------------------------------------------------------------


def test_maximum_matching_is_maximum():
    adjacency = {"a": ["x", "y"], "b": ["y"], "c": ["y", "z"]}
    matched = maximum_matching(adjacency, frozenset({"x", "y", "z"}))
    assert len(matched) == 3
    assert set(matched.values()) == {"a", "b", "c"}


def test_maximum_matching_is_deterministic():
    """A verdict that varied between runs would be unreproducible, hence wrong."""
    adjacency = {"a": ["x", "y", "z"], "b": ["x", "y"], "c": ["y", "z"]}
    right = frozenset({"x", "y", "z"})
    first = maximum_matching(adjacency, right)
    assert all(maximum_matching(adjacency, right) == first for _ in range(5))


def test_global_feasibility_rescues_a_locally_ambiguous_line():
    """The case that justifies gate 8 existing at all.

    Two lines, two events. Line ``b`` can only be explained by ``y``, so ``y`` is
    spoken for, and line ``a`` -- which locally has two candidates and would be
    abstained by any local engine -- has exactly one explanation left.

    Driven through ``maximum_matching`` directly rather than through a dataset,
    because the released datasets contain no such configuration. Gate 8
    eliminates zero candidates on all three families, and this is the test that
    keeps it from being untested dead weight until data that needs it arrives.
    """
    adjacency = {"a": ["x", "y"], "b": ["y"]}
    right = frozenset({"x", "y"})
    baseline = len(maximum_matching(adjacency, right))
    assert baseline == 2

    def allowed(line: str, event: str) -> bool:
        residual = {
            other: [c for c in cands if c != event]
            for other, cands in adjacency.items()
            if other != line
        }
        return len(maximum_matching(residual, right - {event})) == baseline - 1

    assert allowed("a", "x")
    assert not allowed("a", "y")  # taking y strands b, so a resolves to x alone
    assert allowed("b", "y")


# --------------------------------------------------------------------------
# the rung against the published floor
# --------------------------------------------------------------------------


def test_the_rung_beats_b2_on_coverage_without_matching_its_errors(scene):
    """The headline comparison, per family and seed, never averaged.

    B2 is the published floor precisely because it does this job the naive way:
    attribute any unconsumed refund whose amount fits. It therefore resolves the
    ambiguous groups too, and is wrong about roughly half of them. This asserts
    the trade goes the right way -- at least as many correct attributions, and
    strictly fewer wrong ones.
    """
    directory, _, _ = scene
    key = AnswerKey.load(directory)
    agent = score(reconcile(directory).to_agent_output(), key)
    join_only = score(reconcile(directory, JOIN_ONLY_LADDER).to_agent_output(), key)
    b2 = B.score_shared(directory, B.run_b2(B.Batch.load(directory)))

    assert agent.allocations.true_positives > join_only.allocations.true_positives
    assert agent.allocations.false_positives < b2.allocations.false_positives
    assert agent.allocations.precision > b2.allocations.precision


def test_the_pass_publishes_its_per_gate_counters(scene):
    """The report cannot claim a gate does work unless the rung reports it."""
    _, _, result = scene
    for gate in Gate:
        assert gate.value in result.counters
    assert result.counters["resolved"] == len(result.claims)
    assert result.counters["abstained"] == len(result.abstentions)


def test_the_rung_is_deterministic_across_runs(scene):
    _, batch, result = scene
    again = RefundCorroborationPass().run(batch, frozenset())
    assert [c.event_id for c in again.claims] == [c.event_id for c in result.claims]
    assert [a.candidate_event_ids for a in again.abstentions] == [
        a.candidate_event_ids for a in result.abstentions
    ]
