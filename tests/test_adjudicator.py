"""Tests for the evidence-reading rung.

WHAT THESE TESTS ARE FOR, AND WHAT THEY DELIBERATELY ARE NOT

    Not one of them calls a model. A test that did would be measuring the model
    rather than the code, would cost money to run, would fail on a machine with
    no key, and would not be reproducible -- which is the wrong set of
    properties for the suite that guards the published numbers.

    What is under test is the machinery around the reader: that it only ever
    sees the residual, that a label outside the shortlist is discarded rather
    than repaired, that a low confidence becomes a decline, that the default
    reader leaves every published figure exactly where it was, and that a
    reader which reads correctly turns those residual lines into correct
    claims. Each of those is a property of this repo's code and is worth
    pinning; the model's accuracy is not, because it is not ours to pin.

THE ORACLE READER

    ``_OracleReader`` answers from the answer key. It is not a claim that a
    model would do as well -- it is the upper bound on what the plumbing can
    deliver, which is the thing worth testing. If a perfect reader could not
    lift the score through this rung, the rung would be broken regardless of
    which model were attached to it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.adjudicator import (
    DEFAULT_MODEL,
    PRICING,
    RESPONSE_SCHEMA,
    Adjudication,
    AdjudicationPass,
    AdjudicationRequest,
    AnthropicReader,
    Candidate,
    DecliningReader,
    ScriptedReader,
    Usage,
    build_request,
    default_reader,
    render_request,
)
from recon.match.controller import reconcile, run_ladder
from recon.match.normalize import load_batch
from recon.match.passes import DEFAULT_LADDER, ClaimTier
from recon.metrics.score import AnswerKey, score


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def directory(tmp_path_factory) -> Path:
    """One development batch. Dev is the only surface anything may be tuned on."""

    out = tmp_path_factory.mktemp("adjudicator")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


@pytest.fixture(scope="module")
def residual(directory):
    """The batch, and the lines the deterministic ladder could not separate."""

    batch = load_batch(directory)
    run = run_ladder(batch, DEFAULT_LADDER)
    abstentions = tuple(
        abstention for result in run.per_pass for abstention in result.abstentions
    )
    return batch, run, abstentions


class _OracleReader:
    """Answers correctly, from the answer key. The upper bound, not a forecast."""

    model = "oracle"

    def __init__(self, key: AnswerKey) -> None:
        self._truth = {allocation.pair for allocation in key.allocations}
        self.seen: list[AdjudicationRequest] = []

    def read(self, request: AdjudicationRequest) -> Adjudication:
        self.seen.append(request)
        for candidate in request.candidates:
            if (candidate.event_id, request.settlement_id) in self._truth:
                return Adjudication(
                    detail_id=request.detail_id,
                    label=candidate.label,
                    confidence=0.95,
                    reasoning="answer key",
                    model=self.model,
                    usage=Usage(calls=1),
                )
        return Adjudication(
            detail_id=request.detail_id,
            label=None,
            confidence=0.0,
            reasoning="no candidate is the truth",
            model=self.model,
            usage=Usage(calls=1),
        )


class _FirstAlwaysReader:
    """Always names candidate A at high confidence. The failure mode, embodied."""

    model = "first-always"

    def read(self, request: AdjudicationRequest) -> Adjudication:
        return Adjudication(
            detail_id=request.detail_id,
            label=request.candidates[0].label,
            confidence=0.99,
            reasoning="picked the first one",
            model=self.model,
            usage=Usage(calls=1),
        )


# --------------------------------------------------------------------------
# cost accounting
# --------------------------------------------------------------------------


def test_usage_adds_componentwise() -> None:
    total = Usage(10, 20, 30, 1) + Usage(1, 2, 3, 1)
    assert (total.input_tokens, total.output_tokens) == (11, 22)
    assert (total.cache_read_tokens, total.calls) == (33, 2)


def test_cost_uses_published_rates() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert usage.cost_usd("claude-haiku-4-5-20251001") == Decimal("6.000000")


def test_cached_input_is_billed_at_a_tenth() -> None:
    """A cost metric that ignored the cache discount would overstate the price.

    Overstating is the safe direction, but it is still wrong, and the cache hit
    rate is one of the things the design is meant to demonstrate.
    """

    cached = Usage(cache_read_tokens=1_000_000).cost_usd(DEFAULT_MODEL)
    fresh = Usage(input_tokens=1_000_000).cost_usd(DEFAULT_MODEL)
    assert cached * 10 == fresh


def test_an_unknown_model_costs_nothing_rather_than_guessing() -> None:
    """A fabricated price looks exactly like a measured one in a report."""

    assert Usage(input_tokens=5_000).cost_usd("some-model-we-never-priced") == Decimal("0")


def test_the_default_model_is_priced() -> None:
    assert DEFAULT_MODEL in PRICING


# --------------------------------------------------------------------------
# request construction
# --------------------------------------------------------------------------


def test_build_request_needs_two_candidates(residual) -> None:
    """One candidate is gate 9's business, not this rung's."""

    batch, _run, abstentions = residual
    abstention = abstentions[0]
    line = next(l for l in batch.details if l.detail_id == abstention.detail_id)
    assert build_request(batch, line, abstention.candidate_event_ids[:1]) is None


def test_build_request_reaches_the_description_through_lineage(residual) -> None:
    """A refund carries no product text; its parent payment does.

    The hop is the point. Reading a category off the refund row would be
    reading a column; traversing txn_id to the payment that spawned it is
    reconciliation.
    """

    batch, _run, abstentions = residual
    abstention = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)
    line = next(l for l in batch.details if l.detail_id == abstention.detail_id)
    request = build_request(batch, line, abstention.candidate_event_ids)

    assert request is not None
    assert [c.label for c in request.candidates] == ["A", "B"][: len(request.candidates)]
    for candidate in request.candidates:
        event = batch.events[candidate.event_id]
        assert event.description == ""  # the refund itself says nothing
        assert candidate.parent_txn_id == event.txn_id
        assert candidate.parent_description  # the payment does


def test_the_rendered_prompt_carries_evidence_and_not_arithmetic(residual) -> None:
    """Amounts and dates are identical across the shortlist by construction.

    Showing them would offer a difference that does not exist and invite a
    fabricated numeric justification for a decision made on other grounds.
    """

    batch, _run, abstentions = residual
    abstention = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)
    line = next(l for l in batch.details if l.detail_id == abstention.detail_id)
    request = build_request(batch, line, abstention.candidate_event_ids)
    assert request is not None

    rendered = render_request(request)
    assert request.note in rendered
    for candidate in request.candidates:
        assert candidate.parent_description in rendered
    for candidate in request.candidates:
        event = batch.events[candidate.event_id]
        assert str(event.amount_paise) not in rendered
        assert event.created_on.isoformat() not in rendered


def test_candidate_lookup_by_label() -> None:
    request = AdjudicationRequest(
        settlement_id="S1",
        detail_id="D1",
        note="note",
        candidates=(
            Candidate("A", "E1", "T1", "widget"),
            Candidate("B", "E2", "T2", "gadget"),
        ),
    )
    assert request.candidate_by_label("B").event_id == "E2"
    assert request.candidate_by_label("Z") is None


# --------------------------------------------------------------------------
# the rung, driven by readers that cost nothing
# --------------------------------------------------------------------------


def test_the_default_reader_declines_everything() -> None:
    request = AdjudicationRequest("S1", "D1", "note", (Candidate("A", "E1", "T1", "x"),))
    verdict = DecliningReader().read(request)
    assert verdict.label is None
    assert "human review" in verdict.reasoning


def test_the_null_reader_leaves_every_published_number_untouched(directory) -> None:
    """The headline figures must not depend on a network call, or a key, or a mood."""

    key = AnswerKey.load(directory)
    deterministic = score(reconcile(directory, DEFAULT_LADDER).to_agent_output(), key)
    with_null_rung = score(
        reconcile(
            directory, (*DEFAULT_LADDER, AdjudicationPass(DecliningReader()))
        ).to_agent_output(),
        key,
    )
    assert with_null_rung.correct_cases == deterministic.correct_cases
    assert with_null_rung.allocations == deterministic.allocations
    assert with_null_rung.false_match_rate == deterministic.false_match_rate


def test_the_rung_only_ever_sees_residual_lines(residual) -> None:
    """Structural, not promised: the runner hands it the abstentions and nothing else."""

    batch, run, abstentions = residual
    reader = ScriptedReader({})
    AdjudicationPass(reader).run_residual(batch, frozenset(), abstentions)
    seen = {request.detail_id for request in reader.seen}
    assert seen <= {a.detail_id for a in abstentions}
    assert seen.isdisjoint(run.attributed_detail_ids)


def test_a_label_outside_the_shortlist_is_discarded(residual) -> None:
    """The gates decided which events are admissible; this rung cannot add one."""

    batch, run, abstentions = residual
    target = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)
    reader = ScriptedReader({target.detail_id: ("Z", 0.99)})
    result = AdjudicationPass(reader).run_residual(batch, frozenset(), abstentions)

    assert not result.claims
    reason = next(a.reason for a in result.abstentions if a.detail_id == target.detail_id)
    assert "not a candidate" in reason


def test_confidence_below_the_floor_becomes_a_decline(residual) -> None:
    batch, _run, abstentions = residual
    target = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)

    below = ScriptedReader({target.detail_id: ("A", 0.40)})
    result = AdjudicationPass(below, min_confidence=0.70).run_residual(
        batch, frozenset(), abstentions
    )
    assert not result.claims
    reason = next(a.reason for a in result.abstentions if a.detail_id == target.detail_id)
    assert "below the 0.70 floor" in reason

    above = ScriptedReader({target.detail_id: ("A", 0.80)})
    result = AdjudicationPass(above, min_confidence=0.70).run_residual(
        batch, frozenset(), abstentions
    )
    assert [claim.detail_id for claim in result.claims] == [target.detail_id]


def test_claims_from_this_rung_are_suggested_never_confirmed(residual) -> None:
    """The gates could not separate these candidates.

    No amount of fluent prose turns the result into a proof, and the tier is
    what tells a reviewer which claims to look at first.
    """

    batch, _run, abstentions = residual
    target = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)
    reader = ScriptedReader({target.detail_id: ("A", 0.99)})
    result = AdjudicationPass(reader).run_residual(batch, frozenset(), abstentions)
    assert all(claim.tier is ClaimTier.SUGGESTED for claim in result.claims)
    assert all(claim.pass_name == "adjudication" for claim in result.claims)


def test_a_consumed_candidate_leaves_nothing_to_adjudicate(residual) -> None:
    """A shortlist narrowed to one is gate 9's answer, not this rung's."""

    batch, _run, abstentions = residual
    target = next(a for a in abstentions if len(a.candidate_event_ids) >= 2)
    reader = ScriptedReader({target.detail_id: ("A", 0.99)})
    AdjudicationPass(reader).run_residual(
        batch, frozenset(target.candidate_event_ids[1:]), abstentions
    )
    assert target.detail_id not in {request.detail_id for request in reader.seen}


def test_the_call_budget_is_enforced(residual) -> None:
    batch, _run, abstentions = residual
    reader = ScriptedReader({}, default=(None, 0.0))
    result = AdjudicationPass(reader, max_calls=2).run_residual(
        batch, frozenset(), abstentions
    )
    assert len(reader.seen) <= 2
    assert result.counters["lines_adjudicated"] <= 2
    assert any("call budget exhausted" in a.reason for a in result.abstentions)


def test_counters_report_what_the_rung_actually_did(residual) -> None:
    batch, _run, abstentions = residual
    eligible = [a for a in abstentions if len(a.candidate_event_ids) >= 2]
    reader = ScriptedReader({eligible[0].detail_id: ("A", 0.99)})
    result = AdjudicationPass(reader).run_residual(batch, frozenset(), abstentions)

    assert result.counters["calls"] == len(reader.seen)
    assert result.counters["resolved"] == len(result.claims)
    assert result.counters["declined"] == len(result.abstentions)


# --------------------------------------------------------------------------
# what the rung is worth, end to end
# --------------------------------------------------------------------------


def test_a_correct_reader_converts_the_residual_into_correct_claims(directory) -> None:
    """The upper bound on the plumbing, measured with the real scorer.

    A perfect reader must lift the case score and must not book a single false
    attribution. If it cannot, the rung is broken independently of any model.
    """

    key = AnswerKey.load(directory)
    deterministic = score(reconcile(directory, DEFAULT_LADDER).to_agent_output(), key)
    oracle = score(
        reconcile(
            directory, (*DEFAULT_LADDER, AdjudicationPass(_OracleReader(key)))
        ).to_agent_output(),
        key,
    )

    assert oracle.correct_cases > deterministic.correct_cases
    assert oracle.allocations.true_positives > deterministic.allocations.true_positives
    assert oracle.false_match_rate == 0.0
    assert oracle.allocations.precision == 1.0


def test_a_reader_that_never_declines_books_false_attributions(directory) -> None:
    """Abstention has to survive contact with the model.

    Part of the residual is separable from the note and part of it deliberately
    is not. A reader that answers everything scores the first group and is
    wrong about the second, which is why the confidence floor and the decline
    path exist rather than being decoration.
    """

    key = AnswerKey.load(directory)
    reckless = score(
        reconcile(
            directory, (*DEFAULT_LADDER, AdjudicationPass(_FirstAlwaysReader()))
        ).to_agent_output(),
        key,
    )
    assert reckless.allocations.false_positives > 0
    assert reckless.false_match_rate > 0.0


# --------------------------------------------------------------------------
# the Anthropic reader, without Anthropic
# --------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 120
        self.output_tokens = 40
        self.cache_read_input_tokens = 300


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


def _request() -> AdjudicationRequest:
    return AdjudicationRequest(
        settlement_id="S1",
        detail_id="D1",
        note="refund for the blue widget",
        candidates=(
            Candidate("A", "E1", "T1", "Blue Widget"),
            Candidate("B", "E2", "T2", "Red Gadget"),
        ),
    )


def test_the_anthropic_reader_parses_a_structured_verdict() -> None:
    client = _FakeClient('{"label": "A", "confidence": 0.9, "reasoning": "note names it"}')
    verdict = AnthropicReader(client=client).read(_request())
    assert verdict.label == "A"
    assert verdict.confidence == pytest.approx(0.9)
    assert verdict.usage.cache_read_tokens == 300
    assert verdict.usage.calls == 1


def test_the_system_prompt_is_cached_and_the_schema_is_closed() -> None:
    """The preamble is byte-identical on every call and dominates the input.

    Paying full input price for it once per line would make the reported cost
    per batch wrong in the direction that flatters us.
    """

    client = _FakeClient('{"label": null, "confidence": 0.0, "reasoning": "generic"}')
    AnthropicReader(client=client).read(_request())
    sent = client.messages.kwargs

    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    assert set(RESPONSE_SCHEMA["required"]) == {"label", "confidence", "reasoning"}


def test_an_unparseable_response_declines_rather_than_raising() -> None:
    """One unreadable line must not cost the other four hundred and forty nine."""

    client = _FakeClient("I think it is probably the first one, honestly")
    verdict = AnthropicReader(client=client).read(_request())
    assert verdict.label is None
    assert verdict.usage.calls == 1
    assert "declined" in verdict.reasoning


class _FailingMessages:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def create(self, **kwargs):
        raise self._error


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.messages = _FailingMessages(error)


def test_a_transport_failure_declines_the_line_rather_than_the_batch() -> None:
    """A line the network ate is a line for a human, not a line to guess at."""

    import httpx2
    from anthropic import APIConnectionError

    client = _FailingClient(
        APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))
    )
    verdict = AnthropicReader(client=client).read(_request())
    assert verdict.label is None
    assert "unreachable" in verdict.reasoning
    assert verdict.usage.calls == 1


def test_a_bug_in_this_module_still_crashes_the_run() -> None:
    """Degrading to "declined everything" on a TypeError would hide the bug.

    It would look identical to a model that declined everything, which is the
    one failure this design must never be able to disguise.
    """

    client = _FailingClient(TypeError("someone changed a signature"))
    with pytest.raises(TypeError):
        AnthropicReader(client=client).read(_request())


def test_default_reader_is_the_null_one_without_a_key(monkeypatch) -> None:
    """CI has no key, so CI measures the deterministic engine. That is the point."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(default_reader(), DecliningReader)
