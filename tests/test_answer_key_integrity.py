"""Structural proofs that the answer key is trustworthy.

Every metric this project will ever report is computed against the answer key,
so the answer key being right is a precondition for any number in the README
meaning anything.  These tests exist so that claim is checked rather than
asserted.  If this file passes, the ground truth is internally consistent,
complete, and free of the positional leakage that would let a matcher score
well by accident.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from recon.datagen import GenConfig, Resolution, Scenario, generate
from recon.datagen.entities import money


# ---------- completeness and referential integrity ----------

def test_every_key_id_exists_in_the_source_files(dataset):
    ledger_ids = {t.txn_id for t in dataset.gateway}
    statement_utrs = {c.utr for c in dataset.bank}
    for link in dataset.links:
        assert set(link.txn_ids) <= ledger_ids, f"{link.link_id} cites unknown txn_id"
        assert set(link.utrs) <= statement_utrs, f"{link.link_id} cites unknown utr"


def test_every_record_belongs_to_exactly_one_link(dataset):
    txn_hits = Counter(t for link in dataset.links for t in link.txn_ids)
    utr_hits = Counter(u for link in dataset.links for u in link.utrs)

    assert set(txn_hits) == {t.txn_id for t in dataset.gateway}, "gateway rows unaccounted for"
    assert set(utr_hits) == {c.utr for c in dataset.bank}, "bank rows unaccounted for"
    assert not [k for k, v in txn_hits.items() if v > 1], "txn claimed by multiple links"
    assert not [k for k, v in utr_hits.items() if v > 1], "credit claimed by multiple links"


def test_identifiers_are_unique(dataset):
    for label, values in [
        ("txn_id", [t.txn_id for t in dataset.gateway]),
        ("order_id", [t.order_id for t in dataset.gateway]),
        ("utr", [c.utr for c in dataset.bank]),
        ("bank_ref", [c.bank_ref for c in dataset.bank]),
    ]:
        assert len(values) == len(set(values)), f"duplicate {label}"


def test_truth_pairs_are_the_cross_product_of_each_link(dataset):
    expected = {
        (t, u) for link in dataset.links for t in link.txn_ids for u in link.utrs
    }
    actual = {(p.txn_id, p.utr) for p in dataset.pairs}
    assert actual == expected
    assert len(dataset.pairs) == len(expected), "duplicate truth pairs emitted"


# ---------- arithmetic ----------

def test_ledger_arithmetic_identity_holds(dataset):
    for t in dataset.gateway:
        assert t.net_amount == money(t.amount - t.fee - t.tax), t.txn_id
        assert t.fee >= 0 and t.tax >= 0 and t.amount > 0


def test_money_is_exact_to_the_paisa(dataset):
    for t in dataset.gateway:
        for value in (t.amount, t.fee, t.tax, t.net_amount, t.refund_amount):
            assert value == value.quantize(Decimal("0.01")), f"{t.txn_id} carries sub-paisa noise"
    for c in dataset.bank:
        assert c.credit_amount == c.credit_amount.quantize(Decimal("0.01"))


def test_link_totals_match_the_credits_they_cite(dataset, by_scenario):
    credits = {c.utr: c for c in dataset.bank}
    for link in dataset.links:
        if not link.utrs:
            assert link.actual_inr_total is None
            continue
        summed = money(sum((credits[u].credit_amount for u in link.utrs), Decimal(0)))
        assert summed == link.actual_inr_total, f"{link.link_id} total disagrees with its credits"


# ---------- per-scenario invariants ----------
# Each scenario claims to test one capability.  These assert the data actually
# exercises that capability, so a passing matcher cannot have got there by
# matching on something the defect was supposed to have broken.

def test_all_scenarios_are_represented(dataset):
    present = set(dataset.summary())
    missing = {s.value for s in Scenario} - present
    assert not missing, f"scenario(s) never generated at this size: {missing}"


def test_clean_matches_are_actually_clean(by_scenario):
    for link, txns, credits in by_scenario["clean_1to1"]:
        (t,), (c,) = txns, credits
        assert c.credit_amount == t.net_amount
        assert t.currency == "INR" and t.status == "captured"
        lag = (c.settlement_date - t.created_at.date()).days
        assert 1 <= lag <= 2, f"{link.link_id} clean case has lag T+{lag}"
        assert t.order_id.replace("order_", "").upper() in c.narration.upper()


def test_date_offset_cases_span_multiple_lags(by_scenario):
    lags = {
        (c.settlement_date - t.created_at.date()).days
        for _, (t,), (c,) in by_scenario["date_offset"]
    }
    assert len(lags) >= 3, f"date_offset collapsed to lags {lags}; not a time-window test"


def test_rounding_deltas_are_within_the_declared_band(by_scenario):
    for link, (t,), (c,) in by_scenario["rounding"]:
        delta = abs(c.credit_amount - t.net_amount)
        assert Decimal("0.01") <= delta <= Decimal("0.05"), f"{link.link_id} delta {delta}"


def test_fee_deduction_breaks_naive_net_matching(by_scenario):
    for link, (t,), (c,) in by_scenario["fee_deduction"]:
        assert c.credit_amount != t.net_amount, f"{link.link_id} is not actually fee-affected"
        assert c.credit_amount in (t.amount, money(t.amount - t.fee))


def test_batch_settlements_aggregate_correctly(by_scenario):
    for link, txns, credits in by_scenario["many_to_one"]:
        assert len(txns) >= 2 and len(credits) == 1
        assert credits[0].credit_amount == money(sum((t.net_amount for t in txns), Decimal(0)))
        for t in txns:
            assert t.order_id.replace("order_", "").upper() not in credits[0].narration.upper(), (
                "batch narration leaks a per-txn reference; aggregation would not be required"
            )


def test_duplicate_settlements_have_two_credits_for_one_txn(by_scenario):
    for link, (t,), credits in by_scenario["duplicate_settlement"]:
        assert len(credits) == 2
        assert all(c.credit_amount == t.net_amount for c in credits)
        assert credits[0].utr != credits[1].utr
        assert link.resolution is Resolution.EXCEPTION_DUPLICATE


def test_true_exceptions_have_the_shape_they_claim(by_scenario):
    for link, txns, credits in by_scenario["missing_on_bank"]:
        assert len(txns) == 1 and not credits
        assert txns[0].status == "captured", "an unsettled exception must be a settleable txn"
        assert link.resolution is Resolution.EXCEPTION_UNSETTLED

    for link, txns, credits in by_scenario["missing_on_gateway"]:
        assert not txns and len(credits) == 1
        assert link.resolution is Resolution.EXCEPTION_UNEXPLAINED_CREDIT


def test_not_settleable_is_a_trap_not_an_exception(by_scenario):
    for link, (t,), credits in by_scenario["not_settleable"]:
        assert not credits
        assert t.status in {"failed", "created"}
        assert link.resolution is Resolution.NO_ACTION, (
            "a failed payment reported as an exception is a false positive, not a find"
        )


def test_partial_refunds_reconcile_to_net_minus_refund(by_scenario):
    for link, (t,), (c,) in by_scenario["partial_refund"]:
        assert t.refund_amount > 0 and t.status == "partially_refunded"
        assert c.credit_amount == money(t.net_amount - t.refund_amount)


def test_fx_cases_require_currency_normalisation(by_scenario):
    for link, (t,), (c,) in by_scenario["fx_settlement"]:
        assert t.currency != "INR"
        assert c.credit_amount != t.net_amount, "FX case settled at face value; nothing to normalise"


def test_destroyed_narrations_really_are_unrecoverable(by_scenario):
    """The honesty test for the LLM path.

    Where the answer key says the reference was destroyed, no trace of it may
    remain in the narration -- otherwise a confident reference-based match would
    be scored as a hallucination when it was in fact readable.
    """
    MIN_USEFUL_PREFIX = 4   # shorter than this cannot identify a ref anyway
    destroyed = recoverable = 0

    for link, (t,), (c,) in by_scenario["garbled_narration"]:
        ref = t.order_id.replace("order_", "").upper()
        haystack = c.narration.upper().replace("-", "").replace("/", "").replace(" ", "")

        if "destroyed" in link.notes:
            surviving = max(
                (w for w in range(MIN_USEFUL_PREFIX, len(ref) + 1) if ref[:w] in haystack),
                default=0,
            )
            assert surviving == 0, (
                f"{link.link_id} is labelled destroyed but {surviving} chars of the "
                f"reference survive in the narration -- a prefix match on it would be "
                f"scored as a hallucination when it was actually sound"
            )
            destroyed += 1
        else:
            recoverable += 1
        assert link.resolution is Resolution.AUTO_MATCH

    assert destroyed > 0, "no destroyed-reference cases; the abstention path is untested"
    assert recoverable > 0, "no recoverable-reference cases; the LLM recovery path is untested"


# ---------- reproducibility and leakage ----------

def test_generation_is_deterministic_for_a_seed():
    a = generate(GenConfig(n_records=200, seed=7))
    b = generate(GenConfig(n_records=200, seed=7))
    assert [t.to_row() for t in a.gateway] == [t.to_row() for t in b.gateway]
    assert [c.to_row() for c in a.bank] == [c.to_row() for c in b.bank]
    assert [l.to_row() for l in a.links] == [l.to_row() for l in b.links]


def test_different_seeds_produce_genuinely_different_data():
    a = generate(GenConfig(n_records=200, seed=7))
    b = generate(GenConfig(n_records=200, seed=8))
    assert {t.txn_id for t in a.gateway}.isdisjoint({t.txn_id for t in b.gateway})


def test_row_order_carries_no_matching_signal(dataset):
    """Positional leakage check.

    If gateway row i tended to correspond to bank row i, a matcher could score
    well on ordering alone and the metrics would be meaningless.  Both files are
    shuffled; this asserts the shuffle worked.
    """
    gpos = {t.txn_id: i for i, t in enumerate(dataset.gateway)}
    bpos = {c.utr: i for i, c in enumerate(dataset.bank)}
    xs, ys = [], []
    for link in dataset.links:
        if len(link.txn_ids) == 1 and len(link.utrs) == 1:
            xs.append(gpos[link.txn_ids[0]])
            ys.append(bpos[link.utrs[0]])

    n = len(xs)
    assert n > 50
    rx = _ranks(xs)
    ry = _ranks(ys)
    mean = (n - 1) / 2
    cov = sum((a - mean) * (b - mean) for a, b in zip(rx, ry))
    var = sum((a - mean) ** 2 for a in rx) * sum((b - mean) ** 2 for b in ry)
    rho = cov / (var ** 0.5)
    assert abs(rho) < 0.2, f"row order leaks the answer (spearman rho={rho:.3f})"


def _ranks(values: list[int]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for rank, idx in enumerate(order):
        ranks[idx] = float(rank)
    return ranks


# ---------- distributional sanity ----------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_defect_rate_lands_near_the_configured_target(seed):
    ds = generate(GenConfig(n_records=500, seed=seed, defect_rate=0.30))
    counts = ds.summary()
    defect_share = 1 - counts["clean_1to1"] / sum(counts.values())
    assert 0.22 <= defect_share <= 0.40, f"seed {seed}: defect share {defect_share:.1%}"


def test_gateway_row_count_matches_the_request(dataset):
    assert len(dataset.gateway) == 500
    assert dataset.config_meta["n_gateway_rows"] == 500
