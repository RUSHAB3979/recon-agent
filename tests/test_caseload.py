"""Tests for the answer-key boundary.

``answer_key_cases.csv`` carries both the work-item partition, which is fair
agent input, and the expected outcome, which is ground truth. The whole
integrity of every number this project publishes rests on the second never
reaching the matcher, and a comment saying so would be a convention rather than
a guarantee.

So the guarantee is structural -- ``CaseUnit`` has no field an answer could be
stored in -- and these tests check the structure rather than the intention. The
first test is deliberately written so that it FAILS if the answer columns ever
stop existing in the file, because a boundary test that passes vacuously is
worse than none: it would keep reporting success long after it stopped checking
anything.
"""

from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.caseload import FORBIDDEN_COLUMNS, CaseUnit, load_caseload
from recon.match.normalize import NormalizationError


@pytest.fixture(scope="session", params=[42, 7, 99, 2026], ids=lambda s: f"seed{s}")
def directory(request, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp(f"caseload-{request.param}")
    write_dataset(
        generate(GenConfig(n_records=500, seed=request.param, family=Family.PRIMARY)),
        out,
    )
    return Path(out)


def test_the_answer_columns_really_are_in_the_file(directory):
    """Guards against this whole test module going vacuous.

    If the generator stopped writing expected_outcome, every assertion below
    would still pass while checking nothing at all.
    """
    with (directory / "answer_key_cases.csv").open(encoding="utf-8") as handle:
        header = set(next(csv.reader(handle)))
    assert FORBIDDEN_COLUMNS <= header


def test_case_unit_has_nowhere_to_put_an_answer():
    """The enforcement mechanism, asserted directly.

    Not "the loader does not populate these" but "there is no field to
    populate". A future contributor adding an expected_outcome field for
    convenience fails here rather than silently invalidating the benchmark.
    """
    names = {field.name for field in fields(CaseUnit)}
    assert not (names & FORBIDDEN_COLUMNS)
    assert names == {"case_id", "settlement_ids", "bank_row_ids", "event_ids"}


def test_no_attribute_on_a_loaded_unit_holds_an_answer(directory):
    """Slots plus frozen means the guarantee cannot be patched around at runtime."""
    units = load_caseload(directory)
    for unit in units[:5]:
        for column in FORBIDDEN_COLUMNS:
            assert not hasattr(unit, column)
        with pytest.raises((AttributeError, TypeError)):
            setattr(unit, "expected_outcome", "RECONCILED")


def test_partition_matches_the_answer_key_exactly(directory):
    """Same cases, same membership -- the agent sees the whole caseload.

    An agent silently receiving fewer cases than the scorer expects would post a
    flattering accuracy on a subset, which is the failure this benchmark is
    built to prevent in every other place too.
    """
    units = load_caseload(directory)
    rows = list(csv.DictReader((directory / "answer_key_cases.csv").open(encoding="utf-8")))
    assert len(units) == len(rows)
    assert [unit.case_id for unit in units] == [row["case_id"] for row in rows]

    by_id = {unit.case_id: unit for unit in units}
    for row in rows:
        unit = by_id[row["case_id"]]
        assert unit.settlement_ids == tuple(v for v in row["settlement_ids"].split("|") if v)
        assert unit.bank_row_ids == tuple(v for v in row["bank_row_ids"].split("|") if v)
        assert unit.event_ids == tuple(v for v in row["event_ids"].split("|") if v)


def test_has_settlements_is_the_only_derived_signal(directory):
    units = load_caseload(directory)
    assert any(unit.has_settlements for unit in units)
    assert any(not unit.has_settlements for unit in units)
    for unit in units:
        assert unit.has_settlements == bool(unit.settlement_ids)


def test_missing_partition_raises(tmp_path):
    with pytest.raises(NormalizationError, match="case partition missing"):
        load_caseload(tmp_path)


def test_duplicate_case_id_raises(tmp_path, directory):
    rows = list(csv.DictReader((directory / "answer_key_cases.csv").open(encoding="utf-8")))
    target = tmp_path / "answer_key_cases.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows + [dict(rows[0])])
    with pytest.raises(NormalizationError, match="duplicate case_id"):
        load_caseload(tmp_path)
