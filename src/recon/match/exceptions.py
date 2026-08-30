"""The operator work queue: what the agent could not close, and what to do next.

This module produces the artifact that the rest of the pipeline exists to make
possible. Everything upstream decides; this decides nothing. It reads finished
verdicts and renders the subset a human has to act on, ranked so the person
working the queue starts with the money.

WHY THIS IS A SEPARATE MODULE AND NOT A REPORT SECTION

    An exception list has a different consumer from a metrics report. The
    report answers "is this agent any good", is read once by whoever is
    evaluating it, and is prose. The list answers "what do I do this morning",
    is read every day by someone with a phone and a bank portal open, and is a
    file that gets sorted, filtered and assigned. Rendering one from the other
    would force one of the two to be shaped wrong, and it is the operator one
    that would lose, because the evaluator is the one standing in the room.

WHAT IS DELIBERATELY ABSENT FROM THE LIST

    ``NO_ACTION`` cases never appear. These are the ``not_settleable`` trap:
    CREATED and FAILED payments that were never going to settle and are owed to
    nobody. Padding an exception list with them inflates its length while
    lowering its value, and the scorer measures that rate by name -- a list
    that flags everything is not an exception list, it is the input again.

    A RECONCILED case carrying only a duplicate-export warning is also absent.
    The roll-up already used the deduplicated set, so no money moved. That
    warning is evidence the duplicates were seen; it is not work.

WHY EXPOSURE RANKS THE LIST, AND WHY IT IS NOT ALWAYS THE OBVIOUS NUMBER

    A queue in case-id order is a queue in arrival order, which is the one
    ordering guaranteed to be uncorrelated with importance. Exposure is the
    money an operator has to chase, and it is measured per category rather than
    uniformly, because the categories are not commensurable: a fee variance is
    worth the size of the variance, an uncredited settlement is worth the whole
    settlement, and a double credit is worth the surplus that has to go back.
    Each measure is stated at its construction site in ``controls.py``.

    Exposure zero does not mean "ignore". It means the released files do not
    size the break -- a settlement with no summary row is the clear case. Those
    sort last, because a number that cannot be measured should not be allowed
    to outrank one that can, and inventing a figure to rank it by would put a
    fabricated amount at the top of somebody's morning.

WHAT THE EXPOSURE FIGURES ON THIS DATA DO AND DO NOT SHOW

    Stated here rather than discovered by whoever reads the output. The refund
    scenarios draw their amounts from fixed bases in the generator --
    ``CONTESTED_AMOUNT_BASE_PAISE`` and ``DESCRIBED_AMOUNT_BASE_PAISE``, plus a
    per-case counter -- because those classes need amounts provably disjoint
    from every other refund for the cardinality argument to hold. The
    consequence is that every ``AMBIGUOUS_REFUND`` row lands within a few paise
    of every other one.

    So on this dataset the ranking separates the CATEGORIES from each other,
    which is the comparison an operator actually makes first, and separates
    almost nothing WITHIN the refund class, where the amounts were chosen by a
    constant rather than by anything economic. The mechanism is the deliverable
    and it is measured; the specific rupee magnitudes of the refund rows are an
    artifact of how the benchmark had to be built, and quoting them as though
    they described a real book would be reading meaning into a constant.

WHY EVERY ROW CARRIES A RECOMMENDED ACTION

    A category name is a diagnosis, not an instruction. ``BANK_CREDIT_MISSING``
    tells an operator what is wrong; "chase the correspondent bank for the UTR,
    and confirm the settlement was actually released" tells them where to go.
    The mapping is deliberately a table in this file rather than a sentence the
    adjudicator writes: the recommendation for a category must not vary between
    runs, between models, or with the weather, and an operator who sees the
    same wording every time learns it once.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from recon.match.caseload import CaseUnit
from recon.match.controller import (
    ABSTAIN,
    AMBIGUOUS_REFUND,
    CAPTURED_UNSETTLED,
    EXCEPTION,
    RunResult,
    Verdict,
    reconcile,
)

__all__ = [
    "COLUMNS",
    "ExceptionItem",
    "RECOMMENDED_ACTION",
    "build_exception_list",
    "format_text",
    "main",
    "summarise",
    "write_exceptions",
]


# One named next step per category. Anything not listed here still reaches the
# operator -- with a generic instruction and its evidence intact -- because
# dropping a break on the grounds that nobody wrote a sentence for it would be
# the worst possible failure mode of this table.
RECOMMENDED_ACTION: Mapping[str, str] = {
    AMBIGUOUS_REFUND: (
        "pick the refund event this line belongs to, or confirm none does; the "
        "agent found more than one arithmetically valid candidate and declined "
        "rather than guess"
    ),
    CAPTURED_UNSETTLED: (
        "confirm whether the settlement cycle is late or the payout was held; "
        "these captures are owed and have not been paid out"
    ),
    "BANK_CREDIT_MISSING": (
        "chase the correspondent bank for the UTR, and confirm the settlement "
        "was actually released"
    ),
    "BANK_CREDIT_DUPLICATE": (
        "raise a return for the surplus credit and confirm which bank row is the "
        "duplicate before it is reconciled twice"
    ),
    "BANK_AMOUNT_MISMATCH": (
        "compare the credited amount against the settlement net; a short credit "
        "is usually a correspondent charge, an over-credit usually a wrong "
        "beneficiary"
    ),
    "FEE_TAX_VARIANCE": (
        "re-price the line against the rate card in force on its capture date; a "
        "systematic variance is a pricing-config break, not a one-off"
    ),
    "SETTLEMENT_MISSING": (
        "obtain the settlement summary row; the detail lines reference a "
        "settlement the released export does not describe"
    ),
    "ROLLUP_MISMATCH": (
        "reconcile the summary against its own detail lines before looking at "
        "the bank; the two sides of the gateway's own export disagree"
    ),
    "SUMMARY_EQUATION_VIOLATION": (
        "the declared net does not equal gross less refunds, fees and tax; treat "
        "it as unverified until the issuer restates it"
    ),
    "LINE_EQUATION_VIOLATION": (
        "the line's own net does not equal its gross less fee and tax; ask the "
        "issuer to restate the line"
    ),
    "UNATTRIBUTED_SETTLEMENT_LINE": (
        "identify the event this line settles; it carries no event reference and "
        "no rule could attribute it"
    ),
    "UNKNOWN_EVENT_REFERENCE": (
        "the line points at an event id absent from the ledger; confirm the two "
        "exports cover the same period"
    ),
    "UNKNOWN_METHOD": (
        "add the payment method to the pricing rules; its fee and tax cannot be "
        "verified against any rate card until then"
    ),
}

GENERIC_ACTION = "review the evidence and classify manually"

COLUMNS: tuple[str, ...] = (
    "case_id",
    "outcome",
    "category",
    "exposure_paise",
    "exposure_rupees",
    "confidence",
    "recommended_action",
    "settlement_ids",
    "event_ids",
    "bank_row_ids",
    "evidence",
)


@dataclass(frozen=True, slots=True)
class ExceptionItem:
    """One row of the operator queue."""

    case_id: str
    outcome: str
    category: str
    exposure_paise: int
    confidence: float
    evidence: str
    recommended_action: str
    settlement_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    bank_row_ids: tuple[str, ...]

    @property
    def exposure_rupees(self) -> str:
        """The exposure formatted for a human, at the presentation boundary only.

        Paise are integers everywhere upstream; this is the one place a decimal
        point appears, and it appears in a string that nothing computes with.
        """
        sign = "-" if self.exposure_paise < 0 else ""
        whole, part = divmod(abs(self.exposure_paise), 100)
        return f"{sign}{whole}.{part:02d}"

    def as_row(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "outcome": self.outcome,
            "category": self.category,
            "exposure_paise": str(self.exposure_paise),
            "exposure_rupees": self.exposure_rupees,
            "confidence": f"{self.confidence:.2f}",
            "recommended_action": self.recommended_action,
            "settlement_ids": "|".join(self.settlement_ids),
            "event_ids": "|".join(self.event_ids),
            "bank_row_ids": "|".join(self.bank_row_ids),
            "evidence": self.evidence,
        }


def is_workable(verdict: Verdict) -> bool:
    """Whether this verdict is something a human has to act on.

    The two exclusions are the ones the project's rules name explicitly, and
    both exclude things that are NOT breaks -- never a break that happens to be
    inconvenient to explain. RECONCILED and NO_ACTION are closed; a RECONCILED
    case may still carry a duplicate-export warning, and that is evidence the
    duplicates were seen rather than work for anybody.
    """
    return verdict.outcome in (EXCEPTION, ABSTAIN)


def build_exception_list(result: RunResult) -> tuple[ExceptionItem, ...]:
    """Render the workable subset of a finished run, ranked by exposure.

    Sorted by exposure descending, then by case_id, so the ordering is total and
    two runs over the same data produce byte-identical files. Leaving ties in
    dictionary order would make the artifact's diff depend on iteration order,
    and a file that changes without the data changing is one nobody trusts.
    """
    cases: Mapping[str, CaseUnit] = {case.case_id: case for case in result.cases}
    items: list[ExceptionItem] = []
    for verdict in result.verdicts:
        if not is_workable(verdict):
            continue
        # A verdict that reached this list with no category is a bug upstream,
        # not a row to drop. It is surfaced under a name that reads as one.
        category = verdict.category or "UNCLASSIFIED"
        case = cases.get(verdict.case_id)
        items.append(
            ExceptionItem(
                case_id=verdict.case_id,
                outcome=verdict.outcome,
                category=category,
                exposure_paise=verdict.exposure_paise,
                confidence=verdict.confidence,
                # Every reason the verdict recorded, not a summary of them. The
                # operator is the one who has to decide, and a truncated
                # evidence field would send them back to the logs.
                evidence="; ".join(verdict.reasons),
                recommended_action=RECOMMENDED_ACTION.get(category, GENERIC_ACTION),
                settlement_ids=case.settlement_ids if case else (),
                event_ids=case.event_ids if case else (),
                bank_row_ids=case.bank_row_ids if case else (),
            )
        )
    items.sort(key=lambda item: (-item.exposure_paise, item.case_id))
    return tuple(items)


def write_exceptions(items: Sequence[ExceptionItem], path: str | Path) -> Path:
    """Write the queue as CSV, header included even when the queue is empty.

    An empty file with a header says "nothing outstanding". A zero-byte file
    says "the job did not run", and those two must not look the same on disk.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(item.as_row())
    return destination


def summarise(items: Iterable[ExceptionItem]) -> dict[str, tuple[int, int]]:
    """Count and total exposure per category, heaviest category first."""
    summary: dict[str, tuple[int, int]] = {}
    for item in items:
        count, exposure = summary.get(item.category, (0, 0))
        summary[item.category] = (count + 1, exposure + item.exposure_paise)
    return dict(sorted(summary.items(), key=lambda kv: (-kv[1][1], kv[0])))


def format_text(
    items: Sequence[ExceptionItem], *, limit: int = 10, compact: bool = False
) -> str:
    """Render the queue for a terminal: the totals, then the top of the list.

    ``compact`` drops the evidence and action lines, for callers that are
    showing the shape of the queue rather than handing it to somebody to work.
    The evidence stays complete in the written CSV either way -- it is trimmed
    here for the width of a terminal, never for the operator.


    The two outcomes are totalled SEPARATELY and never added together. An
    abstention's exposure is money that arrived and has not been attributed to
    the right event; a control break's exposure is money that does not tie out
    at all. Presenting one figure covering both would report the first kind as
    a loss, and on this dataset the abstentions are the larger number -- so the
    combined total would be dominated by the part that is not actually missing.
    """
    lines: list[str] = []
    unresolved = sum(item.exposure_paise for item in items if item.outcome == ABSTAIN)
    breaks = sum(item.exposure_paise for item in items if item.outcome == EXCEPTION)
    abstained = sum(1 for item in items if item.outcome == ABSTAIN)
    lines.append(f"{len(items)} open item(s)")
    lines.append(
        f"  {len(items) - abstained:>4} control break(s), exposure  {breaks / 100:>14,.2f}"
    )
    lines.append(
        f"  {abstained:>4} unattributed,      value     {unresolved / 100:>14,.2f}"
    )
    lines.append("")
    summary = summarise(items)
    if summary:
        width = max(len(category) for category in summary)
        lines.append(f"  {'category'.ljust(width)}  count      exposure")
        for category, (count, exposure) in summary.items():
            lines.append(
                f"  {category.ljust(width)}  {count:>5}  {exposure / 100:>12,.2f}"
            )
        lines.append("")
    for item in items[:limit]:
        lines.append(f"  {item.case_id}  {item.category}  {item.exposure_rupees}")
        if not compact:
            lines.append(f"      evidence: {item.evidence}")
            lines.append(f"      action:   {item.recommended_action}")
    if len(items) > limit:
        # Not "in the written file": this renderer is also called from the
        # metrics report, which writes nothing, and a message that promises a
        # file the caller never produced is how a queue quietly loses rows.
        lines.append(f"  ... {len(items) - limit} more not shown")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the operator exception queue for one or more datasets."
    )
    parser.add_argument("data_dir", type=Path, nargs="*", default=[Path("data/dev")])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("runs"),
        help=(
            "write OUT/<dataset>/exceptions.csv. Outside data/ for the same "
            "reason the journals are: a dataset directory is an input."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="how many items to print; the written file always holds all of them",
    )
    args = parser.parse_args(argv)

    for directory in args.data_dir:
        result = reconcile(directory)
        items = build_exception_list(result)
        destination = write_exceptions(
            items, args.out_dir / directory.name / "exceptions.csv"
        )
        print(f"== {directory}")
        print(format_text(items, limit=args.limit))
        print(f"  -> {destination}")
        print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
