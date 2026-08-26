"""Accounting controls: the checks that decide whether a settlement ties out.

Attribution and control are different jobs and this module does only the
second. The ladder in ``passes.py`` decides WHICH event explains a line; this
module decides whether the resulting books balance, and if they do not, which
named break to report. Keeping them apart matters because they fail in
different ways: a bad attribution is a wrong answer, while a control break is a
correct answer about bad data, and a module that mixed them would report the
first as the second.

WHY THESE CHECKS AND NOT OTHERS

    Every check here is derivable from the released files alone -- a line
    equation, four roll-ups against the declared summary, the summary equation,
    and the bank credit against the declared net. Nothing here consults the
    answer key, and nothing here needs a tolerance, because every one of these
    is an identity that either holds exactly or does not hold at all. A
    reconciliation engine that applied a tolerance to an identity would be
    hiding its own arithmetic errors inside a fuzz factor.

    Fee and tax are validated on PAYMENT lines only. Refund fee treatment is a
    commercial policy that varies per settlement -- some refunds return the
    processing fee, some do not -- so ``pricing_rules.csv`` does not state a
    rule the engine could assert about a refund line without inventing one.

ORDERING IS PART OF THE CONTRACT

    Findings are produced in a fixed order and the first hard finding names the
    exception category. That is a policy, not an accident, and it is written
    down here so a future check inserted in the middle cannot silently change
    which category a case reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from recon.match.normalize import Batch, DetailLine, Settlement

__all__ = [
    "DUPLICATE_WARNING",
    "Finding",
    "round_half_up",
    "settlement_findings",
]


# Evidence rather than a break. The roll-up already used the deduplicated set,
# so a settlement carrying duplicate export rows still ties out; the warning
# exists so the report can say the duplicates were seen and discounted, which
# is what distinguishes handling them from failing to notice them.
DUPLICATE_WARNING = "DUPLICATE_DETAIL_EXPORT_WARNING"


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer half-up rounding, ties away from zero.

    Deliberately implemented here rather than imported from the generator. The
    agent is a consumer of a released dataset and must be runnable against one
    without the code that produced it; importing the generator arithmetic would
    make a rounding bug in the generator invisible, because both sides would be
    wrong in exactly the same way.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


@dataclass(frozen=True, slots=True)
class Finding:
    """One named control break, with the arithmetic that produced it.

    ``detail`` carries both sides of the comparison rather than the word
    "mismatch". An operator asked to act on an exception needs the size of the
    break to decide whether it is a rounding artefact or a missing payment, and
    a message that omits the numbers forces them back into the raw files.
    """

    category: str
    detail: str

    @property
    def is_hard(self) -> bool:
        return self.category != DUPLICATE_WARNING


def _line_findings(
    batch: Batch, line: DetailLine, attributed: frozenset[str]
) -> list[Finding]:
    """Checks that apply to one detail line in isolation."""
    findings: list[Finding] = []

    if not line.line_equation_holds:
        findings.append(
            Finding(
                "LINE_EQUATION_VIOLATION",
                f"{line.detail_id}: net {line.net_effect_paise} != gross "
                f"{line.gross_effect_paise} - fee {line.fee_paise} - tax {line.tax_paise}",
            )
        )

    if line.is_anonymous:
        if line.detail_id in attributed:
            # The ladder proved which event this line refers to, so there is
            # nothing left to report. Note the direction of the dependency:
            # controls consume the ladder result, never the reverse.
            return findings
        findings.append(
            Finding(
                "UNATTRIBUTED_SETTLEMENT_LINE",
                f"{line.detail_id}: {line.line_type} line carries no event_id",
            )
        )
        return findings

    event_id = line.event_id
    assert event_id is not None
    if event_id not in batch.events:
        findings.append(
            Finding(
                "UNKNOWN_EVENT_REFERENCE",
                f"{line.detail_id}: event_id {event_id} is not in the ledger",
            )
        )
        return findings

    if line.line_type != "PAYMENT":
        return findings

    event = batch.events[event_id]
    rule = batch.pricing.get(event.method)
    if rule is None:
        findings.append(
            Finding(
                "UNKNOWN_METHOD",
                f"{line.detail_id}: method {event.method} is not in pricing rules",
            )
        )
        return findings

    want_fee = round_half_up(line.gross_effect_paise * rule.fee_rate_bps, 10_000)
    want_tax = round_half_up(want_fee * rule.gst_rate_bps, 10_000)
    if line.fee_paise != want_fee:
        findings.append(
            Finding(
                "FEE_TAX_VARIANCE",
                f"{line.detail_id}: fee {line.fee_paise} != expected {want_fee} "
                f"({rule.fee_rate_bps}bps of {line.gross_effect_paise})",
            )
        )
    elif line.tax_paise != want_tax:
        # elif, not if: a wrong fee makes the tax wrong by construction, and
        # reporting both would double-count one break as two.
        findings.append(
            Finding(
                "FEE_TAX_VARIANCE",
                f"{line.detail_id}: tax {line.tax_paise} != expected {want_tax} "
                f"({rule.gst_rate_bps}bps of {want_fee})",
            )
        )
    return findings


def _rollup(lines: Iterable[DetailLine]) -> tuple[int, int, int, int, int]:
    gross_payment = refund = fee = tax = net = 0
    for line in lines:
        if line.line_type == "PAYMENT":
            gross_payment += line.gross_effect_paise
        else:
            # Refund lines carry a negative gross; the summary declares refunds
            # as a positive magnitude it subtracts, so the sign is flipped once
            # here rather than at each comparison.
            refund += -line.gross_effect_paise
        fee += line.fee_paise
        tax += line.tax_paise
        net += line.net_effect_paise
    return gross_payment, refund, fee, tax, net


def settlement_findings(
    batch: Batch, settlement_id: str, attributed: frozenset[str] = frozenset()
) -> list[Finding]:
    """Every control break visible in one settlement, in fixed order.

    ``attributed`` names detail lines the ladder has already explained. An
    anonymous line in that set is not reported, because it is no longer
    unattributed -- which is exactly the headroom the ladder exists to convert
    into resolved cases.
    """
    settlement: Settlement | None = batch.settlements.get(settlement_id)
    if settlement is None:
        return [Finding("SETTLEMENT_MISSING", f"{settlement_id} has no summary row")]

    lines = batch.details_by_settlement.get(settlement_id, ())
    findings: list[Finding] = []
    for line in lines:
        findings.extend(_line_findings(batch, line, attributed))

    gross_payment, refund, fee, tax, net = _rollup(lines)
    for label, declared, computed in (
        ("gross_payment", settlement.gross_payment_paise, gross_payment),
        ("refund", settlement.refund_paise, refund),
        ("fee", settlement.fee_paise, fee),
        ("tax", settlement.tax_paise, tax),
    ):
        if declared != computed:
            findings.append(
                Finding(
                    "ROLLUP_MISMATCH",
                    f"{label} {declared} != unique-line roll-up {computed}",
                )
            )

    declared_net = settlement.net_amount_paise
    if declared_net != settlement.control_net_paise:
        findings.append(
            Finding(
                "SUMMARY_EQUATION_VIOLATION",
                f"net {declared_net} != gross - refund - fee - tax = "
                f"{settlement.control_net_paise}",
            )
        )
    if declared_net != net:
        findings.append(
            Finding("ROLLUP_MISMATCH", f"net {declared_net} != unique-line roll-up {net}")
        )

    credits = batch.credits_for(settlement)
    if not credits:
        findings.append(
            Finding("BANK_CREDIT_MISSING", f"no bank row for utr {settlement.utr}")
        )
    elif len(credits) > 1:
        findings.append(
            Finding(
                "BANK_CREDIT_DUPLICATE",
                f"utr {settlement.utr} credited {len(credits)} times "
                f"({', '.join(credit.bank_row_id for credit in credits)})",
            )
        )
    else:
        (credit,) = credits
        credited = credit.credit_amount_paise
        if credited != declared_net:
            findings.append(
                Finding(
                    "BANK_AMOUNT_MISMATCH",
                    f"credit {credited} != settlement net {declared_net}",
                )
            )

    duplicates = batch.duplicate_detail_counts.get(settlement_id, 0)
    if duplicates:
        findings.append(
            Finding(DUPLICATE_WARNING, f"{duplicates} duplicate detail_id row(s) ignored")
        )
    return findings
