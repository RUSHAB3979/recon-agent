"""What the operator queue must guarantee.

Two of these tests exist because of a rule the project states outright, and
they are the two worth reading first. ``NO_ACTION`` cases must never reach the
queue -- padding an exception list with payments that were never going to
settle is a false positive the scorer measures by name. And the ranking must be
by exposure, because a queue in arrival order is a queue whose ordering is
uncorrelated with what matters.

The rest prove the artifact is trustworthy as a file: a total ordering, a
header even when empty, evidence that survives the round trip, and an action
for every category the pipeline can emit.
"""

from __future__ import annotations

import csv
from dataclasses import replace
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
)
from recon.match.controls import DUPLICATE_WARNING
from recon.match.exceptions import (
    COLUMNS,
    GENERIC_ACTION,
    RECOMMENDED_ACTION,
    ExceptionItem,
    build_exception_list,
    format_text,
    is_workable,
    main,
    summarise,
    write_exceptions,
)


@pytest.fixture(scope="module")
def directory(tmp_path_factory) -> Path:
    """One development batch. Dev is the only surface anything may be tuned on."""

    out = tmp_path_factory.mktemp("exceptions")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


@pytest.fixture(scope="module")
def run(directory):
    return reconcile(directory)


@pytest.fixture(scope="module")
def items(run):
    return build_exception_list(run)


# --------------------------------------------------------------------------
# what the queue must not contain


def test_no_action_cases_never_reach_the_queue(run, items):
    """The not_settleable trap. These are correct, not exceptions."""

    closed = {
        verdict.case_id for verdict in run.verdicts if verdict.outcome == NO_ACTION
    }
    assert closed, "the fixture must contain some NOT_SETTLEABLE cases to test this"
    assert closed.isdisjoint({item.case_id for item in items})


def test_reconciled_cases_never_reach_the_queue(run, items):
    """Including ones carrying a duplicate-export warning.

    The roll-up already used the deduplicated set, so no money moved. That
    warning is evidence the duplicates were seen; it is not work for anybody.
    """
    reconciled = {
        verdict.case_id for verdict in run.verdicts if verdict.outcome == RECONCILED
    }
    assert reconciled.isdisjoint({item.case_id for item in items})

    warned = {
        verdict.case_id
        for verdict in run.verdicts
        if verdict.outcome == RECONCILED and verdict.category == DUPLICATE_WARNING
    }
    assert warned, "the fixture must contain a duplicate-export warning to test this"
    assert warned.isdisjoint({item.case_id for item in items})


def test_workability_is_exactly_exception_and_abstain(run):
    for verdict in run.verdicts:
        assert is_workable(verdict) == (verdict.outcome in (EXCEPTION, ABSTAIN))


# --------------------------------------------------------------------------
# what the queue must contain


def test_every_unresolved_case_appears(run, items):
    """Rule 4: unmatched rows are never hidden."""

    expected = {
        verdict.case_id
        for verdict in run.verdicts
        if verdict.outcome in (EXCEPTION, ABSTAIN)
    }
    assert expected == {item.case_id for item in items}
    assert len(items) == len(expected), "no case may appear twice"


def test_every_item_carries_evidence(items):
    assert items
    for item in items:
        assert item.evidence.strip(), f"{item.case_id} has no evidence"


def test_every_item_carries_an_action_and_none_is_the_fallback(items):
    """The generic action exists so a break is never dropped, not to be used."""

    assert items
    for item in items:
        assert item.recommended_action.strip()
    assert not [item for item in items if item.recommended_action == GENERIC_ACTION]


def test_every_category_the_pipeline_emits_has_a_named_action(run):
    emitted = {
        verdict.category
        for verdict in run.verdicts
        if verdict.outcome in (EXCEPTION, ABSTAIN) and verdict.category
    }
    assert emitted <= set(RECOMMENDED_ACTION), (
        f"no recommended action for {sorted(emitted - set(RECOMMENDED_ACTION))}"
    )


def test_items_name_the_records_behind_the_case(run, items):
    """An operator cannot work a case_id. They need the settlement and the rows."""

    cases = {case.case_id: case for case in run.cases}
    for item in items:
        case = cases[item.case_id]
        assert item.settlement_ids == case.settlement_ids
        assert item.event_ids == case.event_ids
        assert item.bank_row_ids == case.bank_row_ids
    assert any(item.settlement_ids for item in items)
    assert any(item.event_ids for item in items)


# --------------------------------------------------------------------------
# ranking


def test_ranked_by_exposure_descending(items):
    exposures = [item.exposure_paise for item in items]
    assert exposures == sorted(exposures, reverse=True)


def test_ties_break_by_case_id_so_the_order_is_total(run):
    """Two runs over the same data must produce the same file, byte for byte."""

    first = build_exception_list(run)
    second = build_exception_list(run)
    assert [item.case_id for item in first] == [item.case_id for item in second]

    ties = [
        (a.case_id, b.case_id)
        for a, b in zip(first, first[1:])
        if a.exposure_paise == b.exposure_paise
    ]
    assert ties, "the fixture must contain an exposure tie to test the tiebreak"
    for earlier, later in ties:
        assert earlier < later


def test_exposure_is_never_negative(items):
    """Every measure in controls.py takes an absolute value.

    A negative exposure would sort a real break to the bottom of the queue,
    which is the one direction a ranking bug must never fail in.
    """
    for item in items:
        assert item.exposure_paise >= 0, item.case_id


def test_exposure_of_a_control_break_matches_its_findings(run):
    """The verdict's exposure is the sum of its hard findings, not just one.

    A case with three breaks is worth all three to whoever works it, so summing
    only the finding that named the category would under-rank the worst cases.
    """
    from recon.match.controls import settlement_findings

    checked = 0
    for verdict in run.verdicts:
        if verdict.outcome != EXCEPTION or verdict.category is None:
            continue
        case = next(c for c in run.cases if c.case_id == verdict.case_id)
        if not case.settlement_ids:
            continue  # CAPTURED_UNSETTLED is sized from events, not findings
        expected = 0
        for settlement_id in case.settlement_ids:
            for finding in settlement_findings(run.batch, settlement_id):
                if finding.is_hard:
                    expected += finding.exposure_paise
        assert verdict.exposure_paise == expected, verdict.case_id
        checked += 1
    assert checked, "the fixture must contain a settlement-level break"


def test_no_action_verdicts_carry_zero_exposure(run):
    for verdict in run.verdicts:
        if verdict.outcome == NO_ACTION:
            assert verdict.exposure_paise == 0


def test_summary_totals_agree_with_the_items(items):
    summary = summarise(items)
    assert sum(count for count, _ in summary.values()) == len(items)
    assert sum(exposure for _, exposure in summary.values()) == sum(
        item.exposure_paise for item in items
    )


# --------------------------------------------------------------------------
# the written artifact


def test_written_file_round_trips(items, tmp_path):
    path = write_exceptions(items, tmp_path / "exceptions.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(items)
    assert list(rows[0]) == list(COLUMNS)
    for row, item in zip(rows, items):
        assert row["case_id"] == item.case_id
        assert row["category"] == item.category
        assert int(row["exposure_paise"]) == item.exposure_paise
        # Full evidence, not a truncation. The CSV is what an operator works
        # from; the terminal renderer is the one allowed to abbreviate.
        assert row["evidence"] == item.evidence


def test_an_empty_queue_still_writes_a_header(tmp_path):
    """Nothing outstanding and the job never ran must not look the same."""

    path = write_exceptions([], tmp_path / "exceptions.csv")
    text = path.read_text(encoding="utf-8")
    assert text.strip() == ",".join(COLUMNS)


def test_rupee_rendering_is_a_string_and_pads_paise():
    item = ExceptionItem(
        case_id="case_00001",
        outcome=EXCEPTION,
        category="BANK_CREDIT_MISSING",
        exposure_paise=100_005,
        confidence=1.0,
        evidence="e",
        recommended_action="a",
        settlement_ids=(),
        event_ids=(),
        bank_row_ids=(),
    )
    assert item.exposure_rupees == "1000.05"
    assert replace(item, exposure_paise=7).exposure_rupees == "0.07"
    assert replace(item, exposure_paise=0).exposure_rupees == "0.00"


# --------------------------------------------------------------------------
# rendering and the CLI


def test_the_two_totals_are_never_added_together(items):
    """An abstention is unattributed money; a break is money that does not tie.

    Reporting one combined figure would present the first as a loss, and on
    this data the abstentions are the larger number -- so the combined total
    would be dominated by the part that is not actually missing.
    """
    text = format_text(items, limit=1)
    breaks = sum(i.exposure_paise for i in items if i.outcome == EXCEPTION)
    unattributed = sum(i.exposure_paise for i in items if i.outcome == ABSTAIN)
    assert breaks and unattributed, "the fixture needs both kinds to test this"
    assert f"{breaks / 100:,.2f}" in text
    assert f"{unattributed / 100:,.2f}" in text
    assert f"{(breaks + unattributed) / 100:,.2f}" not in text


def test_compact_rendering_drops_evidence_but_not_rows(items):
    full = format_text(items, limit=3)
    compact = format_text(items, limit=3, compact=True)
    assert "evidence:" in full
    assert "evidence:" not in compact
    for item in items[:3]:
        assert item.case_id in compact


def test_cli_writes_one_file_per_dataset(directory, tmp_path, capsys):
    out = tmp_path / "runs"
    assert main([str(directory), "--out-dir", str(out)]) == 0
    written = out / directory.name / "exceptions.csv"
    assert written.exists()
    assert str(written) in capsys.readouterr().out
