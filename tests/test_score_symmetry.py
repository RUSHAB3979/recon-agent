"""The two halves of the scorer must agree on what a legal decision is.

`AnswerKeyCase` and `CaseDecision` validate the same field, `category`, on
opposite sides of the same comparison.  When they disagreed, the answer key
could express a label -- `DUPLICATE_DETAIL_EXPORT_WARNING` on a `RECONCILED`
case -- that no agent could construct: building the decision raised
`ValueError` before scoring began.  Eight cases per batch were therefore
unscoreable-correct no matter what the agent had actually worked out, and the
headline accuracy was capped below 100% for a reason that had nothing to do
with reconciliation.

The defect was invisible to a hand-written fixture and obvious the moment a
decision set was built from the released answer key itself.  So that is what
these tests do: they read the real key and assert that every label in it is
one an agent is allowed to say back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.metrics.score import AgentOutput, AnswerKey, CaseDecision, score


@pytest.fixture(scope="module", params=[42, 7], ids=lambda s: f"seed{s}")
def key(request, tmp_path_factory) -> AnswerKey:
    out = tmp_path_factory.mktemp(f"symmetry-{request.param}")
    write_dataset(generate(GenConfig(n_records=500, seed=request.param, family=Family.PRIMARY)), out)
    return AnswerKey.load(Path(out))


def _perfect(key: AnswerKey) -> AgentOutput:
    """Replay the answer key back as an agent that got everything right."""
    return AgentOutput(
        CaseDecision(
            case_id=case.case_id,
            outcome=case.expected_outcome,
            category=case.expected_exception_category,
            allocations=key.allocations_for(case.case_id),
            confidence=1.0,
        )
        for case in key.cases
    )


def test_every_expected_label_is_one_an_agent_may_report(key):
    """The regression test for the asymmetry.

    Constructing this decision set is the assertion -- if either side rejects a
    label the other side produces, `CaseDecision.__post_init__` raises here.
    """
    _perfect(key)


def test_replaying_the_answer_key_scores_perfectly(key):
    """A ceiling below 1.0 would mean some case cannot be answered correctly."""
    report = score(_perfect(key), key)
    assert report.outcome_accuracy == 1.0
    assert report.false_match_rate == 0.0
    assert report.allocation_precision == 1.0
    assert report.allocation_recall == 1.0


def test_a_reconciled_case_may_carry_only_a_warning(key):
    """The relaxation is narrow on purpose: RECONCILED admits diagnostics, not
    exception categories.  Letting an arbitrary category ride on a reconciled
    case would blur the line the exception list depends on."""
    with pytest.raises(ValueError):
        CaseDecision(
            case_id=key.cases[0].case_id,
            outcome="RECONCILED",
            category="BANK_CREDIT_MISSING",
            allocations=frozenset(),
            confidence=1.0,
        )


def test_no_action_still_admits_no_category(key):
    """NO_ACTION is the trap class.  A category here is the first step toward
    padding the exception list with rows that were never going to settle."""
    with pytest.raises(ValueError):
        CaseDecision(
            case_id=key.cases[0].case_id,
            outcome="NO_ACTION",
            category="ANYTHING_AT_ALL",
            allocations=frozenset(),
            confidence=1.0,
        )


def test_an_exception_still_requires_a_category(key):
    """An unnamed exception is not an exception list, it is a pile."""
    with pytest.raises(ValueError):
        CaseDecision(
            case_id=key.cases[0].case_id,
            outcome="EXCEPTION",
            category=None,
            allocations=frozenset(),
            confidence=1.0,
        )
