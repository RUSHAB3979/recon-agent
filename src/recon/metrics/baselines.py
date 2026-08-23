"""B1 -- the published baseline that defines the benchmark's difficulty floor.

This module exists to be RUN BY A SCEPTIC.  The headline claim of this project
is a reconciliation rate, and a reconciliation rate means nothing on its own: a
dataset can always be built where a dictionary join scores 97%.  So the number
is quoted against a floor,

    D = 1 - (cases B1 resolves correctly / total cases)

and B1 is shipped as runnable code rather than asserted as a percentage.  A
panel member can read this file, run it against the released CSVs, and reproduce
D without trusting the generator.  That reproducibility is the whole point; an
asserted floor would be worth nothing.

WHAT B1 IS ALLOWED TO DO
    - exact joins on event_id, settlement_id and utr
    - respect signed line amounts (refunds are negative)
    - deduplicate settlement_detail on its primary key before rolling up
    - validate fee and tax against pricing_rules.csv
    - check every control equation in the data spec
    - decide disposition from lifecycle status (created/failed never settle)

B2 IS B1 PLUS EXACTLY ONE RULE
    Attribute an anonymous refund line to any unconsumed refund event whose
    amount reproduces it exactly -- no window, no lineage, no uniqueness check.

    B2 exists because publishing only B1 would have been dishonest.  Measuring
    B1 alone answers "how hard is this for a script forbidden from refund
    recovery", which is not a question anyone asked.  B2 answers the question a
    sceptic actually asks: how much is left once you add the one line of SQL
    that the restriction was hiding.  Quote match rates against B2, not B1.

WHAT B1 IS NOT ALLOWED TO DO
    - read the narration
    - attempt refund-delta recovery: if a detail line carries no event_id, B1
      cannot attribute it and reports an exception
    - abstain: B1 always commits to an outcome

Two of those deserve a defence, because together they set D.

Deduplication is INCLUDED deliberately.  Dropping duplicate rows on a primary
key is one line of code, and a baseline that gave up there would hand the agent
several points of headline for free.  A floor that flatters the thing it
measures is not a floor.

Refund recovery is EXCLUDED deliberately, and this is the honest boundary
between the two.  An anonymous refund line is precisely where an exact join has
nothing to join on.  B1 does the competent thing a SQL reconciliation script
does -- it reports the line it cannot attribute -- and that is a *correct*
engineering response, not a strawman.  The agent's advantage is not that it
guesses; it is that it can PROVE which refund event the line refers to, by
showing exactly one candidate survives every admissibility gate.  Where two
candidates survive the agent abstains, and B1 still guesses.  That gap is the
capability being measured.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TypedDict

# A hook that decides whether an anonymous settlement detail line can be
# attributed.  Returning True means "attributed, report nothing".
RecoveryHook = Optional[Callable[[dict[str, str]], bool]]


class ScoreResult(TypedDict):
    """The scorer's output, typed so a renamed key fails at check time.

    ``false_attributions`` is separate from the accuracy counters on purpose.  A
    contested delta charged to the wrong refund event still produces the
    expected outcome, so an attribution error is invisible in ``b1_correct``
    unless it is counted in its own right.
    """

    total_cases: int
    b1_correct: int
    b1_accuracy: float
    difficulty_floor_D: float
    false_attributions: int
    per_scenario: dict[str, tuple[int, int]]


SETTLEABLE_STATUSES = {"CAPTURED", "PROCESSED"}
NEVER_SETTLES = {"CREATED", "FAILED"}
DUPLICATE_WARNING = "DUPLICATE_DETAIL_EXPORT_WARNING"


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer half-up rounding.

    Ties round away from zero, matching the generator's convention exactly.

    This is deliberately a SECOND implementation rather than an import of
    ``recon.datagen.entities.round_half_up``.  B1 exists to check the data; if
    it borrowed the generator's arithmetic, a rounding bug in the generator
    would be invisible because both sides would be wrong identically.  An
    independent reimplementation makes the fee equations a real check.  The two
    are asserted equal over an exhaustive range in ``tests/test_baselines.py``,
    which is what stops independence from decaying into divergence.

    Money is integer paise everywhere; a float would reintroduce exactly the
    representation error a reconciliation engine exists to detect.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _split(value: str) -> list[str]:
    return [v for v in value.split("|") if v]


@dataclass
class Batch:
    """One generated dataset, indexed for exact joins."""

    events: dict[str, dict[str, str]]
    detail_by_settlement: dict[str, list[dict[str, str]]]
    summaries: dict[str, dict[str, str]]
    bank_by_utr: dict[str, list[dict[str, str]]]
    pricing: dict[str, tuple[int, int]]
    cases: list[dict[str, str]]
    # (settlement_id, event_id) pairs the answer key considers correct.  Outcome
    # alone cannot score a recovery: attributing a delta to the WRONG refund
    # event still yields RECONCILED, which is the expected outcome, so a
    # baseline that guesses would score full marks for a false match.
    allocations: set[tuple[str, str]]

    @classmethod
    def load(cls, d: Path) -> "Batch":
        detail: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _read(d / "settlement_detail.csv"):
            detail[row["settlement_id"]].append(row)
        bank: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _read(d / "bank_statement.csv"):
            bank[row["utr"]].append(row)
        return cls(
            events={r["event_id"]: r for r in _read(d / "gateway_ledger.csv")},
            detail_by_settlement=dict(detail),
            summaries={r["settlement_id"]: r for r in _read(d / "settlement_summary.csv")},
            bank_by_utr=dict(bank),
            pricing={
                r["method"]: (int(r["fee_rate_bps"]), int(r["gst_rate_bps"]))
                for r in _read(d / "pricing_rules.csv")
            },
            cases=_read(d / "answer_key_cases.csv"),
            allocations={
                (r["settlement_id"], r["event_id"])
                for r in _read(d / "answer_key_allocations.csv")
            },
        )


@dataclass
class Verdict:
    """B1's decision on one case, with the evidence that produced it."""

    case_id: str
    outcome: str
    category: str | None = None
    reasons: list[str] = field(default_factory=list)
    # Every (settlement_id, event_id) attribution this verdict asserts.  A
    # verdict that claims nothing is scored on its outcome alone; one that
    # claims an attribution must also get the attribution right.
    claims: list[tuple[str, str]] = field(default_factory=list)


def _line_findings(
    batch: Batch, line: dict[str, str], recover: RecoveryHook = None
) -> list[tuple[str, str]]:
    """Checks that apply to a single settlement detail line.

    ``recover`` is the ONLY difference between B1 and B2.  B1 passes None and
    reports every anonymous line; B2 passes a hook that attributes the line by
    exact amount lookup.  Keeping it a hook rather than a second copy of this
    function means the two baselines cannot silently drift apart in any check
    other than the one being compared.
    """
    findings: list[tuple[str, str]] = []
    gross = int(line["gross_effect_paise"])
    fee = int(line["fee_paise"])
    tax = int(line["tax_paise"])
    net = int(line["net_effect_paise"])
    did = line["detail_id"]

    if net != gross - fee - tax:
        findings.append(
            ("LINE_EQUATION_VIOLATION",
             f"{did}: net {net} != gross {gross} - fee {fee} - tax {tax}")
        )

    event_id = line["event_id"]
    if not event_id:
        # No identifier to join on.  B1 cannot attribute this line and does not
        # guess -- see the module docstring for why this boundary is the honest
        # one rather than a handicap.  B2 supplies a hook and does guess.
        if recover is not None and recover(line):
            return findings
        findings.append(
            (
                "UNATTRIBUTED_SETTLEMENT_LINE",
                f"{did}: {line['line_type']} line carries no event_id",
            )
        )
        return findings

    if event_id not in batch.events:
        findings.append(
            ("UNKNOWN_EVENT_REFERENCE", f"{did}: event_id {event_id} is not in the ledger")
        )
        return findings

    # Pricing rules are validated on payment lines only.  Refund fee treatment
    # is a commercial policy that varies per settlement (some refunds return the
    # fee, some do not), so it is not a rule B1 can assert from
    # pricing_rules.csv without inventing one.
    if line["line_type"] != "PAYMENT":
        return findings

    event = batch.events[event_id]
    rates = batch.pricing.get(event["method"])
    if rates is None:
        findings.append(
            ("UNKNOWN_METHOD", f"{did}: method {event['method']} not in pricing rules")
        )
        return findings

    fee_bps, gst_bps = rates
    want_fee = round_half_up(gross * fee_bps, 10_000)
    want_tax = round_half_up(want_fee * gst_bps, 10_000)
    if fee != want_fee:
        findings.append(("FEE_TAX_VARIANCE", f"{did}: fee {fee} != expected {want_fee}"))
    elif tax != want_tax:
        findings.append(("FEE_TAX_VARIANCE", f"{did}: tax {tax} != expected {want_tax}"))
    return findings


def _settlement_findings(
    batch: Batch, settlement_id: str, recover: RecoveryHook = None
) -> list[tuple[str, str]]:
    """Every control-total failure B1 can see in one settlement.

    Returns (category, human-readable reason) pairs.  The order of checks is
    fixed so output is deterministic and diffable across runs.
    """
    summary = batch.summaries.get(settlement_id)
    if summary is None:
        return [("SETTLEMENT_MISSING", f"{settlement_id} has no summary row")]

    # Deduplicate on the primary key.  Duplicate detail_id rows are an export
    # artefact, not a second movement of money -- the summary was produced from
    # the unique set, so rolling up the raw rows double-counts.
    unique: dict[str, dict[str, str]] = {}
    duplicates = 0
    for line in batch.detail_by_settlement.get(settlement_id, []):
        if line["detail_id"] in unique:
            duplicates += 1
            continue
        unique[line["detail_id"]] = line
    lines = list(unique.values())

    findings: list[tuple[str, str]] = []
    gross_payment = refund = fee_total = tax_total = net_total = 0
    for line in lines:
        findings.extend(_line_findings(batch, line, recover))
        gross = int(line["gross_effect_paise"])
        if line["line_type"] == "PAYMENT":
            gross_payment += gross
        else:
            refund += -gross
        fee_total += int(line["fee_paise"])
        tax_total += int(line["tax_paise"])
        net_total += int(line["net_effect_paise"])

    for label, declared, computed in (
        ("gross_payment", int(summary["gross_payment_paise"]), gross_payment),
        ("refund", int(summary["refund_paise"]), refund),
        ("fee", int(summary["fee_paise"]), fee_total),
        ("tax", int(summary["tax_paise"]), tax_total),
    ):
        if declared != computed:
            findings.append(
                ("ROLLUP_MISMATCH", f"{label} {declared} != unique-line roll-up {computed}")
            )

    declared_net = int(summary["net_amount_paise"])
    control = (
        int(summary["gross_payment_paise"])
        - int(summary["refund_paise"])
        - int(summary["fee_paise"])
        - int(summary["tax_paise"])
    )
    if declared_net != control:
        findings.append(
            ("SUMMARY_EQUATION_VIOLATION",
             f"net {declared_net} != gross - refund - fee - tax = {control}")
        )
    if declared_net != net_total:
        findings.append(
            ("ROLLUP_MISMATCH", f"net {declared_net} != unique-line roll-up {net_total}")
        )

    credits = batch.bank_by_utr.get(summary["utr"], [])
    if not credits:
        findings.append(("BANK_CREDIT_MISSING", f"no bank row for utr {summary['utr']}"))
    elif len(credits) > 1:
        findings.append(
            ("BANK_CREDIT_DUPLICATE", f"utr {summary['utr']} credited {len(credits)} times")
        )
    else:
        credited = int(credits[0]["credit_amount_paise"])
        if credited != declared_net:
            findings.append(
                ("BANK_AMOUNT_MISMATCH", f"credit {credited} != settlement net {declared_net}")
            )

    if duplicates:
        # Evidence, not a failure: the roll-up above already used the unique
        # set, so a deduplicating baseline ties out and moves on.
        findings.append((DUPLICATE_WARNING, f"{duplicates} duplicate detail_id row(s) ignored"))
    return findings


def b1_case(
    batch: Batch, case: dict[str, str], recover: RecoveryHook = None
) -> Verdict:
    """Verdict on a single case.  With no hook this is B1; with one it is B2."""
    case_id = case["case_id"]
    settlement_ids = _split(case["settlement_ids"])
    event_ids = _split(case["event_ids"])

    if not settlement_ids:
        statuses = {batch.events[e]["status"] for e in event_ids if e in batch.events}
        if statuses and statuses <= NEVER_SETTLES:
            # The trap class.  Reporting these as exceptions would be a false
            # positive, and the harness measures that rate by name.
            return Verdict(
                case_id, "NO_ACTION",
                reasons=[f"all {len(event_ids)} events are created/failed"],
            )
        unsettled = sorted(s for s in statuses if s in SETTLEABLE_STATUSES)
        return Verdict(
            case_id, "EXCEPTION", "CAPTURED_UNSETTLED",
            [f"{len(event_ids)} events with status {unsettled} appear in no settlement"],
        )

    findings: list[tuple[str, str]] = []
    for sid in settlement_ids:
        findings.extend(_settlement_findings(batch, sid, recover))

    reasons = [reason for _, reason in findings]
    hard = [f for f in findings if f[0] != DUPLICATE_WARNING]
    if hard:
        # First finding wins the category.  B1 has no ranking policy, and
        # inventing one would be a capability the baseline is not meant to have.
        return Verdict(case_id, "EXCEPTION", hard[0][0], reasons)
    return Verdict(
        case_id, "RECONCILED", DUPLICATE_WARNING if findings else None, reasons
    )


def run_b1(batch: Batch) -> list[Verdict]:
    return [b1_case(batch, case) for case in batch.cases]


def _unconsumed_refunds(batch: Batch) -> dict[int, list[str]]:
    """Refund events no detail line references, indexed by exact amount."""
    referenced: set[str] = set()
    for lines in batch.detail_by_settlement.values():
        seen: set[str] = set()
        for line in lines:
            if line["detail_id"] in seen:
                continue
            seen.add(line["detail_id"])
            if line["event_id"]:
                referenced.add(line["event_id"])

    index: dict[int, list[str]] = defaultdict(list)
    for event_id, event in batch.events.items():
        if event["event_type"] == "REFUND" and event_id not in referenced:
            index[int(event["amount_paise"])].append(event_id)
    # Sorted so a tie is broken identically on every run.  B2 must be
    # reproducible even though its tie-break is arbitrary.
    return {amount: sorted(ids) for amount, ids in index.items()}


def run_b2(batch: Batch) -> list[Verdict]:
    """B2 -- B1 plus exactly ONE additional rule.

    That rule is: an anonymous refund detail line is attributed to any unconsumed
    refund event whose amount reproduces it exactly.  No date window, no lineage
    check, no uniqueness requirement, no narration -- and, like B1, no abstention.

    B2 exists because publishing only B1 would have been dishonest.  B1 is
    forbidden from refund recovery, and it is fair to ask whether that
    restriction is what creates the difficulty rather than the data.  B2 answers
    that question in public: it is the strongest baseline reachable by adding one
    line of SQL, so the floor it sets is the one a sceptic would actually build.

    Where B2 fails is therefore the real measurement.  It cannot tell a contested
    delta from an uncontested one, because it never checks whether a second
    candidate exists, and it cannot decline, because a floor does not abstain.
    """
    index = {amount: list(ids) for amount, ids in _unconsumed_refunds(batch).items()}
    consumed: set[str] = set()
    claimed: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def recover(line: dict[str, str]) -> bool:
        if line["line_type"] != "REFUND":
            return False
        delta = -int(line["gross_effect_paise"])
        for event_id in index.get(delta, []):
            if event_id in consumed:
                continue
            # Commit to the first candidate.  This is the whole point: B2 has no
            # gate 9, so it cannot notice that a second candidate exists.  The
            # claim is recorded so a WRONG attribution is scored as wrong rather
            # than hidden behind a correct-looking outcome.
            consumed.add(event_id)
            claimed[line["settlement_id"]].append((line["settlement_id"], event_id))
            return True
        return False

    verdicts = []
    for case in batch.cases:
        verdict = b1_case(batch, case, recover)
        for sid in _split(case["settlement_ids"]):
            verdict.claims.extend(claimed.get(sid, []))
        verdicts.append(verdict)
    return verdicts


def score(batch: Batch, verdicts: list[Verdict]) -> ScoreResult:
    """Score B1 against the answer key and derive the difficulty floor D."""
    by_id = {v.case_id: v for v in verdicts}
    per_scenario: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    correct = 0
    false_attributions = 0
    for case in batch.cases:
        verdict = by_id[case["case_id"]]
        # Outcome AND attribution.  Scoring the outcome alone would give full
        # marks to a baseline that resolved a contested delta by picking the
        # wrong refund event, because the expected outcome is RECONCILED either
        # way.  That is precisely the false match this benchmark exists to
        # penalise, so a wrong claim fails the case.
        wrong_claims = [c for c in verdict.claims if c not in batch.allocations]
        false_attributions += len(wrong_claims)
        ok = verdict.outcome == case["expected_outcome"] and not wrong_claims
        correct += ok
        per_scenario[case["scenario"]][0] += ok
        per_scenario[case["scenario"]][1] += 1
    total = len(batch.cases)
    return {
        "total_cases": total,
        "b1_correct": correct,
        "b1_accuracy": correct / total if total else 0.0,
        "difficulty_floor_D": 1 - (correct / total) if total else 0.0,
        "false_attributions": false_attributions,
        "per_scenario": {k: (v[0], v[1]) for k, v in sorted(per_scenario.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the B1 baseline and report the benchmark difficulty floor."
    )
    ap.add_argument("data_dir", type=Path, nargs="*", default=[Path("data/dev")])
    ap.add_argument("--verbose", action="store_true", help="list the cases B1 gets wrong")
    args = ap.parse_args(argv)

    # The floor is reported per family, never averaged across them.  A single
    # blended D would let a hard family hide behind an easy one, which is the
    # exact form of dishonesty this whole number exists to prevent.
    for data_dir in args.data_dir:
        _report(data_dir, args.verbose)
    return 0


def _report(data_dir: Path, verbose: bool) -> None:
    batch = Batch.load(data_dir)
    verdicts = run_b1(batch)
    result = score(batch, verdicts)
    b2 = score(batch, run_b2(batch))
    by_id = {v.case_id: v for v in verdicts}

    print(f"\n{data_dir}  (published baselines)")
    print(f"  cases                {result['total_cases']:>6}")
    print(f"  B1 exact joins only  {result['b1_correct']:>6}  "
          f"({result['b1_accuracy']:.1%})   D = {result['difficulty_floor_D']:.1%}")
    print(f"  B2 + amount lookup   {b2['b1_correct']:>6}  "
          f"({b2['b1_accuracy']:.1%})   D = {b2['difficulty_floor_D']:.1%}"
          f"   <- quote against this one")
    per_scenario = result["per_scenario"]
    b2_scenario = b2["per_scenario"]
    print("\n  per scenario (correct / total):")
    print(f"    {'scenario':<26} {'B1':>7}  {'B2':>7}")
    for scenario, (ok, n) in per_scenario.items():
        b2_ok = b2_scenario[scenario][0]
        flag = "" if b2_ok == n else "   <- survives B2"
        print(f"    {scenario:<26} {ok:>3}/{n:<3}  {b2_ok:>3}/{n:<3}{flag}")

    if verbose:
        print("\n  cases B1 gets wrong:")
        for case in batch.cases:
            verdict = by_id[case["case_id"]]
            if verdict.outcome != case["expected_outcome"]:
                print(
                    f"    {case['case_id']}  {case['scenario']}: "
                    f"expected {case['expected_outcome']}, B1 said {verdict.outcome} "
                    f"({verdict.category})"
                )
                for reason in verdict.reasons[:2]:
                    print(f"        {reason}")


if __name__ == "__main__":
    raise SystemExit(main())
