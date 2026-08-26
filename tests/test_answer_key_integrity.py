"""Structural proofs that the new answer key describes the emitted evidence.

These tests re-derive controls and recovery candidate counts from source rows.
Trusting a scenario label to describe its own defect would make every benchmark
metric circular, which is exactly what the constructive answer key avoids.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from recon.datagen import Family, GenConfig, Resolution, Scenario, generate, write_dataset
from recon.datagen.config import (
    DEVELOPMENT_CASE_SHARES,
    GST_RATE_BPS,
    PRIMARY_CASE_SHARES,
    STRESS_CASE_SHARES,
)
from recon.datagen.entities import Dataset, round_half_up


def _unique_details(dataset: Dataset):
    unique = []
    seen = set()
    for detail in dataset.details:
        if detail.detail_id not in seen:
            seen.add(detail.detail_id)
            unique.append(detail)
    return unique


def _case_maps(dataset: Dataset):
    events = {event.event_id: event for event in dataset.gateway}
    summaries = {summary.settlement_id: summary for summary in dataset.summaries}
    banks = {row.bank_row_id: row for row in dataset.bank}
    return events, summaries, banks


def _assert_referential_integrity(dataset: Dataset) -> None:
    events, summaries, banks = _case_maps(dataset)
    event_hits = Counter(event_id for case in dataset.cases for event_id in case.event_ids)
    settlement_hits = Counter(
        settlement_id for case in dataset.cases for settlement_id in case.settlement_ids
    )
    bank_hits = Counter(bank_id for case in dataset.cases for bank_id in case.bank_row_ids)

    assert set(event_hits) == set(events)
    assert set(settlement_hits) == set(summaries)
    assert set(bank_hits) == set(banks)
    assert all(hit == 1 for hit in event_hits.values())
    assert all(hit == 1 for hit in settlement_hits.values())
    assert all(hit == 1 for hit in bank_hits.values())

    for detail in dataset.details:
        assert detail.settlement_id in summaries
        if detail.event_id is not None:
            assert detail.event_id in events


def test_every_record_belongs_to_exactly_one_primary_case(dataset):
    _assert_referential_integrity(dataset)


def test_every_record_belongs_to_exactly_one_stress_case(stress_dataset):
    _assert_referential_integrity(stress_dataset)


def _assert_identifiers(dataset: Dataset) -> None:
    for label, values in (
        ("event_id", [event.event_id for event in dataset.gateway]),
        ("settlement_id", [summary.settlement_id for summary in dataset.summaries]),
        ("bank_row_id", [row.bank_row_id for row in dataset.bank]),
        ("bank_ref", [row.bank_ref for row in dataset.bank]),
        ("case_id", [case.case_id for case in dataset.cases]),
    ):
        assert len(values) == len(set(values)), f"duplicate {label}"

    duplicate_detail_ids = {
        detail_id for detail_id, count in Counter(
            detail.detail_id for detail in dataset.details
        ).items() if count > 1
    }
    duplicate_utrs = {
        utr for utr, count in Counter(row.utr for row in dataset.bank).items() if count > 1
    }
    duplicate_case_settlements = {
        settlement_id
        for case in dataset.cases
        if case.scenario is Scenario.DUPLICATE_DETAIL_EXPORT
        for settlement_id in case.settlement_ids
    }
    duplicate_bank_utrs = {
        row.utr
        for case in dataset.cases
        if case.scenario is Scenario.BANK_CREDIT_DUPLICATE
        for row in dataset.bank
        if row.bank_row_id in case.bank_row_ids
    }
    assert duplicate_detail_ids
    assert all(
        detail.settlement_id in duplicate_case_settlements
        for detail in dataset.details
        if detail.detail_id in duplicate_detail_ids
    )
    assert duplicate_utrs == duplicate_bank_utrs


def test_only_named_primary_scenarios_repeat_identifiers(dataset):
    _assert_identifiers(dataset)


def test_only_named_stress_scenarios_repeat_identifiers(stress_dataset):
    _assert_identifiers(stress_dataset)


def _assert_control_equations(dataset: Dataset) -> None:
    events = {event.event_id: event for event in dataset.gateway}
    pricing = {rule.method: rule for rule in dataset.pricing_rules}
    settlement_to_case = {
        settlement_id: case
        for case in dataset.cases
        for settlement_id in case.settlement_ids
    }
    pricing_failures: dict[str, list[str]] = defaultdict(list)

    for detail in dataset.details:
        assert detail.net_effect_paise == (
            detail.gross_effect_paise - detail.fee_paise - detail.tax_paise
        ), detail.detail_id
        assert isinstance(detail.gross_effect_paise, int)
        assert isinstance(detail.fee_paise, int)
        assert isinstance(detail.tax_paise, int)
        assert isinstance(detail.net_effect_paise, int)

        if detail.event_id is None:
            assert detail.line_type == "REFUND"
            assert detail.fee_paise == detail.tax_paise == 0
            continue
        event = events[detail.event_id]
        rule = pricing[event.method]
        expected_fee = round_half_up(event.amount_paise * rule.fee_rate_bps, 10_000)
        if detail.fee_paise != expected_fee:
            pricing_failures[settlement_to_case[detail.settlement_id].case_id].append("fee")
        expected_tax = round_half_up(detail.fee_paise * rule.gst_rate_bps, 10_000)
        if detail.tax_paise != expected_tax:
            pricing_failures[settlement_to_case[detail.settlement_id].case_id].append("tax")

    details_by_settlement: dict[str, list] = defaultdict(list)
    for detail in _unique_details(dataset):
        details_by_settlement[detail.settlement_id].append(detail)
    bank_by_utr: dict[str, list] = defaultdict(list)
    for row in dataset.bank:
        bank_by_utr[row.utr].append(row)

    for summary in dataset.summaries:
        case = settlement_to_case[summary.settlement_id]
        details = details_by_settlement[summary.settlement_id]
        gross_payment = sum(
            detail.gross_effect_paise for detail in details if detail.line_type == "PAYMENT"
        )
        refund = -sum(
            detail.gross_effect_paise for detail in details if detail.line_type == "REFUND"
        )
        fee = sum(detail.fee_paise for detail in details)
        tax = sum(detail.tax_paise for detail in details)

        assert summary.net_amount_paise == (
            summary.gross_payment_paise
            - summary.refund_paise
            - summary.fee_paise
            - summary.tax_paise
        )
        assert (
            summary.gross_payment_paise,
            summary.refund_paise,
            summary.fee_paise,
            summary.tax_paise,
            summary.net_amount_paise,
            summary.line_count,
        ) == (
            gross_payment,
            refund,
            fee,
            tax,
            sum(detail.net_effect_paise for detail in details),
            len(details),
        )
        assert summary.net_amount_paise > 0

        bank_rows = bank_by_utr[summary.utr]
        bank_total = sum(row.credit_amount_paise for row in bank_rows)
        if case.scenario is Scenario.BANK_CREDIT_MISSING:
            assert not bank_rows and bank_total != summary.net_amount_paise
        elif case.scenario is Scenario.BANK_CREDIT_DUPLICATE:
            assert len(bank_rows) == 2 and bank_total != summary.net_amount_paise
            assert all(row.credit_amount_paise == summary.net_amount_paise for row in bank_rows)
        else:
            assert len(bank_rows) == 1
            assert bank_total == summary.net_amount_paise

    for case in dataset.cases:
        failures = pricing_failures.get(case.case_id, [])
        if case.scenario is Scenario.FEE_TAX_VARIANCE:
            assert len(failures) == 1
            assert failures[0] in {"fee", "tax"}
        else:
            assert not failures, f"{case.case_id} has incidental pricing failures {failures}"


def test_every_primary_control_equation_has_only_its_named_break(dataset):
    _assert_control_equations(dataset)


def test_every_stress_control_equation_has_only_its_named_break(stress_dataset):
    _assert_control_equations(stress_dataset)


def _assert_duplicate_rollups(dataset: Dataset) -> None:
    details_by_settlement: dict[str, list] = defaultdict(list)
    for detail in dataset.details:
        details_by_settlement[detail.settlement_id].append(detail)
    summaries = {summary.settlement_id: summary for summary in dataset.summaries}

    for case in dataset.cases:
        if case.scenario is not Scenario.DUPLICATE_DETAIL_EXPORT:
            continue
        assert len(case.settlement_ids) == 1
        settlement_id = case.settlement_ids[0]
        rows = details_by_settlement[settlement_id]
        assert len(rows) > len({row.detail_id for row in rows})
        assert sum(row.net_effect_paise for row in rows) != summaries[settlement_id].net_amount_paise
        unique = []
        seen = set()
        for row in rows:
            if row.detail_id not in seen:
                seen.add(row.detail_id)
                unique.append(row)
        assert sum(row.net_effect_paise for row in unique) == summaries[settlement_id].net_amount_paise


def test_duplicate_primary_detail_export_rolls_up_unique_ids(dataset):
    _assert_duplicate_rollups(dataset)


def test_duplicate_stress_detail_export_rolls_up_unique_ids(stress_dataset):
    _assert_duplicate_rollups(stress_dataset)


def _recovery_candidates(dataset: Dataset, case):
    unique_details = _unique_details(dataset)
    referenced = {detail.event_id for detail in unique_details if detail.event_id}
    by_settlement: dict[str, list] = defaultdict(list)
    for detail in unique_details:
        by_settlement[detail.settlement_id].append(detail)
    payments_by_txn: dict[str, list] = defaultdict(list)
    for event in dataset.gateway:
        if event.event_type == "PAYMENT":
            payments_by_txn[event.txn_id].append(event)
    summaries = {summary.settlement_id: summary for summary in dataset.summaries}

    recoveries = []
    for settlement_id in case.settlement_ids:
        summary = summaries[settlement_id]
        identified_refund = -sum(
            detail.gross_effect_paise
            for detail in by_settlement[settlement_id]
            if detail.line_type == "REFUND" and detail.event_id
        )
        delta = summary.refund_paise - identified_refund
        if delta <= 0:
            continue

        amount_candidates = [
            event
            for event in dataset.gateway
            if event.event_type == "REFUND" and event.amount_paise == delta
        ]
        failures_by_event: dict[str, set[str]] = {}
        after_gates = []
        for event in amount_candidates:
            failures: set[str] = set()
            if event.event_id in referenced:
                failures.add("GATE_1")
            age = (summary.settlement_date - event.created_at.date()).days
            if not 0 <= age <= 4:
                failures.add("GATE_3")
            parent_settled = any(
                parent.event_id in referenced and parent.status == "PROCESSED"
                for parent in payments_by_txn[event.txn_id]
            )
            if not parent_settled:
                failures.add("GATE_5")
            failures_by_event[event.event_id] = failures
            if not failures:
                after_gates.append(event)
        recoveries.append(
            {
                "settlement_id": settlement_id,
                "amount": delta,
                "amount_candidates": amount_candidates,
                "after_gates": after_gates,
                "failures": failures_by_event,
            }
        )
    return recoveries


def _candidate_counts(dataset: Dataset, case) -> list[int]:
    return [
        len(recovery["after_gates"])
        for recovery in _recovery_candidates(dataset, case)
    ]


def _contested_distractor_labels(case) -> dict[str, str]:
    marker = "distractors="
    assert marker in case.notes
    encoded = case.notes.split(marker, 1)[1]
    return dict(item.split(":", 1) for item in encoded.split("|") if item)


def _assert_recovery_cardinality(dataset: Dataset) -> None:
    corroborated = [
        case for case in dataset.cases if case.scenario is Scenario.CORROBORATED_REFUND
    ]
    assert corroborated
    for case in corroborated:
        assert _candidate_counts(dataset, case) == [1]
        assert case.expected_outcome is Resolution.RECONCILED

    ambiguous = [
        case for case in dataset.cases if case.scenario is Scenario.AMBIGUOUS_REFUND
    ]
    assert ambiguous
    for case in ambiguous:
        counts = _candidate_counts(dataset, case)
        assert counts and all(count >= 2 for count in counts)
        assert case.expected_outcome is Resolution.ABSTAIN


def _assert_contested_refunds(dataset: Dataset) -> None:
    contested = [
        case for case in dataset.cases if case.scenario is Scenario.CONTESTED_REFUND
    ]
    assert contested
    allocated_event_ids = {allocation.event_id for allocation in dataset.allocations}
    observed_labels: set[str] = set()
    for case in contested:
        recoveries = _recovery_candidates(dataset, case)
        assert len(recoveries) == 1
        recovery = recoveries[0]
        assert 2 <= len(recovery["amount_candidates"]) <= 4
        assert len(recovery["after_gates"]) == 1
        assert case.expected_outcome is Resolution.RECONCILED

        survivor_id = recovery["after_gates"][0].event_id
        labels = _contested_distractor_labels(case)
        observed_labels.update(labels.values())
        case_amount_candidate_ids = {
            event.event_id
            for event in recovery["amount_candidates"]
            if event.event_id in case.event_ids
        }
        assert set(labels) == case_amount_candidate_ids - {survivor_id}
        assert survivor_id in allocated_event_ids
        survivor_allocations = [
            allocation
            for allocation in dataset.allocations
            if allocation.event_id == survivor_id
        ]
        assert survivor_allocations
        assert {
            allocation.settlement_id for allocation in survivor_allocations
        } == {recovery["settlement_id"]}
        # A distractor must not be allocated to the settlement carrying the
        # contested delta -- that would hand the delta a second valid answer.
        # It may be allocated elsewhere, and a GATE_1 distractor always is:
        # failing gate 1 MEANS another settlement already consumed it, and the
        # export says so on a detail line. Requiring it to be allocated nowhere
        # deleted those pairs from the key, so an agent that merely read the
        # export was scored as having invented them.
        assert not set(labels) & {
            allocation.event_id
            for allocation in dataset.allocations
            if allocation.settlement_id == recovery["settlement_id"]
        }
        for event_id, label in labels.items():
            named_gate = "_".join(label.split("_")[:2])
            assert named_gate in {"GATE_1", "GATE_3", "GATE_5"}
            assert recovery["failures"][event_id] == {named_gate}

    assert "GATE_1_CONSUMED" in observed_labels
    assert "GATE_3_TOO_OLD" in observed_labels
    assert "GATE_3_AFTER_SETTLEMENT" in observed_labels
    assert "GATE_5_BROKEN_LINEAGE" in observed_labels


def test_primary_refund_recovery_has_exactly_one_candidate(dataset):
    _assert_recovery_cardinality(dataset)
    _assert_contested_refunds(dataset)


def test_stress_refund_recovery_separates_one_from_many(stress_dataset):
    _assert_recovery_cardinality(stress_dataset)
    _assert_contested_refunds(stress_dataset)


def _assert_contested_and_ambiguous_do_not_overlap(dataset: Dataset) -> None:
    contested = [
        case for case in dataset.cases if case.scenario is Scenario.CONTESTED_REFUND
    ]
    ambiguous = [
        case for case in dataset.cases if case.scenario is Scenario.AMBIGUOUS_REFUND
    ]
    contested_settlements = {
        settlement_id for case in contested for settlement_id in case.settlement_ids
    }
    ambiguous_settlements = {
        settlement_id for case in ambiguous for settlement_id in case.settlement_ids
    }
    contested_amounts = {
        recovery["amount"]
        for case in contested
        for recovery in _recovery_candidates(dataset, case)
    }
    ambiguous_amounts = {
        recovery["amount"]
        for case in ambiguous
        for recovery in _recovery_candidates(dataset, case)
    }
    assert contested_settlements.isdisjoint(ambiguous_settlements)
    assert contested_amounts.isdisjoint(ambiguous_amounts)
    assert all(
        len(recovery["after_gates"]) == 1
        for case in contested
        for recovery in _recovery_candidates(dataset, case)
    )
    assert all(
        len(recovery["after_gates"]) >= 2
        for case in ambiguous
        for recovery in _recovery_candidates(dataset, case)
    )


def test_primary_contested_and_ambiguous_never_overlap(dataset):
    _assert_contested_and_ambiguous_do_not_overlap(dataset)


def test_stress_contested_and_ambiguous_never_overlap(stress_dataset):
    _assert_contested_and_ambiguous_do_not_overlap(stress_dataset)


def _assert_contested_distractor_structure(dataset: Dataset) -> None:
    events = {event.event_id: event for event in dataset.gateway}
    details_by_event: dict[str, list] = defaultdict(list)
    for detail in _unique_details(dataset):
        if detail.event_id:
            details_by_event[detail.event_id].append(detail)
    captured_case_event_ids = {
        event_id
        for case in dataset.cases
        if case.scenario is Scenario.CAPTURED_UNSETTLED
        for event_id in case.event_ids
    }

    for case in dataset.cases:
        if case.scenario is not Scenario.CONTESTED_REFUND:
            continue
        recoveries = _recovery_candidates(dataset, case)
        assert len(recoveries) == 1
        target_settlement_id = recoveries[0]["settlement_id"]
        for refund_id, label in _contested_distractor_labels(case).items():
            refund = events[refund_id]
            parents = [
                event
                for event in dataset.gateway
                if event.event_type == "PAYMENT" and event.txn_id == refund.txn_id
            ]
            assert len(parents) == 1
            parent = parents[0]
            if label == "GATE_1_CONSUMED":
                references = details_by_event[refund_id]
                assert len(references) == 1
                assert references[0].settlement_id != target_settlement_id
            elif label == "GATE_5_BROKEN_LINEAGE":
                assert parent.status == "CAPTURED"
                assert not details_by_event[parent.event_id]
                assert parent.event_id not in captured_case_event_ids


def test_primary_contested_distractors_preserve_case_boundaries(dataset):
    _assert_contested_distractor_structure(dataset)


def test_stress_contested_distractors_preserve_case_boundaries(stress_dataset):
    _assert_contested_distractor_structure(stress_dataset)


def _assert_disposition(dataset: Dataset) -> None:
    settled_event_ids = {detail.event_id for detail in dataset.details if detail.event_id}
    for case in dataset.cases:
        events = [event for event in dataset.gateway if event.event_id in case.event_ids]
        assert 3 <= len(events) <= 7
        if case.scenario is Scenario.NOT_SETTLEABLE:
            assert not case.settlement_ids and not case.bank_row_ids
            assert all(event.status in {"CREATED", "FAILED"} for event in events)
            assert not set(case.event_ids) & settled_event_ids
            assert case.expected_outcome is Resolution.NO_ACTION
        elif case.scenario is Scenario.CAPTURED_UNSETTLED:
            assert not case.settlement_ids and not case.bank_row_ids
            assert all(event.status == "CAPTURED" for event in events)
            assert not set(case.event_ids) & settled_event_ids
            assert case.expected_outcome is Resolution.EXCEPTION


def test_primary_disposition_is_not_conflated_with_an_exception(dataset):
    _assert_disposition(dataset)


def test_stress_disposition_is_not_conflated_with_an_exception(stress_dataset):
    _assert_disposition(stress_dataset)


def _assert_allocations(dataset: Dataset) -> None:
    events, summaries, banks = _case_maps(dataset)
    contested_distractor_ids = {
        event_id
        for case in dataset.cases
        if case.scenario is Scenario.CONTESTED_REFUND
        for event_id in _contested_distractor_labels(case)
    }
    actual = {
        (allocation.event_id, allocation.settlement_id, allocation.bank_row_id)
        for allocation in dataset.allocations
    }
    assert len(actual) == len(dataset.allocations)

    for allocation in dataset.allocations:
        assert allocation.event_id in events
        assert allocation.settlement_id in summaries
        if allocation.bank_row_id is not None:
            assert allocation.bank_row_id in banks
        owners = [
            case
            for case in dataset.cases
            if allocation.event_id in case.event_ids
            and allocation.settlement_id in case.settlement_ids
            and (
                allocation.bank_row_id is None
                or allocation.bank_row_id in case.bank_row_ids
            )
        ]
        assert len(owners) == 1

    bank_by_utr: dict[str, list] = defaultdict(list)
    for row in dataset.bank:
        bank_by_utr[row.utr].append(row)
    for detail in _unique_details(dataset):
        if detail.event_id is None:
            continue
        summary = summaries[detail.settlement_id]
        bank_rows = bank_by_utr[summary.utr]
        expected = {
            (detail.event_id, detail.settlement_id, row.bank_row_id)
            for row in bank_rows
        } or {(detail.event_id, detail.settlement_id, None)}
        assert expected <= actual


def test_primary_allocations_are_consistent_with_cases(dataset):
    _assert_allocations(dataset)


def test_stress_allocations_are_consistent_with_cases(stress_dataset):
    _assert_allocations(stress_dataset)


def _assert_schema_and_money(dataset: Dataset) -> None:
    assert len(dataset.gateway) == 500
    assert len(dataset.cases) == 100
    assert dataset.batch_config[0].n_gateway_events == 500
    assert dataset.batch_config[0].n_cases == 100
    assert dataset.config_meta["scenario_case_counts"] == dataset.summary()
    for event in dataset.gateway:
        assert isinstance(event.amount_paise, int) and event.amount_paise > 0
    for summary in dataset.summaries:
        for value in (
            summary.gross_payment_paise,
            summary.refund_paise,
            summary.fee_paise,
            summary.tax_paise,
            summary.net_amount_paise,
        ):
            assert isinstance(value, int)
    for row in dataset.bank:
        assert isinstance(row.credit_amount_paise, int)
        assert row.narration.startswith("NEFT CR: ")
        assert row.narration.endswith(f" {row.utr} RAZORPAY SETTLEMENT")
        assert not any(event.order_id in row.narration for event in dataset.gateway)


def test_primary_schema_counts_money_and_shares(dataset):
    _assert_schema_and_money(dataset)
    # Every one of the twelve classes has to appear in the headline family.
    # BANK_CREDIT_MISSING and BANK_CREDIT_DUPLICATE were absent here until the
    # shares were corrected, which let an agent with no bank-side handling at
    # all score identically on the published number.
    assert dataset.summary() == {
        "AMBIGUOUS_REFUND": 4,
        "BANK_CREDIT_DUPLICATE": 2,
        "BANK_CREDIT_MISSING": 3,
        "CAPTURED_UNSETTLED": 6,
        "CONTESTED_REFUND": 8,
        "CORROBORATED_REFUND": 6,
        "DESCRIBED_REFUND": 6,
        "DUPLICATE_DETAIL_EXPORT": 8,
        "FEE_TAX_VARIANCE": 4,
        "NOT_SETTLEABLE": 6,
        "REFUND_LATER_CYCLE": 10,
        "STRAIGHT_THROUGH": 37,
    }
    assert set(dataset.summary()) == {scenario.value for scenario in Scenario}


def test_stress_schema_counts_money_and_shares(stress_dataset):
    _assert_schema_and_money(stress_dataset)
    assert stress_dataset.summary() == {
        "AMBIGUOUS_REFUND": 6,
        "BANK_CREDIT_DUPLICATE": 5,
        "BANK_CREDIT_MISSING": 7,
        "CAPTURED_UNSETTLED": 7,
        "CONTESTED_REFUND": 14,
        "CORROBORATED_REFUND": 10,
        "DESCRIBED_REFUND": 10,
        "DUPLICATE_DETAIL_EXPORT": 10,
        "FEE_TAX_VARIANCE": 8,
        "NOT_SETTLEABLE": 3,
        "REFUND_LATER_CYCLE": 10,
        "STRAIGHT_THROUGH": 10,
    }


def test_all_case_share_tables_are_complete_integer_percentages():
    for shares in (
        PRIMARY_CASE_SHARES,
        STRESS_CASE_SHARES,
        DEVELOPMENT_CASE_SHARES,
    ):
        assert sum(share for _, share in shares) == 100
    assert {scenario for scenario, _ in DEVELOPMENT_CASE_SHARES} == set(Scenario)
    assert dict(DEVELOPMENT_CASE_SHARES) == {
        Scenario.STRAIGHT_THROUGH: 16,
        Scenario.CONTESTED_REFUND: 14,
        Scenario.CORROBORATED_REFUND: 10,
        Scenario.DESCRIBED_REFUND: 10,
        Scenario.REFUND_LATER_CYCLE: 9,
        Scenario.DUPLICATE_DETAIL_EXPORT: 9,
        Scenario.AMBIGUOUS_REFUND: 8,
        Scenario.CAPTURED_UNSETTLED: 6,
        Scenario.FEE_TAX_VARIANCE: 6,
        Scenario.NOT_SETTLEABLE: 5,
        Scenario.BANK_CREDIT_MISSING: 4,
        Scenario.BANK_CREDIT_DUPLICATE: 3,
    }


def test_refund_events_share_the_parent_transaction(dataset):
    payments = {event.txn_id: event for event in dataset.gateway if event.event_type == "PAYMENT"}
    refunds = [event for event in dataset.gateway if event.event_type == "REFUND"]
    assert refunds
    for refund in refunds:
        parent = payments[refund.txn_id]
        assert refund.order_id == parent.order_id
        assert refund.amount_paise <= parent.amount_paise


def test_round_half_up_is_integer_only_and_tie_correct():
    assert round_half_up(1, 2) == 1
    assert round_half_up(-1, 2) == -1
    assert round_half_up(1, 3) == 0
    with pytest.raises(TypeError):
        round_half_up(1.0, 2)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [42, 7, 99, 2026])
def test_generation_is_byte_identical_for_same_config(seed):
    config = GenConfig(n_records=200, seed=seed, family=Family.STRESS)
    first = generate(config)
    second = generate(config)
    with TemporaryDirectory(prefix="recon-determinism-") as temporary:
        root = Path(temporary)
        first_paths = write_dataset(first, root / "first")
        second_paths = write_dataset(second, root / "second")
        assert set(first_paths) == set(second_paths)
        for key in first_paths:
            assert first_paths[key].read_bytes() == second_paths[key].read_bytes(), key


def test_different_seeds_produce_disjoint_event_ids():
    first = generate(GenConfig(n_records=200, seed=7))
    second = generate(GenConfig(n_records=200, seed=8))
    assert {event.event_id for event in first.gateway}.isdisjoint(
        {event.event_id for event in second.gateway}
    )
