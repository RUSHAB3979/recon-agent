"""Turn a completed run into an audit log.

``audit.py`` defines what a sealed decision record is. This module decides
which decisions a reconciliation run actually made, and writes them down. It is
a pure function of a finished ``RunResult``: same run in, same records out.

WHY THE LOG IS DERIVED, NOT EMITTED AS A SIDE EFFECT

    The obvious alternative is to hand every rung a logger and have it append
    as it goes. That was rejected. A rung that forgets to log is indetectable,
    two rungs can disagree about what a record should contain, and the contents
    of the log then depend on the order in which each pass happened to iterate
    its own inputs -- the same class of bug that consumption-in-the-runner
    exists to prevent. Deriving the log from the run's output makes "every
    decision is logged" a property of one function with one test, rather than a
    discipline six modules have to keep.

    The honest cost, stated before a panel finds it: a derived log can only
    record what the run retained. If a rung considered something and dropped it
    silently, no journal can recover it. That is why ``PassResult`` keeps
    abstentions with their candidate lists, why the runner keeps rejected
    claims with the reason each was refused, and why ``Verdict`` keeps its
    reasons -- nothing is discarded between the decision and the record.

WHICH STAGES EMIT, AND WHICH DO NOT

    S1  exact_join             an event a line names outright
    S2  refund_corroboration   an anonymous line proved through nine gates
    S4  adjudication           the evidence-reading rung, when it is enabled
    S5  disposition            the case-level outcome

    S0 emits nothing on purpose. Normalization makes no decisions; it builds a
    canonical representation. The one judgement embedded in it -- whether an
    event was ever going to settle -- is restated in the S5 record that acts on
    it, so logging it twice would inflate the record count without adding a
    fact. S3 emits nothing because the shipped ladder has no fuzzy-recovery
    rung; an empty stage is more honest than a padded one.

    A pass name with no stage raises rather than defaulting. A new rung whose
    decisions were silently filed under the wrong stage would corrupt every
    per-stage count in the summary, and that is worse than a failed run.

TIMESTAMPS MAKE THE HEAD HASH RUN-SPECIFIC, DELIBERATELY

    ``built_at`` is injectable so tests can pin it, but the default is the
    wall clock. Two runs over the same data therefore produce different head
    hashes. That is correct: a log that sealed to the same value whether it was
    written yesterday or today would be a checksum of the input, not a record
    of when a decision was taken.

WHAT IS OVERRIDABLE

    Attributions, abstentions and case dispositions are all overridable -- an
    operator who knows which refund is which must be able to say so. Runner
    rejections are not. Overriding "this event was already consumed by an
    earlier rung" would allocate one event to two lines, which is the single
    invariant the runner exists to hold, and an override layer that can break
    it is not a control.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from recon.match.audit import AuditLog, Decision
from recon.match.normalize import Batch
from recon.match.passes import Claim, PassResult

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from recon.match.controller import RunResult, Verdict

__all__ = [
    "DISPOSITION_STAGE",
    "STAGE_BY_PASS",
    "UnknownStageError",
    "build_journal",
    "stage_for_pass",
    "write_journal",
]


# The ladder's rung names, mapped to the stage vocabulary the audit log
# validates against. Keys are ``Pass.name`` values, not class names, because
# the name is what travels on a Claim.
STAGE_BY_PASS: Mapping[str, str] = {
    "exact_join": "S1",
    "refund_corroboration": "S2",
    "adjudication": "S4",
}

DISPOSITION_STAGE = "S5"

# Outcome vocabulary -> the four actions an audit record may carry. The
# controller's outcomes and the audit module's actions are separate
# vocabularies on purpose (one is the report, one is the ledger), so the
# translation is written down once here instead of being inferred at each site.
_ACTION_BY_OUTCOME: Mapping[str, str] = {
    "RECONCILED": "match",
    "EXCEPTION": "exception",
    "NO_ACTION": "no_action",
    "ABSTAIN": "abstain",
}


class UnknownStageError(KeyError):
    """A pass produced decisions but was never assigned an audit stage."""


def stage_for_pass(pass_name: str) -> str:
    """Map a rung name to its audit stage, refusing to guess."""
    try:
        return STAGE_BY_PASS[pass_name]
    except KeyError:
        raise UnknownStageError(
            f"pass {pass_name!r} has no audit stage; add it to STAGE_BY_PASS "
            "rather than letting its decisions be filed under the wrong one"
        ) from None


def _reasoning(reasons: tuple[str, ...], fallback: str) -> str:
    """Join a reason list into non-empty prose.

    ``Decision`` rejects empty reasoning, and that rejection is the point: a
    decision that cannot say why it happened is not auditable. The fallback
    names the rule that fired rather than inventing a justification.
    """
    joined = "; ".join(reason for reason in reasons if reason)
    return joined if joined else fallback


def _claim_inputs(batch: Batch, claim: Claim, lines: Mapping[str, object]) -> dict[str, object]:
    """The evidence a reader would need to re-check this attribution by hand."""
    inputs: dict[str, object] = {
        "settlement_id": claim.settlement_id,
        "event_id": claim.event_id,
        "detail_id": claim.detail_id,
    }
    event = batch.events.get(claim.event_id)
    if event is not None:
        inputs["event_amount_paise"] = event.amount_paise
        inputs["event_currency"] = event.currency
        inputs["event_type"] = event.event_type
        inputs["event_txn_id"] = event.txn_id
    line = lines.get(claim.detail_id) if claim.detail_id is not None else None
    if line is not None:
        inputs["line_net_effect_paise"] = line.net_effect_paise  # type: ignore[attr-defined]
        inputs["line_type"] = line.line_type  # type: ignore[attr-defined]
        inputs["line_settled_at"] = line.settled_at.isoformat()  # type: ignore[attr-defined]
    return inputs


def _claim_decision(
    batch: Batch,
    claim: Claim,
    lines: Mapping[str, object],
    timestamp: datetime,
) -> Decision:
    return Decision(
        stage=stage_for_pass(claim.pass_name),
        subject=(claim.settlement_id, claim.detail_id or "-", claim.event_id),
        action="match",
        inputs=_claim_inputs(batch, claim, lines),
        rule=f"{claim.pass_name}/{claim.tier.value}",
        result={"settlement_id": claim.settlement_id, "event_id": claim.event_id},
        confidence=float(claim.confidence),
        reasoning=_reasoning(claim.reasons, f"attributed by {claim.pass_name}"),
        timestamp=timestamp,
        overridable=True,
    )


def _abstention_decisions(
    result: PassResult, timestamp: datetime
) -> list[Decision]:
    stage = stage_for_pass(result.pass_name)
    return [
        Decision(
            stage=stage,
            subject=(abstention.settlement_id, abstention.detail_id),
            action="abstain",
            inputs={
                "settlement_id": abstention.settlement_id,
                "detail_id": abstention.detail_id,
                "candidate_event_ids": list(abstention.candidate_event_ids),
                "candidate_count": len(abstention.candidate_event_ids),
            },
            rule=f"{result.pass_name}/abstain",
            # Recording the surviving candidates as the result is what makes the
            # abstention actionable: an operator opening this record is shown the
            # shortlist the engine could not separate, not merely that it failed.
            result={"candidates": list(abstention.candidate_event_ids)},
            confidence=0.0,
            reasoning=_reasoning((abstention.reason,), "candidates not separable"),
            timestamp=timestamp,
            overridable=True,
        )
        for abstention in result.abstentions
    ]


def _rejection_decision(claim: Claim, reason: str, timestamp: datetime) -> Decision:
    return Decision(
        stage=stage_for_pass(claim.pass_name),
        subject=(claim.settlement_id, claim.detail_id or "-", claim.event_id),
        action="no_action",
        inputs={
            "settlement_id": claim.settlement_id,
            "event_id": claim.event_id,
            "detail_id": claim.detail_id,
            "proposed_by": claim.pass_name,
        },
        rule="runner/consumption",
        result=None,
        confidence=None,
        reasoning=f"claim refused by the runner: {reason}",
        timestamp=timestamp,
        # Not overridable. See the module docstring: an override that reinstated
        # this claim would allocate one event twice.
        overridable=False,
    )


def _verdict_decision(verdict: Verdict, timestamp: datetime) -> Decision:
    action = _ACTION_BY_OUTCOME[verdict.outcome]
    allocations = sorted(
        [event_id, settlement_id] for event_id, settlement_id in verdict.allocations
    )
    return Decision(
        stage=DISPOSITION_STAGE,
        subject=verdict.case_id,
        action=action,
        inputs={
            "case_id": verdict.case_id,
            "allocation_count": len(verdict.allocations),
            "allocations": allocations,
        },
        rule="controller/disposition",
        result={"outcome": verdict.outcome, "category": verdict.category},
        confidence=float(verdict.confidence),
        reasoning=_reasoning(verdict.reasons, f"disposition {verdict.outcome}"),
        timestamp=timestamp,
        overridable=True,
    )


def build_journal(result: RunResult, *, built_at: datetime | None = None) -> AuditLog:
    """Every decision one run made, sealed into a hash-chained log.

    Records are emitted in a fixed order -- each rung's attributions then its
    abstentions, in ladder order, then the claims the runner refused, then the
    case dispositions. The order is part of what the chain seals, so it is
    stated here rather than left to whatever order a dict happened to yield.
    """
    timestamp = built_at if built_at is not None else datetime.now(timezone.utc)
    lines: dict[str, object] = {line.detail_id: line for line in result.batch.details}

    log = AuditLog()
    for pass_result in result.ladder.per_pass:
        for claim in pass_result.claims:
            log.append(_claim_decision(result.batch, claim, lines, timestamp))
        for decision in _abstention_decisions(pass_result, timestamp):
            log.append(decision)
    for claim, reason in result.ladder.rejected:
        log.append(_rejection_decision(claim, reason, timestamp))
    for verdict in result.verdicts:
        log.append(_verdict_decision(verdict, timestamp))
    return log


def write_journal(
    result: RunResult, path: str | Path, *, built_at: datetime | None = None
) -> AuditLog:
    """Build the journal and write it as JSONL, creating parent directories."""
    log = build_journal(result, built_at=built_at)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    log.write(destination)
    return log
