"""The work-item partition, loaded WITHOUT the answers attached to it.

``answer_key_cases.csv`` carries two different kinds of column, and the whole
integrity of this project depends on separating them:

    the partition            case_id, settlement_ids, bank_row_ids, event_ids
    the answers              expected_outcome, expected_exception_category, notes

The partition is legitimate agent input. It says which settlements and events
form one unit of work -- the same thing a queue, a batch job, or an operator
worklist would say in production. Nothing about it reveals what the right
answer is; it only says which records to consider together.

The answers are ground truth and must never reach the matcher. If they did,
every number this project publishes would be worthless, and the failure would
be invisible: the agent would simply be right, and nothing in the output would
show why.

Two files could enforce that separation. This one does it by construction --
``load_caseload`` physically cannot return the answer columns, because
``CaseUnit`` has nowhere to put them. A comment saying "do not read the
expected outcome" would be a convention; a dataclass without the field is a
guarantee, and the test suite asserts the guarantee holds.

WHY THE PARTITION IS FAIR INPUT AND NOT A HINT

    A sceptic is right to push here, so the answer should be stated plainly
    rather than defended when asked. Grouping is not a solution to this
    benchmark. Knowing that four settlements and twelve events belong to one
    case tells you nothing about whether the books tie out, which refund event
    explains an anonymous line, or whether an unsettled payment is an exception
    or was never going to settle. Every published baseline reads the same
    partition, so the floor and the agent are measured on identical input --
    which is the only way the gap between them means anything.

    The one thing the partition does provide is the same thing a production
    system gets for free: a boundary for "these records are about each other".
    Withholding it would not make the benchmark harder in an interesting way,
    it would add a clustering problem that nobody asked for and that no real
    reconciliation system has to solve, because settlement cycles arrive
    already grouped.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from recon.match.normalize import NormalizationError

__all__ = ["CaseUnit", "FORBIDDEN_COLUMNS", "load_caseload"]


# Columns that exist in the file and must never be parsed. Named here so the
# test suite can assert none of them appears on CaseUnit, and so a future
# schema change makes the intent obvious rather than silently widening what
# the agent can see.
FORBIDDEN_COLUMNS = frozenset(
    {"expected_outcome", "expected_exception_category", "notes"}
)

_PARTITION_COLUMNS = ("case_id", "settlement_ids", "bank_row_ids", "event_ids")


@dataclass(frozen=True, slots=True)
class CaseUnit:
    """One unit of reconciliation work: which records to consider together.

    There is deliberately no field for the expected outcome or category. That
    absence is the enforcement mechanism, not an oversight.
    """

    case_id: str
    settlement_ids: tuple[str, ...]
    bank_row_ids: tuple[str, ...]
    event_ids: tuple[str, ...]

    @property
    def has_settlements(self) -> bool:
        """Whether any settlement claims to contain this case.

        A case with no settlement is either a payment that should have settled
        and did not, or one that was never going to. Which of the two it is
        comes from lifecycle status, never from this flag.
        """
        return bool(self.settlement_ids)


def _split(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split("|") if part)


def load_caseload(directory: str | Path) -> tuple[CaseUnit, ...]:
    """Read the case partition, discarding every answer column.

    The reader selects columns by name rather than reading whole rows, so an
    answer column cannot be picked up by accident even if the schema grows.
    """
    path = Path(directory) / "answer_key_cases.csv"
    if not path.exists():
        raise NormalizationError(f"case partition missing: {path}")

    units: list[CaseUnit] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [column for column in _PARTITION_COLUMNS if column not in header]
        if missing:
            raise NormalizationError(
                f"{path.name}: missing partition column(s) {', '.join(missing)}"
            )
        for row in reader:
            case_id = (row.get("case_id") or "").strip()
            if not case_id:
                raise NormalizationError(f"{path.name}: a row carries no case_id")
            if case_id in seen:
                raise NormalizationError(f"{path.name}: duplicate case_id {case_id}")
            seen.add(case_id)
            units.append(
                CaseUnit(
                    case_id=case_id,
                    settlement_ids=_split(row.get("settlement_ids", "")),
                    bank_row_ids=_split(row.get("bank_row_ids", "")),
                    event_ids=_split(row.get("event_ids", "")),
                )
            )
    if not units:
        raise NormalizationError(f"{path.name} contains no cases")
    return tuple(units)


def iter_case_ids(units: Iterator[CaseUnit]) -> Iterator[str]:
    for unit in units:
        yield unit.case_id
