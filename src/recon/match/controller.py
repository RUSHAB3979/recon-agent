"""The agent: run the ladder, apply the controls, decide each case.

This is the only module that knows the whole shape of a run, and it is
deliberately thin. Everything it does is composition:

    normalize  ->  ladder  ->  controls  ->  disposition  ->  AgentOutput

CONSUMPTION IS DECIDED HERE, NOT INSIDE A PASS

    Passes return claims; this module decides which claims survive and records
    which events are then unavailable. That inversion is the single most
    important structural decision in the ladder. If a pass consumed events
    itself, two passes could each believe they owned the same refund event, and
    the result would depend on the order in which each happened to iterate its
    own inputs -- a bug that reproduces only on some seeds and looks like a data
    problem rather than a code problem.

    The rule applied is first-claim-wins in ladder order. Earlier rungs are
    more precise by construction, so a later rung arriving at an event an
    earlier rung already proved is not new information.

ABSTENTION IS AN OUTCOME, NOT A FAILURE

    A case whose evidence does not separate its candidates is reported as
    ABSTAIN with the candidates listed. This is the behaviour the whole design
    exists to make safe: the published floor cannot abstain, so where it guesses
    and gets a contested refund wrong, it books a false attribution that the
    scorer counts by name. The agent trades that for a measured non-answer.

    Concretely: outcome accuracy alone would reward guessing, since a contested
    refund resolved to the wrong event still produces the expected RECONCILED.
    That is why the scorer checks allocations too, and why abstaining is the
    correct move rather than a cowardly one.

WHAT THIS MODULE MUST NEVER READ

    ``answer_key_cases.csv`` is opened only through ``caseload.load_caseload``,
    which cannot return the answer columns. No other answer-key file is opened
    at all. The scorer loads those, after the agent has finished and its output
    is frozen.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from recon.match.adjudicator import (
    DEFAULT_MIN_CONFIDENCE,
    AdjudicationPass,
    default_reader,
)
from recon.match.caseload import CaseUnit, load_caseload
from recon.match.controls import DUPLICATE_WARNING, Finding, settlement_findings
from recon.match.journal import write_journal
from recon.match.normalize import Batch, DetailLine, load_batch
from recon.match.passes import DEFAULT_LADDER, Claim, Pass, PassResult
from recon.metrics.score import AgentOutput, CaseDecision

__all__ = [
    "LadderRun",
    "RunResult",
    "reconcile",
    "run_ladder",
]


# Outcome vocabulary. These four strings are the contract with the scorer and
# with the answer key; they are named constants so a typo fails at import
# rather than silently scoring every case wrong.
RECONCILED = "RECONCILED"
EXCEPTION = "EXCEPTION"
NO_ACTION = "NO_ACTION"
ABSTAIN = "ABSTAIN"

AMBIGUOUS_REFUND = "AMBIGUOUS_REFUND"
CAPTURED_UNSETTLED = "CAPTURED_UNSETTLED"


@dataclass
class LadderRun:
    """Everything the ladder produced across the whole batch.

    Per-pass results are kept rather than merged so the report can print a
    yield table -- how many lines each rung examined, claimed and declined. A
    rung that resolves nothing is a rung to delete, and that is only visible if
    the numbers survive to the report.
    """

    per_pass: tuple[PassResult, ...]
    accepted: tuple[Claim, ...]
    rejected: tuple[tuple[Claim, str], ...]

    @property
    def attributed_detail_ids(self) -> frozenset[str]:
        return frozenset(
            claim.detail_id for claim in self.accepted if claim.detail_id is not None
        )

    @property
    def abstained_detail_ids(self) -> frozenset[str]:
        return frozenset(
            abstention.detail_id
            for result in self.per_pass
            for abstention in result.abstentions
        )

    def claims_by_settlement(self) -> Mapping[str, tuple[Claim, ...]]:
        grouped: dict[str, list[Claim]] = {}
        for claim in self.accepted:
            grouped.setdefault(claim.settlement_id, []).append(claim)
        return {key: tuple(value) for key, value in grouped.items()}

    def abstained_lines_by_settlement(self) -> Mapping[str, tuple[str, ...]]:
        """Detail ids the ladder declined, keyed by settlement.

        Separate from ``abstentions_by_settlement`` because that one returns
        prose for a human and this one returns keys for arithmetic. Parsing the
        ids back out of the prose would work until somebody improves the
        wording.
        """
        grouped: dict[str, list[str]] = {}
        for result in self.per_pass:
            for abstention in result.abstentions:
                grouped.setdefault(abstention.settlement_id, []).append(
                    abstention.detail_id
                )
        return {key: tuple(value) for key, value in grouped.items()}

    def abstentions_by_settlement(self) -> Mapping[str, tuple[str, ...]]:
        """Human-readable abstention reasons, keyed by settlement."""
        grouped: dict[str, list[str]] = {}
        for result in self.per_pass:
            for abstention in result.abstentions:
                grouped.setdefault(abstention.settlement_id, []).append(
                    f"{abstention.detail_id}: {abstention.reason}"
                )
        return {key: tuple(value) for key, value in grouped.items()}


def run_ladder(batch: Batch, ladder: Sequence[Pass] = DEFAULT_LADDER) -> LadderRun:
    """Run every rung in order, enforcing consumption between them."""
    consumed: set[str] = set()
    claimed_lines: set[str] = set()
    accepted: list[Claim] = []
    rejected: list[tuple[Claim, str]] = []
    per_pass: list[PassResult] = []

    for rung in ladder:
        # A rung that declares ``run_residual`` is asking to see what the rungs
        # before it could not decide. It is handed those abstentions and nothing
        # else, which is what makes "the model never sees a resolved case" a
        # property of the runner rather than a promise made by the rung.
        residual = getattr(rung, "run_residual", None)
        if residual is None:
            result = rung.run(batch, frozenset(consumed))
        else:
            outstanding = tuple(
                abstention
                for earlier in per_pass
                for abstention in earlier.abstentions
                if abstention.detail_id not in claimed_lines
            )
            result = residual(batch, frozenset(consumed), outstanding)
        surviving: list[Claim] = []
        for claim in result.claims:
            if claim.event_id in consumed:
                # Not an error in the pass; the runner is the authority on what
                # is still available, and it says no.
                rejected.append((claim, f"event {claim.event_id} already consumed"))
                continue
            if claim.detail_id is not None and claim.detail_id in claimed_lines:
                rejected.append(
                    (claim, f"detail line {claim.detail_id} already attributed")
                )
                continue
            consumed.add(claim.event_id)
            if claim.detail_id is not None:
                claimed_lines.add(claim.detail_id)
            surviving.append(claim)
        accepted.extend(surviving)
        # The stored result reports what the pass ACTUALLY contributed, so the
        # yield table cannot be inflated by claims the runner threw away.
        per_pass.append(
            PassResult(
                pass_name=result.pass_name,
                claims=surviving,
                abstentions=result.abstentions,
                examined=result.examined,
                # Diagnostics carry through unchanged. They describe what the
                # pass considered, not what it was allowed to keep, so the
                # runner has no business editing them.
                counters=result.counters,
            )
        )

    # An abstention a later rung settled is no longer an abstention. Pruning
    # here rather than in the rungs keeps the same invariant as consumption:
    # the runner owns what survives, so no rung can leave a stale non-answer
    # attached to a line that was later resolved -- or report one line twice,
    # once by the rung that gave up and once by the rung that took it on.
    # Walking backwards makes the LAST word on a line the one that is kept.
    spoken_for = set(claimed_lines)
    pruned: list[PassResult] = []
    for result in reversed(per_pass):
        kept = [
            abstention
            for abstention in result.abstentions
            if abstention.detail_id not in spoken_for
        ]
        spoken_for.update(abstention.detail_id for abstention in kept)
        pruned.append(
            PassResult(
                pass_name=result.pass_name,
                claims=result.claims,
                abstentions=kept,
                examined=result.examined,
                counters=result.counters,
            )
        )
    per_pass = list(reversed(pruned))

    return LadderRun(
        per_pass=tuple(per_pass), accepted=tuple(accepted), rejected=tuple(rejected)
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    """One case decision with the evidence behind it."""

    case_id: str
    outcome: str
    category: str | None
    reasons: tuple[str, ...]
    allocations: frozenset[tuple[str, str]]
    confidence: float
    # Money an operator has to chase for this case, in paise. Carried on the
    # decision rather than recomputed by the report, so the exception list and
    # the audit record cannot disagree about what a case is worth.
    exposure_paise: int = 0


def _disposition_without_settlement(
    batch: Batch, case: CaseUnit
) -> tuple[str, str | None, list[str], int]:
    """Decide a case whose events appear in no settlement at all.

    The NOT_SETTLEABLE trap lives here. A CREATED or FAILED payment that never
    settled is correct behaviour, and reporting it as an exception is a false
    positive that pads the exception list -- the harness measures that rate by
    name, because a list that flags everything is not an exception list.
    """
    events = [batch.events[event_id] for event_id in case.event_ids if event_id in batch.events]
    if events and not any(event.is_settleable for event in events):
        statuses = sorted({event.status for event in events})
        return (
            NO_ACTION,
            None,
            [f"all {len(events)} events are terminal before settlement ({', '.join(statuses)})"],
            # Zero exposure, and that is the whole point of the trap class:
            # nothing is owed, nothing is missing, and an operator queue that
            # ranked these above a real break would be actively harmful.
            0,
        )
    settleable = [event for event in events if event.is_settleable]
    unsettled = sorted({event.status for event in settleable})
    return (
        EXCEPTION,
        CAPTURED_UNSETTLED,
        [f"{len(events)} events with status {unsettled} appear in no settlement"],
        sum(abs(event.amount_paise) for event in settleable),
    )


@dataclass(frozen=True)
class LadderIndex:
    """The whole-run views ``_verdict`` needs, built once for the whole run.

    Each of these is a fold over every claim or abstention in the batch, and
    ``_verdict`` used to call all four itself -- once per case. That is O(cases x
    claims), and it does not look like a bottleneck at 100 cases: at 500 records
    the pipeline runs in 22ms and the published throughput figure came from
    exactly there. Profiled at 8,000 records, ``claims_by_settlement`` and
    ``attributed_detail_ids`` were more than half the runtime, and end to end
    the collapse was 48k records/sec at 500 down to 1.3k at 20,000.

    Passing the index in rather than caching it on ``LadderRun`` is deliberate:
    a cache makes the repeated call cheap, while this makes it impossible. The
    cost is visible at the one call site that pays it.
    """

    attributed: frozenset[str]
    claims_by_settlement: Mapping[str, tuple[Claim, ...]]
    abstentions_by_settlement: Mapping[str, tuple[str, ...]]
    abstained_lines_by_settlement: Mapping[str, tuple[str, ...]]

    @classmethod
    def build(cls, ladder: LadderRun) -> "LadderIndex":
        return cls(
            attributed=ladder.attributed_detail_ids,
            claims_by_settlement=ladder.claims_by_settlement(),
            abstentions_by_settlement=ladder.abstentions_by_settlement(),
            abstained_lines_by_settlement=ladder.abstained_lines_by_settlement(),
        )


def _verdict(
    batch: Batch,
    case: CaseUnit,
    index: LadderIndex,
    lines: Mapping[str, DetailLine],
) -> Verdict:
    attributed = index.attributed
    claims_by_settlement = index.claims_by_settlement
    abstentions_by_settlement = index.abstentions_by_settlement
    abstained_lines_by_settlement = index.abstained_lines_by_settlement

    allocations: set[tuple[str, str]] = set()
    claim_confidences: list[float] = []
    for settlement_id in case.settlement_ids:
        for claim in claims_by_settlement.get(settlement_id, ()):
            allocations.add((claim.event_id, settlement_id))
            claim_confidences.append(claim.confidence)

    # A case is only as certain as the least certain claim holding it up.
    #
    # This used to be a hardcoded 1.0 on every resolved path, and that was a
    # real defect rather than a rounding of one: the adjudicator books SUGGESTED
    # claims at whatever the model said, and flattening them to 1.0 published a
    # line a model guessed at as though nine gates had proved it. With a reader
    # answering at 0.72, fourteen dev claims arrived below certainty and every
    # case resting on them was still published at 1.00, the middle
    # tier was invisible in every metric that quotes confidence, and the
    # precision/coverage curve could not move -- the abstention dial was inert
    # by construction while looking like a measurement.
    #
    # Minimum, not mean: a case resting on one proved leg and one guessed leg is
    # a guess, and averaging would let the proved leg launder the other. A case
    # resting on no claims at all is a disposition the engine reached by itself,
    # so it stays at 1.0 -- which is why every deterministic figure this project
    # publishes is byte-identical before and after this change.
    confidence = min(claim_confidences, default=1.0)

    if not case.has_settlements:
        outcome, category, reasons, exposure = _disposition_without_settlement(
            batch, case
        )
        return Verdict(
            case_id=case.case_id,
            outcome=outcome,
            category=category,
            reasons=tuple(reasons),
            allocations=frozenset(allocations),
            confidence=1.0,
            exposure_paise=exposure,
        )

    findings: list[Finding] = []
    for settlement_id in case.settlement_ids:
        findings.extend(settlement_findings(batch, settlement_id, attributed))

    abstained = [
        reason
        for settlement_id in case.settlement_ids
        for reason in abstentions_by_settlement.get(settlement_id, ())
    ]
    reasons = tuple(finding.detail for finding in findings) + tuple(abstained)

    if abstained:
        # Abstention outranks every other disposition. A case where the engine
        # cannot tell which event explains a line is not reconciled, and calling
        # it an exception would misdescribe it: the books may well tie out, and
        # what is missing is an attribution, not money.
        return Verdict(
            case_id=case.case_id,
            outcome=ABSTAIN,
            category=AMBIGUOUS_REFUND,
            reasons=reasons,
            allocations=frozenset(allocations),
            confidence=0.0,
            # The exposure of an abstention is the money sitting on the lines
            # nobody has attributed. It is unallocated, not lost, which is why
            # this is a different queue from a control break of the same size.
            exposure_paise=sum(
                abs(lines[detail_id].net_effect_paise)
                for settlement_id in case.settlement_ids
                for detail_id in abstained_lines_by_settlement.get(settlement_id, ())
                if detail_id in lines
            ),
        )

    hard = [finding for finding in findings if finding.is_hard]
    if hard:
        # First hard finding names the category. Ranking them would be a policy
        # this engine has not earned yet, and an arbitrary ranking dressed up as
        # a priority order is worse than a stated first-wins rule.
        return Verdict(
            case_id=case.case_id,
            outcome=EXCEPTION,
            category=hard[0].category,
            reasons=reasons,
            allocations=frozenset(allocations),
            confidence=confidence,
            # Every hard finding, not only the one that named the category. A
            # case with three breaks is worth all three to whoever works it.
            exposure_paise=sum(finding.exposure_paise for finding in hard),
        )

    return Verdict(
        case_id=case.case_id,
        outcome=RECONCILED,
        category=DUPLICATE_WARNING if findings else None,
        reasons=reasons,
        allocations=frozenset(allocations),
        confidence=confidence,
        exposure_paise=0,
    )


@dataclass
class RunResult:
    """One complete agent run over one dataset directory."""

    verdicts: tuple[Verdict, ...]
    ladder: LadderRun
    batch: Batch
    # The case partition this run decided. Retained so a consumer -- the
    # exception list, the journal -- can name the settlements, events and bank
    # rows behind a verdict without re-reading the caseload from disk and
    # risking a different file.
    cases: tuple[CaseUnit, ...]
    elapsed_seconds: float
    record_count: int
    per_pass_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def throughput(self) -> float:
        return self.record_count / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def to_agent_output(self) -> AgentOutput:
        """Translate into the vocabulary the shared scorer reads.

        The allocation tuple is ``(event_id, settlement_id)`` on the answer key
        side. A transposition here type-checks cleanly and then scores every
        claim as a false positive and every truth as a false negative, halving
        two published numbers with nothing to show for it, so the orientation is
        fixed in one place: ``_verdict`` builds the pair already flipped.
        """
        return AgentOutput(
            CaseDecision(
                case_id=verdict.case_id,
                outcome=verdict.outcome,
                category=verdict.category,
                allocations=frozenset(verdict.allocations),
                confidence=verdict.confidence,
                reasons=verdict.reasons,
            )
            for verdict in self.verdicts
        )


def reconcile(
    directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER
) -> RunResult:
    """Reconcile one released dataset directory end to end."""
    directory = Path(directory)
    started = time.perf_counter()
    batch = load_batch(directory)
    cases = load_caseload(directory)
    ladder_run = run_ladder(batch, ladder)
    # Built once, not per case: a lookup rebuilt inside the loop would turn the
    # disposition step quadratic for no gain.
    lines = {line.detail_id: line for line in batch.details}
    index = LadderIndex.build(ladder_run)
    verdicts = tuple(_verdict(batch, case, index, lines) for case in cases)
    elapsed = time.perf_counter() - started
    return RunResult(
        verdicts=verdicts,
        ladder=ladder_run,
        batch=batch,
        cases=cases,
        elapsed_seconds=elapsed,
        record_count=len(batch.events) + len(batch.details) + len(batch.settlements),
        per_pass_names=tuple(rung.name for rung in ladder),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the reconciliation agent over one or more released datasets."
    )
    parser.add_argument("data_dir", type=Path, nargs="*", default=[Path("data/dev")])
    parser.add_argument(
        "--adjudicate",
        action="store_true",
        help=(
            "append the evidence-reading rung, which acts on the residual the "
            "gates could not separate. Off by default: the published numbers "
            "are the deterministic ones and must not depend on a network call."
        ),
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=None,
        help=(
            "write each dataset's decision journal to DIR/<dataset>/audit.jsonl. "
            "Kept outside data/ on purpose: a dataset directory is an input and "
            "stays read-only, and mixing a run's output into it would make the "
            "byte-for-byte regeneration check meaningless."
        ),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help="below this, a stated preference is recorded as a decline",
    )
    args = parser.parse_args(argv)

    for directory in args.data_dir:
        # A fresh rung per dataset, so the token and cost figures printed under
        # each batch are that batch's own and not a running total.
        rung = (
            AdjudicationPass(default_reader(), min_confidence=args.min_confidence)
            if args.adjudicate
            else None
        )
        ladder: Sequence[Pass] = DEFAULT_LADDER if rung is None else (*DEFAULT_LADDER, rung)
        result = reconcile(directory, ladder)
        counts: dict[str, int] = {}
        for verdict in result.verdicts:
            counts[verdict.outcome] = counts.get(verdict.outcome, 0) + 1
        print(f"\n{directory}")
        print(f"  cases            {len(result.verdicts):>6}")
        for outcome in (RECONCILED, EXCEPTION, NO_ACTION, ABSTAIN):
            print(f"    {outcome:<14} {counts.get(outcome, 0):>6}")
        print(f"  allocations      {sum(len(v.allocations) for v in result.verdicts):>6}")
        print(f"  throughput       {result.throughput:>9.0f} records/sec")
        print("\n  per-pass yield (examined / claimed / abstained):")
        for pass_result in result.ladder.per_pass:
            print(
                f"    {pass_result.pass_name:<20} {pass_result.examined:>5} / "
                f"{len(pass_result.claims):>5} / {len(pass_result.abstentions):>5}"
            )
        if rung is not None:
            # Cost per batch is a reported metric, so it is printed by the
            # command that incurs it rather than reconstructed afterwards.
            print(
                f"\n  adjudicator      {rung.usage.calls} calls, "
                f"{rung.usage.input_tokens} in / {rung.usage.output_tokens} out "
                f"({rung.usage.cache_read_tokens} cached), "
                f"${rung.cost_usd()} on {rung.reader.model}"
            )
        if args.audit_dir is not None:
            # Derived from the finished run rather than appended as each rung
            # goes: see recon.match.journal for why, and for what that costs.
            destination = args.audit_dir / directory.name / "audit.jsonl"
            log = write_journal(result, destination)
            print(f"  audit            {len(log)} decisions -> {destination}")
            print(f"                   head {log.head_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
