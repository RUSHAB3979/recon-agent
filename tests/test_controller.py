"""Tests for the agent controller, and for the Phase B gate.

THE GATE

    A ladder holding only the exact-join rung must reproduce B1 exactly. Not
    approximately, not on average -- the same number of correct cases, and the
    same true positives, false positives and false negatives on allocations.

    That is a strong test precisely because it is a coincidence that has to be
    engineered. B1 is written against raw CSV dictionaries with its own rounding
    function and its own ordering; the controller is written against typed
    records from the normalizer, with a separately implemented rounding function
    and its own ordering. Two independent implementations landing on an
    identical verdict for every case in the batch is evidence that both are
    right. One deviation is a bug with a known answer, which is the cheapest
    kind of bug to own.

    It also pins the honest starting line. Every point the agent scores above
    this figure has to come from a rung added later and shows up in the per-pass
    yield table, so the headline number can never quietly absorb credit for work
    the published floor already does.

CONSUMPTION

    The runner, not the pass, decides which claims survive. The tests below use
    a deliberately greedy fake pass to check that a second claim on an
    already-consumed event is rejected rather than double-allocated, because
    that failure is invisible in aggregate metrics -- it inflates recall and
    precision together.

THE BOUNDARY

    ``test_reconcile_never_opens_an_answer_file`` records every file the agent
    opens during a full run and asserts the answer key is not among them, apart
    from the case partition, which is loaded through the loader that cannot
    return answers. This is the test that makes every published number
    meaningful; without it, "the agent does not read the answers" is an
    assertion about intent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.controller import (
    ABSTAIN,
    EXCEPTION,
    NO_ACTION,
    RECONCILED,
    reconcile,
    run_ladder,
)
from recon.match.normalize import load_batch
from recon.match.passes import (
    DEFAULT_LADDER,
    JOIN_ONLY_LADDER,
    Cardinality,
    Claim,
    ClaimTier,
    ExactJoinPass,
    PassResult,
)
from recon.metrics import baselines as B
from recon.metrics.score import AnswerKey, score

SEEDS = [42, 7, 99, 2026]
FAMILIES = [Family.DEVELOPMENT, Family.PRIMARY, Family.STRESS]
OUTCOMES = {RECONCILED, EXCEPTION, NO_ACTION, ABSTAIN}


def _dataset(tmp_path_factory, seed: int, family: Family) -> Path:
    out = tmp_path_factory.mktemp(f"ctl-{family.value}-{seed}")
    write_dataset(generate(GenConfig(n_records=500, seed=seed, family=family)), out)
    return Path(out)


@pytest.fixture(
    scope="session",
    params=[(seed, family) for seed in SEEDS for family in FAMILIES],
    ids=lambda p: f"{p[1].value}-seed{p[0]}",
)
def run(request, tmp_path_factory):
    """One agent run and one B1 run over the same dataset."""
    seed, family = request.param
    directory = _dataset(tmp_path_factory, seed, family)
    key = AnswerKey.load(directory)
    join_only = score(
        reconcile(directory, JOIN_ONLY_LADDER).to_agent_output(), key
    )
    agent = score(reconcile(directory).to_agent_output(), key)
    baseline = B.score_shared(directory, B.run_b1(B.Batch.load(directory)))
    return directory, join_only, agent, baseline


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def test_join_only_ladder_reproduces_b1_outcomes_exactly(run):
    _, join_only, _agent, baseline = run
    assert join_only.total_cases == baseline.total_cases
    assert join_only.correct_cases == baseline.correct_cases


def test_join_only_ladder_reproduces_b1_allocations_exactly(run):
    _, join_only, _agent, baseline = run
    assert join_only.allocations.true_positives == baseline.allocations.true_positives
    assert join_only.allocations.false_positives == baseline.allocations.false_positives
    assert join_only.allocations.false_negatives == baseline.allocations.false_negatives


def test_join_only_ladder_reproduces_b1_per_scenario(run):
    """Same total by a different route would still be a bug.

    Two wrong scenarios cancelling out is exactly the kind of agreement that
    looks like correctness in aggregate, so the comparison is made class by
    class.
    """
    _, join_only, _agent, baseline = run
    assert join_only.per_scenario == baseline.per_scenario


def test_an_exact_join_is_never_a_false_match(run):
    """Reading a foreign key cannot be wrong, so precision here must be perfect.

    If this ever drops below 1.0, the defect is an orientation flip in the
    allocation tuple rather than a matching error -- the pair is
    ``(event_id, settlement_id)`` on the answer-key side, and a transposition
    type-checks cleanly while scoring every claim as a false positive.
    """
    _, join_only, _agent, _ = run
    assert join_only.allocations.precision == 1.0
    assert join_only.false_match_rate == 0.0


def test_the_full_ladder_never_books_a_false_attribution(run):
    """The claim the corroboration rung exists to support, asserted directly.

    Adding a rung that resolves anonymous lines is only an improvement if it
    resolves them correctly. B2 resolves more of them than the join-only agent
    and is wrong about some, which is why its false-match rate is non-zero and
    its allocation precision is below one. The whole argument for nine gates is
    that they buy the extra coverage without buying that, so precision staying
    at exactly 1.0 here is the load-bearing assertion of this module.
    """
    _, _join_only, agent, _ = run
    assert agent.allocations.precision == 1.0
    assert agent.false_match_rate == 0.0


def test_the_full_ladder_resolves_strictly_more_than_the_join_alone(run):
    """A rung that adds no coverage is a rung to delete, so check it adds some."""
    _, join_only, agent, _ = run
    assert agent.allocations.true_positives > join_only.allocations.true_positives
    assert agent.allocations.recall > join_only.allocations.recall


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------


def test_every_verdict_uses_the_declared_vocabulary(run):
    directory, _, _, _ = run
    for verdict in reconcile(directory).verdicts:
        assert verdict.outcome in OUTCOMES
        if verdict.outcome == EXCEPTION:
            assert verdict.category, verdict.case_id
        assert verdict.reasons or verdict.outcome == RECONCILED


def test_no_action_cases_are_never_reported_as_exceptions(run):
    """The trap class, checked from the agent side.

    A NOT_SETTLEABLE case padded into the exception list is a false positive,
    and an exception list that flags correct behaviour is not an exception list.
    """
    directory, _, _, _ = run
    key = AnswerKey.load(directory)
    expected_no_action = {
        case.case_id for case in key.cases if case.expected_outcome == NO_ACTION
    }
    for verdict in reconcile(directory).verdicts:
        if verdict.case_id in expected_no_action:
            assert verdict.outcome == NO_ACTION, verdict.reasons


def test_the_join_only_ladder_never_abstains(run):
    """Abstention needs a rung that can find more than one candidate.

    With only the exact join there is nothing to be ambiguous about, so an
    abstention here would mean the abstention path is firing for the wrong
    reason.
    """
    directory, _, _, _ = run
    verdicts = reconcile(directory, JOIN_ONLY_LADDER).verdicts
    assert all(v.outcome != ABSTAIN for v in verdicts)


# --------------------------------------------------------------------------
# consumption discipline
# --------------------------------------------------------------------------


class _GreedyDouble:
    """A fake rung that claims the same event twice, and one already taken."""

    name = "greedy_double"
    cardinality = Cardinality.ONE_TO_ONE
    tolerance = None

    def __init__(self, settlement_id: str, event_id: str) -> None:
        self._settlement_id = settlement_id
        self._event_id = event_id

    def run(self, batch, consumed):
        claim = Claim(
            settlement_id=self._settlement_id,
            event_id=self._event_id,
            detail_id=None,
            pass_name=self.name,
            tier=ClaimTier.CONFIRMED,
            confidence=1.0,
            reasons=("fabricated for the consumption test",),
        )
        return PassResult(self.name, claims=[claim, claim], examined=2)


def test_the_runner_rejects_a_second_claim_on_a_consumed_event(tmp_path_factory):
    directory = _dataset(tmp_path_factory, 42, Family.PRIMARY)
    batch = load_batch(directory)
    line = next(line for line in batch.details if not line.is_anonymous)
    assert line.event_id is not None

    greedy = _GreedyDouble(line.settlement_id, line.event_id)
    result = run_ladder(batch, (greedy,))
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
    assert "already consumed" in result.rejected[0][1]


def test_a_later_rung_cannot_take_what_an_earlier_rung_proved(tmp_path_factory):
    """First claim wins, in ladder order.

    Earlier rungs are more precise by construction, so a later rung arriving at
    an event an earlier rung already proved is not new information -- and
    letting it through would make the result depend on iteration order inside
    each pass.
    """
    directory = _dataset(tmp_path_factory, 42, Family.PRIMARY)
    batch = load_batch(directory)
    line = next(line for line in batch.details if not line.is_anonymous)
    assert line.event_id is not None

    greedy = _GreedyDouble(line.settlement_id, line.event_id)
    result = run_ladder(batch, (ExactJoinPass(), greedy))
    claimed_by = {
        claim.event_id: claim.pass_name for claim in result.accepted
    }
    assert claimed_by[line.event_id] == "exact_join"
    assert all(claim.pass_name != "greedy_double" for claim in result.accepted)


def test_per_pass_yield_reports_only_surviving_claims(tmp_path_factory):
    """A rung cannot inflate its yield with claims the runner threw away."""
    directory = _dataset(tmp_path_factory, 42, Family.PRIMARY)
    batch = load_batch(directory)
    line = next(line for line in batch.details if not line.is_anonymous)
    assert line.event_id is not None

    result = run_ladder(batch, (ExactJoinPass(), _GreedyDouble(line.settlement_id, line.event_id)))
    by_name = {r.pass_name: r for r in result.per_pass}
    assert len(by_name["greedy_double"].claims) == 0
    assert by_name["greedy_double"].examined == 2


def test_no_event_is_allocated_to_two_settlements(run):
    directory, _, _, _ = run
    result = reconcile(directory)
    seen: set[str] = set()
    for verdict in result.verdicts:
        for event_id, _settlement_id in verdict.allocations:
            assert event_id not in seen, event_id
            seen.add(event_id)


# --------------------------------------------------------------------------
# the answer-key boundary
# --------------------------------------------------------------------------


def test_reconcile_never_opens_an_answer_file(tmp_path_factory, monkeypatch):
    """Record every file the agent opens and check the answers are not among them.

    ``answer_key_cases.csv`` is permitted because it is loaded through
    ``load_caseload``, which returns the partition and structurally cannot
    return the outcome columns. Every other answer-key file is off limits, and
    this test is what turns that from an intention into a property.
    """
    directory = _dataset(tmp_path_factory, 42, Family.PRIMARY)
    opened: list[str] = []
    real_open = Path.open

    def recording_open(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    reconcile(directory)
    monkeypatch.undo()

    answer_files = {name for name in opened if name.startswith("answer_key")}
    assert answer_files == {"answer_key_cases.csv"}, answer_files


# --------------------------------------------------------------------------
# reporting surface
# --------------------------------------------------------------------------


def test_run_reports_throughput_and_pass_names(tmp_path_factory):
    directory = _dataset(tmp_path_factory, 42, Family.PRIMARY)
    result = reconcile(directory)
    assert result.per_pass_names == ("exact_join", "refund_corroboration")
    assert result.record_count > 0
    assert result.throughput > 0


def test_the_same_dataset_reconciles_identically_twice(tmp_path_factory):
    """Determinism, asserted rather than assumed.

    An engine whose output moved between runs would make every published number
    unreproducible, and dict iteration order is exactly the kind of thing that
    silently introduces that.
    """
    directory = _dataset(tmp_path_factory, 7, Family.STRESS)
    first = reconcile(directory).verdicts
    second = reconcile(directory).verdicts
    assert first == second


# --------------------------------------------------------------------------
# how certain a case is allowed to claim to be
# --------------------------------------------------------------------------


class _SuggestingRung:
    """A fake rung that claims one anonymous line at a stated confidence.

    Stands in for the evidence-reading rung without a model in the loop. What
    matters for these tests is only that a claim can arrive below 1.0, which is
    a property of the SUGGESTED tier rather than of any particular reader.
    """

    name = "suggesting"
    cardinality = Cardinality.ONE_TO_ONE
    tolerance = None

    def __init__(self, settlement_id: str, event_id: str, confidence: float) -> None:
        self._settlement_id = settlement_id
        self._event_id = event_id
        self._confidence = confidence

    def run(self, batch, consumed):
        claim = Claim(
            settlement_id=self._settlement_id,
            event_id=self._event_id,
            detail_id=None,
            pass_name=self.name,
            tier=ClaimTier.SUGGESTED,
            confidence=self._confidence,
            reasons=("fabricated for the confidence test",),
        )
        return PassResult(self.name, claims=[claim], examined=1)


def test_the_deterministic_ladder_is_certain_or_absent(run):
    """Proof or abstain, measured rather than asserted.

    The gates either leave exactly one survivor or leave the line alone, so
    every case the deterministic engine resolves carries 1.0 and every case it
    declines carries 0.0. The report's precision/coverage table quotes this
    fact as the reason its curve is flat, and a rung that later emits a graded
    confidence has to retire that note -- so the claim is pinned here, where
    breaking it fails a test instead of quietly making a printed sentence
    wrong.
    """
    directory, _, _, _ = run
    confidences = {verdict.confidence for verdict in reconcile(directory).verdicts}
    assert confidences <= {0.0, 1.0}


def test_a_suggested_claim_lowers_the_confidence_of_its_own_case(tmp_path_factory):
    """A case is only as certain as the least certain claim holding it up.

    This was hardcoded to 1.0 on every resolved path, which published a line a
    model had guessed at as though nine gates had proved it. The bug was
    invisible in every headline metric -- the allocation was still right or
    wrong on its own merits -- and showed up only as an abstention curve that
    could not move.
    """
    directory = _dataset(tmp_path_factory, 42, Family.DEVELOPMENT)
    batch = load_batch(directory)
    line = next(line for line in batch.details if not line.is_anonymous)
    assert line.event_id is not None

    rung = _SuggestingRung(line.settlement_id, line.event_id, 0.72)
    verdicts = reconcile(directory, (rung, *DEFAULT_LADDER)).verdicts

    touched = [
        verdict
        for verdict in verdicts
        if (line.event_id, line.settlement_id) in verdict.allocations
    ]
    assert len(touched) == 1
    assert touched[0].confidence == pytest.approx(0.72)

    others = [verdict for verdict in verdicts if verdict is not touched[0]]
    assert {verdict.confidence for verdict in others} <= {0.0, 1.0}


def test_confidence_is_the_minimum_and_never_the_average(tmp_path_factory):
    """One proved leg must not launder a guessed one.

    A mean would report a case with one certain and one uncertain allocation as
    more certain than the uncertain half of it, which is the direction of error
    that costs money: the operator filters at a threshold and the guess rides
    through under cover of the proof.
    """
    directory = _dataset(tmp_path_factory, 42, Family.DEVELOPMENT)
    batch = load_batch(directory)

    # A settlement carrying at least two joinable lines, so the case ends up
    # holding one deterministic claim at 1.0 beside the suggested one.
    by_settlement: dict[str, list] = {}
    for detail in batch.details:
        if not detail.is_anonymous:
            by_settlement.setdefault(detail.settlement_id, []).append(detail)
    settlement_id, lines_here = next(
        (key, value) for key, value in sorted(by_settlement.items()) if len(value) >= 2
    )

    target = lines_here[0]
    assert target.event_id is not None
    rung = _SuggestingRung(settlement_id, target.event_id, 0.60)
    verdicts = reconcile(directory, (rung, *DEFAULT_LADDER)).verdicts

    verdict = next(
        verdict
        for verdict in verdicts
        if (target.event_id, settlement_id) in verdict.allocations
    )
    assert len(verdict.allocations) > 1
    assert verdict.confidence == pytest.approx(0.60)
