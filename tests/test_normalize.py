"""Tests for stage 0, the normalizer.

The normalizer is the only place raw CSV text becomes typed records, so every
guarantee the rest of the engine relies on is either established here or is not
established at all. Three of those guarantees are load-bearing enough to be
worth stating:

    MONEY IS INTEGER PAISE.  Not Decimal, not float, not str. A float would
    reintroduce exactly the representation error a reconciliation engine exists
    to detect, and it would do so at the boundary where a match is accepted or
    refused rather than somewhere visible.

    SETTLEABILITY IS DECIDED ONCE.  A CAPTURED payment that never settled is an
    exception; a FAILED one is correct behaviour. If that distinction were made
    at each call site, one site would eventually get it backwards and the
    NOT_SETTLEABLE trap would start producing false positives.

    A MALFORMED EXPORT IS NOT A FINANCE PROBLEM.  Bad input raises
    NormalizationError rather than flowing through as an exception category. A
    reconciliation report that lists a broken CSV alongside a missing bank
    credit is telling an operator to investigate the wrong thing.
"""

from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.normalize import (
    NEVER_SETTLES,
    SETTLEABLE_STATUSES,
    NormalizationError,
    load_batch,
    to_rupees,
)

FAMILIES = [Family.DEVELOPMENT, Family.PRIMARY, Family.STRESS]


def _write(tmp_path_factory, seed: int, family: Family) -> Path:
    out = tmp_path_factory.mktemp(f"norm-{family.value}-{seed}")
    write_dataset(generate(GenConfig(n_records=500, seed=seed, family=family)), out)
    return Path(out)


@pytest.fixture(scope="session", params=[42, 7, 99, 2026], ids=lambda s: f"seed{s}")
def seed(request) -> int:
    return request.param


@pytest.fixture(scope="session")
def batches(tmp_path_factory, seed):
    """One normalized batch per family, at one seed."""
    return {
        family: load_batch(_write(tmp_path_factory, seed, family))
        for family in FAMILIES
    }


# --------------------------------------------------------------------------
# money representation
# --------------------------------------------------------------------------


def test_every_money_field_is_a_python_int(batches):
    """No float, no Decimal, no numeric string anywhere inside the batch."""
    for family, batch in batches.items():
        for event in batch.events.values():
            assert type(event.amount_paise) is int, family
        for line in batch.details:
            for value in (
                line.gross_effect_paise,
                line.fee_paise,
                line.tax_paise,
                line.net_effect_paise,
            ):
                assert type(value) is int, family
        for settlement in batch.settlements.values():
            for value in (
                settlement.gross_payment_paise,
                settlement.refund_paise,
                settlement.fee_paise,
                settlement.tax_paise,
                settlement.net_amount_paise,
            ):
                assert type(value) is int, family
        for credits in batch.bank_by_utr.values():
            for credit in credits:
                assert type(credit.credit_amount_paise) is int, family


def test_to_rupees_is_exact_and_display_only():
    """Decimal appears only when a human has to read the number."""
    assert to_rupees(199900) == Decimal("1999.00")
    assert to_rupees(-1) == Decimal("-0.01")
    assert to_rupees(0) == Decimal("0.00")
    # The conversion must not be reversible through float, which is the usual
    # way paise leak back into binary floating point.
    assert str(to_rupees(1)) == "0.01"


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def test_settleability_partitions_every_status_present(batches):
    """No status in the data falls outside the two declared sets.

    If the generator ever introduced a third lifecycle state, ``is_settleable``
    would quietly classify it as not-settleable and the NOT_SETTLEABLE trap
    would start swallowing real exceptions. This test is what makes that a test
    failure rather than a silent drop in recall.
    """
    for family, batch in batches.items():
        statuses = {event.status for event in batch.events.values()}
        assert statuses <= (SETTLEABLE_STATUSES | NEVER_SETTLES), (family, statuses)
        assert not (SETTLEABLE_STATUSES & NEVER_SETTLES)


def test_signed_amount_follows_event_type(batches):
    for batch in batches.values():
        for event in batch.events.values():
            if event.event_type == "PAYMENT":
                assert event.signed_amount_paise == event.amount_paise
            else:
                assert event.signed_amount_paise == -event.amount_paise


# --------------------------------------------------------------------------
# deduplication
# --------------------------------------------------------------------------


def test_duplicate_detail_rows_are_collapsed_and_counted(tmp_path_factory, seed):
    """The batch holds unique lines, and remembers how many it discarded.

    Both halves matter. Collapsing without counting would make a duplicate
    export indistinguishable from a clean one, and the DUPLICATE_DETAIL_EXPORT
    class expects the engine to say it noticed.
    """
    directory = _write(tmp_path_factory, seed, Family.PRIMARY)
    raw = list(csv.DictReader((directory / "settlement_detail.csv").open(encoding="utf-8")))
    counts = Counter(row["detail_id"] for row in raw)
    expected_duplicates = sum(count - 1 for count in counts.values() if count > 1)

    batch = load_batch(directory)
    assert len(batch.details) == len(counts)
    assert batch.duplicate_line_total == expected_duplicates
    assert len({line.detail_id for line in batch.details}) == len(batch.details)


def test_details_by_settlement_covers_every_line(batches):
    for batch in batches.values():
        flattened = [
            line for lines in batch.details_by_settlement.values() for line in lines
        ]
        assert len(flattened) == len(batch.details)
        assert {line.detail_id for line in flattened} == {
            line.detail_id for line in batch.details
        }


# --------------------------------------------------------------------------
# derived views the ladder depends on
# --------------------------------------------------------------------------


def test_anonymous_lines_are_exactly_the_unjoined_refunds(batches):
    """The residual the ladder exists to resolve is refunds and only refunds.

    A payment line with no event_id would be a different problem entirely, and
    a gate designed for refund recovery would silently mishandle it.
    """
    for family, batch in batches.items():
        for line in batch.anonymous_lines:
            assert line.event_id is None
            assert line.line_type == "REFUND", family
        assert set(batch.anonymous_lines) <= set(batch.details)


def test_unconsumed_refunds_are_disjoint_from_referenced_events(batches):
    """Gate 1 in its simplest form: a claimed event is not also available.

    Two indexes disagreeing about which refunds are still available is exactly
    the bug that produces a confident wrong match, which is why both views are
    derived from one place rather than rebuilt per pass.
    """
    for family, batch in batches.items():
        referenced = batch.referenced_event_ids
        unconsumed = {event.event_id for event in batch.unconsumed_refunds()}
        assert not (referenced & unconsumed), family
        for event in batch.unconsumed_refunds():
            assert event.event_type == "REFUND"


def test_unconsumed_refunds_are_deterministically_ordered(batches):
    for batch in batches.values():
        ids = [event.event_id for event in batch.unconsumed_refunds()]
        assert ids == sorted(ids)


# --------------------------------------------------------------------------
# malformed input
# --------------------------------------------------------------------------


def _corrupt(directory: Path, name: str, mutate) -> Path:
    rows = list(csv.DictReader((directory / name).open(encoding="utf-8")))
    rows = mutate(rows)
    with (directory / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return directory


def test_missing_file_raises_normalization_error(tmp_path):
    with pytest.raises(NormalizationError):
        load_batch(tmp_path)


def test_duplicate_event_id_raises(tmp_path_factory, seed):
    directory = _write(tmp_path_factory, seed, Family.DEVELOPMENT)

    def duplicate(rows):
        return rows + [dict(rows[0])]

    _corrupt(directory, "gateway_ledger.csv", duplicate)
    with pytest.raises(NormalizationError, match="event_id"):
        load_batch(directory)


def test_non_numeric_amount_raises(tmp_path_factory, seed):
    directory = _write(tmp_path_factory, seed, Family.DEVELOPMENT)

    def wreck(rows):
        rows[0]["amount_paise"] = "1999.00"
        return rows

    _corrupt(directory, "gateway_ledger.csv", wreck)
    with pytest.raises(NormalizationError):
        load_batch(directory)
