"""Tests for B1, the published difficulty floor.

B1 is the denominator of every headline number in this project: the agent score
is only meaningful quoted against D.  So the properties that matter here are not
"does it run" but "is D attributable".  A floor that moved for reasons nobody
could name would be worthless, and a floor whose failures were spread thinly
across every scenario would tell a panel nothing about what the agent actually
contributes.

The central assertion is therefore CONCENTRATION: B1 solves every scenario
except the two on the refund-attribution axis, and solves those not at all.
That is what licenses the claim "the gap between B1 and the agent is exactly the
ability to attribute an anonymous refund line, or to decline to".
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.entities import round_half_up as generator_round
from recon.datagen.io import write_dataset
from recon.metrics.baselines import (
    Batch,
    lexical_separation,
    lexical_similarity,
    lexical_hit_rate,
    round_half_up,
    run_b1,
    run_b2,
    run_b3,
    score,
)

# The two classes B1 cannot solve.  Everything else it must solve completely --
# see the module docstring for why the split, not the total, is the claim.
REFUND_ATTRIBUTION_CLASSES = {
    "CORROBORATED_REFUND",
    "CONTESTED_REFUND",
    "AMBIGUOUS_REFUND",
    "DESCRIBED_REFUND",
}

# Classes B2 must still fail.  CONTESTED_REFUND needs gate 9 (no second
# candidate survives), AMBIGUOUS_REFUND needs abstention, and DESCRIBED_REFUND
# needs the settlement note to be read; B2 has none of the three.
SURVIVES_B2 = {"CONTESTED_REFUND", "AMBIGUOUS_REFUND", "DESCRIBED_REFUND"}


def _b1(tmp_path_factory, seed: int, family: Family):
    out = tmp_path_factory.mktemp(f"b1-{family.value}-{seed}")
    write_dataset(generate(GenConfig(n_records=500, seed=seed, family=family)), out)
    batch = Batch.load(Path(out))
    return batch, score(batch, run_b1(batch))


@pytest.fixture(scope="session", params=[42, 7, 99, 2026], ids=lambda s: f"seed{s}")
def primary_b1(request, tmp_path_factory):
    return _b1(tmp_path_factory, request.param, Family.PRIMARY)


@pytest.fixture(scope="session", params=[42, 7, 99, 2026], ids=lambda s: f"seed{s}")
def stress_b1(request, tmp_path_factory):
    return _b1(tmp_path_factory, request.param, Family.STRESS)


@pytest.fixture(scope="session")
def all_seed_batches(tmp_path_factory):
    """Every seed and family in one list, for claims that need the pooled sample."""
    return [
        _b1(tmp_path_factory, seed, family)[0]
        for seed in (42, 7, 99, 2026)
        for family in (Family.PRIMARY, Family.STRESS)
    ]


# --------------------------------------------------------------------------
# rounding
# --------------------------------------------------------------------------


def test_rounding_agrees_with_the_generator_exhaustively():
    """The two implementations are independent on purpose; they must still agree.

    Independence is what makes the B1 fee check a real check rather than a
    tautology.  Divergence would make it a source of phantom FEE_TAX_VARIANCE
    findings, which is worse than having no check at all.
    """
    for denominator in (10_000, 3, 7, 100):
        for numerator in range(-5_000, 5_001):
            assert round_half_up(numerator, denominator) == generator_round(
                numerator, denominator
            ), (numerator, denominator)


def test_rounding_matches_decimal_half_up():
    """Ties go away from zero, which is what ROUND_HALF_UP means."""
    for numerator in range(-2_000, 2_001):
        expected = int(
            (Decimal(numerator) / Decimal(1_000)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        assert round_half_up(numerator, 1_000) == expected, numerator


def test_rounding_rejects_a_non_positive_denominator():
    with pytest.raises(ValueError):
        round_half_up(1, 0)


# --------------------------------------------------------------------------
# the difficulty floor
# --------------------------------------------------------------------------


def test_b1_solves_every_scenario_outside_the_refund_axis(primary_b1):
    _, result = primary_b1
    for scenario, (ok, total) in result["per_scenario"].items():
        if scenario in REFUND_ATTRIBUTION_CLASSES:
            continue
        assert ok == total, f"{scenario}: B1 got {ok}/{total}, expected all"


def test_b1_solves_no_refund_attribution_case(primary_b1):
    """If B1 ever solved one of these, D would be measuring something else."""
    _, result = primary_b1
    for scenario in REFUND_ATTRIBUTION_CLASSES:
        if scenario not in result["per_scenario"]:
            continue
        ok, total = result["per_scenario"][scenario]
        assert ok == 0, f"{scenario}: B1 solved {ok}/{total}, expected none"


def test_stress_floor_is_also_concentrated(stress_b1):
    """The stress family carries all ten classes, including the three the
    primary mix omits.  BANK_CREDIT_MISSING and BANK_CREDIT_DUPLICATE are the
    interesting ones: B1 does catch them, so they contribute nothing to D and
    must not be sold as agent capability.
    """
    _, result = stress_b1
    for scenario, (ok, total) in result["per_scenario"].items():
        if scenario in REFUND_ATTRIBUTION_CLASSES:
            assert ok == 0, f"{scenario}: B1 solved {ok}/{total}"
        else:
            assert ok == total, f"{scenario}: B1 got {ok}/{total}"


def test_d_is_bounded_by_the_refund_class_prevalence(primary_b1, stress_b1):
    """D must equal the share of cases on the refund axis -- no more, no less.

    This is the arithmetic form of the concentration claim, and it is the
    assertion that would fail first if a future generator change quietly made
    some other class unsolvable by exact joins.
    """
    for _, result in (primary_b1, stress_b1):
        unsolvable = sum(
            total
            for scenario, (_, total) in result["per_scenario"].items()
            if scenario in REFUND_ATTRIBUTION_CLASSES
        )
        assert result["b1_correct"] == result["total_cases"] - unsolvable
        assert result["difficulty_floor_D"] == pytest.approx(
            unsolvable / result["total_cases"]
        )


def test_score_accounting_is_internally_consistent(primary_b1):
    _, result = primary_b1
    counted = sum(total for _, total in result["per_scenario"].values())
    assert counted == result["total_cases"]
    assert 0 <= result["b1_correct"] <= result["total_cases"]
    assert result["b1_accuracy"] + result["difficulty_floor_D"] == pytest.approx(1.0)


def test_every_case_receives_exactly_one_verdict(primary_b1):
    """B1 may never abstain and may never skip -- it is a floor, not an agent."""
    batch, _ = primary_b1
    verdicts = run_b1(batch)
    assert len(verdicts) == len(batch.cases)
    assert {v.case_id for v in verdicts} == {c["case_id"] for c in batch.cases}
    assert all(v.outcome != "ABSTAIN" for v in verdicts)


def test_b1_is_deterministic(primary_b1):
    batch, _ = primary_b1
    first = [(v.case_id, v.outcome, v.category) for v in run_b1(batch)]
    second = [(v.case_id, v.outcome, v.category) for v in run_b1(batch)]
    assert first == second


def test_every_wrong_verdict_still_carries_its_evidence(primary_b1):
    """A floor that failed silently could not be audited by a sceptic, which is
    the entire reason B1 ships as code rather than as a quoted percentage.
    """
    batch, _ = primary_b1
    expected = {c["case_id"]: c["expected_outcome"] for c in batch.cases}
    for verdict in run_b1(batch):
        if verdict.outcome != expected[verdict.case_id]:
            assert verdict.reasons, f"{verdict.case_id} failed with no stated reason"


def test_not_settleable_is_never_reported_as_an_exception(primary_b1):
    """The trap class.  Padding the exception list with rows that were never
    going to settle is a false positive, and B1 must not model that behaviour
    any more than the agent may.
    """
    batch, _ = primary_b1
    scenarios = {c["case_id"]: c["scenario"] for c in batch.cases}
    for verdict in run_b1(batch):
        if scenarios[verdict.case_id] == "NOT_SETTLEABLE":
            assert verdict.outcome == "NO_ACTION"


# --------------------------------------------------------------------------
# B2 -- the strengthened floor
# --------------------------------------------------------------------------


def _b2(fixture):
    batch, _ = fixture
    return batch, score(batch, run_b2(batch))


def test_b2_is_at_least_as_strong_as_b1(primary_b1, stress_b1):
    """B2 is B1 plus a rule, so it can never score worse.  If it does, the hook
    has broken a check rather than added one."""
    for fixture in (primary_b1, stress_b1):
        batch, b1_result = fixture
        _, b2_result = _b2(fixture)
        assert b2_result["b1_correct"] >= b1_result["b1_correct"]


def test_b2_solves_uncontested_recovery(primary_b1, stress_b1):
    """A delta with a unique amount candidate is not a capability -- it is a
    lookup, and B2 must demonstrate that publicly."""
    for fixture in (primary_b1, stress_b1):
        _, result = _b2(fixture)
        if "CORROBORATED_REFUND" not in result["per_scenario"]:
            continue
        ok, total = result["per_scenario"]["CORROBORATED_REFUND"]
        assert ok == total, f"B2 solved only {ok}/{total} uncontested recoveries"


def test_the_benchmark_is_not_trivially_solvable(primary_b1, stress_b1):
    """The regression test for the defect that forced this class to exist.

    The benchmark once had D = 0.0% under B2 on the primary family: every
    anonymous refund delta was unique on amount alone, so the nine-gate
    corroboration rule eliminated no candidate and the whole architecture was
    decorative.  A floor of zero means the dataset is solved by one line of SQL.
    """
    for fixture in (primary_b1, stress_b1):
        _, result = _b2(fixture)
        assert result["difficulty_floor_D"] > 0.0, (
            "B2 solves the whole batch -- the benchmark is trivially solvable"
        )


def test_gates_are_load_bearing(primary_b1, stress_b1):
    """B2 must fail the classes that need uniqueness or abstention.

    This is the assertion that would have caught the original defect: if B2
    solves CONTESTED_REFUND, then amount alone was decisive and gates 1-8 did
    no work.
    """
    for fixture in (primary_b1, stress_b1):
        _, result = _b2(fixture)
        for scenario in SURVIVES_B2:
            if scenario not in result["per_scenario"]:
                continue
            ok, total = result["per_scenario"][scenario]
            assert ok < total, (
                f"B2 solved {ok}/{total} of {scenario} -- it should not be able to"
            )


def test_a_wrong_attribution_is_scored_wrong(primary_b1):
    """Outcome-only scoring would give full marks to a false match, because a
    contested delta resolved to the WRONG refund event still yields RECONCILED.
    """
    batch, _ = primary_b1
    verdicts = run_b1(batch)
    verdicts[0].outcome = next(
        c["expected_outcome"] for c in batch.cases if c["case_id"] == verdicts[0].case_id
    )
    verdicts[0].claims = [("setl_does_not_exist", "evt_does_not_exist")]
    result = score(batch, verdicts)
    assert result["false_attributions"] == 1
    scenario = next(
        c["scenario"] for c in batch.cases if c["case_id"] == verdicts[0].case_id
    )
    ok, _ = result["per_scenario"][scenario]
    assert result["b1_correct"] < len(batch.cases)


def test_b2_makes_false_attributions(primary_b1, stress_b1):
    """B2 must get some attributions WRONG, and that is the whole point.

    Before CONTESTED_REFUND existed, every anonymous delta had a single
    amount-matching candidate, so B2 was never wrong and the difficulty floor
    was zero.  A non-zero false-attribution count is the direct evidence that
    amount alone is no longer decisive and the gates have work to do.

    Note this is measured on ATTRIBUTIONS, not outcomes: a contested delta
    charged to the wrong refund event still yields RECONCILED, so outcome-only
    scoring would report B2 as flawless here.
    """
    for fixture in (primary_b1, stress_b1):
        batch, _ = fixture
        result = score(batch, run_b2(batch))
        assert result["false_attributions"] > 0, (
            "B2 attributed every delta correctly -- amount alone is still decisive"
        )


# --------------------------------------------------------------------------
# B3 -- the lexical baseline, published because it is expected to fail
# --------------------------------------------------------------------------


def test_content_token_overlap_is_zero_on_every_contested_line(primary_b1, stress_b1):
    """The claim "string similarity has nothing to rank on", measured.

    ``recon.datagen.catalogue`` proves the two vocabularies are disjoint at
    import, but that is a property of the catalogue, not of the emitted CSVs.
    This asserts it end to end: on released data, for every anonymous refund
    line with more than one exact-amount candidate, no candidate's product
    description shares a single content token with the settlement note.
    """
    for batch, _ in (primary_b1, stress_b1):
        separation = lexical_separation(batch)
        assert separation["contested_lines"] > 0, (
            "no multi-candidate refund lines -- the measurement is vacuous"
        )
        assert separation["max_token_overlap"] == 0


def test_lexical_ranking_performs_at_chance(primary_b1, stress_b1):
    """The claim, measured on the decision rather than on the case count.

    Comparing B3's case accuracy against B2's is the wrong instrument: a
    different tie-break consumes a different event, which shifts what later
    lines can claim, so the two baselines swing several cases apart for reasons
    unrelated to ranking quality.  On one seed B3 came out four cases ahead of
    B2 that way, which measured the knock-on effect and not the strings.

    This measures the decision directly.  Over every multi-candidate refund line
    that has a knowable answer, lexical ranking must land within noise of the
    coin-flip rate.  The band is wide because the sample is a few dozen lines
    and a binomial at that size moves; what would falsify the design is a
    consistent positive lift, not a seed that runs warm.
    """
    for batch, _ in (primary_b1, stress_b1):
        hit = lexical_hit_rate(batch)
        assert hit["decidable_lines"] >= 5, "sample too small to say anything"
        assert abs(hit["lift"]) < 0.25, (
            f"lexical ranking hit {hit['hits']} of {hit['decidable_lines']} against "
            f"{hit['expected_by_chance']:.1f} expected -- string similarity is "
            "carrying signal it should not have"
        )


def test_lexical_lift_does_not_accumulate_across_seeds(all_seed_batches):
    """One warm seed is noise; a positive lift on the pooled sample is not.

    Pooling the four seeds and both families multiplies the sample by eight,
    which is what turns "within the band" into an actual statement.  If string
    similarity carried even weak signal it would survive the pooling, and this
    is the assertion that would catch it.
    """
    hits = 0
    expected = 0.0
    lines = 0
    for batch in all_seed_batches:
        hit = lexical_hit_rate(batch)
        hits += hit["hits"]
        expected += hit["expected_by_chance"]
        lines += hit["decidable_lines"]
    assert lines >= 100
    assert abs(hits - expected) / lines < 0.10, (
        f"pooled over {lines} decidable lines, lexical ranking hit {hits} "
        f"against {expected:.1f} expected by chance"
    )


def test_lexical_margin_between_candidates_is_noise(primary_b1):
    """What is left after the tokens are gone is incidental letter overlap."""
    batch, _ = primary_b1
    separation = lexical_separation(batch)
    assert separation["mean_margin"] < 0.15
    assert separation["max_margin"] < 0.40


def test_lexical_similarity_is_zero_on_an_empty_side():
    assert lexical_similarity("", "Puma Smash v2 sneakers") == 0.0
    assert lexical_similarity("return - footwear size mismatch", "") == 0.0


def test_b3_still_commits_to_every_case(primary_b1):
    """B3 is a floor, not an agent: it may not abstain either."""
    batch, _ = primary_b1
    verdicts = run_b3(batch)
    assert len(verdicts) == len(batch.cases)
    assert all(v.outcome != "ABSTAIN" for v in verdicts)
