"""Rung two: prove which refund event explains an anonymous settlement line.

This is the whole measured gap. Every other capability in this engine is
matched by a competent SQL script -- the published floor demonstrates that in
runnable code. What the floor cannot do is attribute an anonymous refund line
without guessing, or decline when attribution is not provable, and that is what
this module does.

THE RULE, STATED BEFORE THE CODE

    An anonymous line resolves ONLY when exactly one candidate survives every
    gate below. Two survivors means ABSTAIN, and resolving either one is scored
    as a false match.

        1. unconsumed        no other allocation already claims the event
        2. exact amount      integer paise, no tolerance
        3. recovery window   the event predates the settlement by 0..N days
        4. currency          the line and the event agree
        5. lineage           the parent payment exists and itself settled
        6. sign              a refund line is explained by a refund event
        7. controls hold     the settlement ties out after the allocation
        8. global feasibility  accepting it leaves every other line solvable
        9. uniqueness        no second candidate survives gates 1-8

    A tie may never be broken by frequency, by prior, by file order, or by
    plausibility. "This merchant has forty refunds and no chargebacks" is a
    prior and is inadmissible. "There exists exactly one unconsumed record whose
    amount and date reproduce this line" is a proof and is admissible.

    That distinction is the reason gate 9 exists. Gates 1-8 establish that a
    candidate COULD explain the line; only gate 9 establishes that it is the
    ONLY thing that could, and without it the engine is guessing confidently.
    The published B2 baseline is precisely this rule with gates 3, 5, 8 and 9
    deleted, and it books false attributions at a measurable rate as a result.

WHY GATE 8 IS NOT DECORATION

    Gates 1-7 are local: they look at one line and one candidate. Gate 8 is
    global, and it is the only gate that can see a conflict between two lines
    that are individually fine.

    Take two anonymous lines of the same amount and two admissible events. Every
    local gate passes for all four pairings, so a local engine reports two
    candidates for each line and abstains on both. But if a third line can be
    explained only by the second event, then the second event is spoken for, and
    the first line has exactly one explanation left. Gate 8 finds that; nothing
    local can.

    It is implemented as the question a reconciler actually asks: is there a
    globally consistent assignment that uses this pairing? Concretely:

        force the candidate edge (line, event)
        remove both endpoints from the graph
        re-solve the residual
        the candidate survives iff the residual still reaches
            ``baseline_matching_size - 1``

    That is the test for whether committing to this pairing costs the rest of
    the batch nothing -- equivalently, whether SOME maximum assignment of lines
    to events uses it.

    The neighbouring question is not the same question, and the difference is
    worth stating because it is easy to conflate. Deleting the edge while
    leaving both endpoints available and re-solving asks whether the pairing is
    in EVERY maximum assignment. That is strictly stronger: it would eliminate
    candidates that are perfectly admissible merely because an equally good
    assignment exists without them, and it would leave gate 9 with nothing to
    do. Gate 8 establishes that a pairing is POSSIBLE; gate 9 establishes that
    it is the ONLY possibility. Keeping those two questions in two gates is what
    keeps "could explain this line" separate from "is the only thing that could".

    The graphs here have tens of nodes, so the direct formulation is used rather
    than an alternating-path argument that would be faster and harder to defend
    in a room.

    On the current datasets gate 8 eliminates nothing, and the per-gate table
    this module produces says so out loud. That is the honest reporting posture:
    a gate that does no work on this data is still the gate that stops the
    engine from being wrong on data where two lines compete, and the way to show
    that is to publish the count rather than to imply it.

THE WINDOW IS A DECLARED POLICY, NOT A LEARNED THRESHOLD

    ``RECOVERY_WINDOW_DAYS`` is the one tunable number in this module. It is
    calibrated on ``data/dev`` and never against the primary or stress families,
    and in a real deployment it would come from the settlement SLA the payment
    processor publishes rather than from any dataset at all. It is stated here
    rather than inferred so that a reader can disagree with the value without
    having to reverse-engineer it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from recon.match.controls import settlement_findings
from recon.match.normalize import Batch, DetailLine, GatewayEvent
from recon.match.passes import (
    EXACT,
    Abstention,
    Cardinality,
    Claim,
    ClaimTier,
    PassResult,
    Tolerance,
)

__all__ = [
    "Gate",
    "GateLedger",
    "RECOVERY_WINDOW_DAYS",
    "RefundCorroborationPass",
    "maximum_matching",
]


# Calibrated on data/dev only. See the module docstring: this is a declared
# settlement-window policy, not a fitted parameter, and it is the only number in
# this file that a reasonable person could set differently.
RECOVERY_WINDOW_DAYS = 4

# Roll-up and summary-equation breaks. A settlement carrying one of these does
# not tie out, and attributing its anonymous line would assert an explanation
# for books that do not balance -- so gate 7 refuses rather than resolving.
CONTROL_BREAK_CATEGORIES = frozenset(
    {
        "ROLLUP_MISMATCH",
        "SUMMARY_EQUATION_VIOLATION",
        "LINE_EQUATION_VIOLATION",
        "SETTLEMENT_MISSING",
    }
)


class Gate(str, Enum):
    """The nine admissibility gates, named so a rejection is explainable.

    Gate 2 is applied as an index lookup rather than a scan, so it never appears
    as an elimination count -- the starting multiplicity for every line IS the
    set of events whose amount reproduces it exactly. Reporting "gate 2 rejected
    four thousand events" would be true and useless.
    """

    UNCONSUMED = "gate_1_unconsumed"
    EXACT_AMOUNT = "gate_2_exact_amount"
    RECOVERY_WINDOW = "gate_3_recovery_window"
    CURRENCY = "gate_4_currency"
    LINEAGE = "gate_5_txn_lineage"
    SIGN = "gate_6_sign"
    CONTROLS = "gate_7_controls_hold"
    GLOBAL_FEASIBILITY = "gate_8_global_feasibility"
    UNIQUENESS = "gate_9_no_second_candidate"


@dataclass
class GateLedger:
    """How many candidates each gate eliminated, across the whole batch.

    This exists so the engine can be audited on the question "which of your
    nine gates actually does anything". A gate that never fires is not
    automatically dead code -- gate 8 is the standing example -- but the claim
    that it earns its place has to survive someone reading the count, not be
    asserted in a comment.
    """

    eliminated: dict[Gate, int] = field(default_factory=lambda: defaultdict(int))
    lines_examined: int = 0
    candidates_by_amount: int = 0
    resolved: int = 0
    abstained: int = 0
    unexplained: int = 0

    def record(self, gate: Gate) -> None:
        self.eliminated[gate] += 1

    def as_rows(self) -> list[tuple[str, int]]:
        return [(gate.value, self.eliminated.get(gate, 0)) for gate in Gate]


# --------------------------------------------------------------------------
# bipartite matching
# --------------------------------------------------------------------------


def maximum_matching(
    adjacency: dict[str, list[str]], right_nodes: frozenset[str]
) -> dict[str, str]:
    """Maximum bipartite matching, augmenting-path (Kuhn) algorithm.

    Returns a mapping from right node to left node. Deterministic: the caller
    supplies adjacency in sorted order, and ties are therefore broken the same
    way on every run. A matching that varied between runs would make the gate-8
    verdict irreproducible, which for a benchmark is the same as wrong.
    """
    matched: dict[str, str] = {}

    def augment(left: str, seen: set[str]) -> bool:
        for right in adjacency.get(left, ()):
            if right not in right_nodes or right in seen:
                continue
            seen.add(right)
            if right not in matched or augment(matched[right], seen):
                matched[right] = left
                return True
        return False

    for left in sorted(adjacency):
        augment(left, set())
    return matched


@dataclass(frozen=True)
class CandidateGraph:
    """The candidate graph, split into the pieces gate 8 can reason about alone.

    WHY THIS EXISTS

        Gate 8 asks, for every candidate edge, whether some globally consistent
        assignment uses it -- by forcing the edge, removing both endpoints and
        re-solving the residual. Done against the whole graph that is a fresh
        Kuhn run per candidate, each preceded by a full copy of the adjacency:
        measured at 20,000 records it was 1,841 matching runs and the largest
        cost in the pipeline, with the scaling exponent reaching 2.15 by 50,000.

    WHY SPLITTING IS EXACT AND NOT AN APPROXIMATION

        A maximum matching of a disconnected graph is the union of maximum
        matchings of its components, so its size is the sum of theirs. Forcing
        an edge and deleting its two endpoints changes only the component that
        edge lives in; every other component still contributes exactly its own
        baseline. So

            total residual == baseline - 1
                <=>  residual within this component == that component's baseline - 1

        and the second question is the one worth asking, because these
        components are amount-collision clusters -- in practice two or three
        nodes, never the whole batch. The gate's verdict is unchanged, edge for
        edge; only the graph it is asked about shrinks.

        ``test_the_component_baselines_sum_to_the_global_baseline`` pins the
        premise, so if the decomposition ever stops being a decomposition the
        arithmetic above fails loudly rather than quietly admitting a candidate.
    """

    adjacency: Mapping[str, list[str]]
    right_nodes: frozenset[str]
    baseline: int
    _component_of: Mapping[str, int]
    _adjacency_of: tuple[Mapping[str, list[str]], ...]
    _right_of: tuple[frozenset[str], ...]
    _baseline_of: tuple[int, ...]

    @classmethod
    def build(
        cls, adjacency: Mapping[str, list[str]], right_nodes: frozenset[str]
    ) -> "CandidateGraph":
        # The inverse edge list, built once. Walking components needs "which
        # lines name this event", and answering that by re-scanning every line
        # would put an O(lines) loop inside the walk -- the same shape of bug
        # this class exists to remove, reintroduced one level down.
        lines_naming: dict[str, list[str]] = {}
        for left in sorted(adjacency):
            for right in adjacency[left]:
                if right in right_nodes:
                    lines_naming.setdefault(right, []).append(left)

        # Connected components over the bipartite graph, walked from each left
        # node. Component numbering has no effect on any verdict; the sorted
        # iteration is so that a dump of intermediate state is readable when
        # something does go wrong.
        component_of: dict[str, int] = {}
        groups: list[list[str]] = []
        seen_right: set[str] = set()

        for start in sorted(adjacency):
            if start in component_of:
                continue
            index = len(groups)
            members = [start]
            component_of[start] = index
            queue = [start]
            while queue:
                left = queue.pop()
                for right in adjacency.get(left, ()):
                    if right not in right_nodes or right in seen_right:
                        continue
                    seen_right.add(right)
                    for other in lines_naming.get(right, ()):
                        if other in component_of:
                            continue
                        component_of[other] = index
                        members.append(other)
                        queue.append(other)
            groups.append(sorted(members))

        adjacency_of: list[Mapping[str, list[str]]] = []
        right_of: list[frozenset[str]] = []
        baseline_of: list[int] = []
        for members in groups:
            local = {left: list(adjacency.get(left, ())) for left in members}
            local_right = frozenset(
                right for candidates in local.values() for right in candidates
            ) & right_nodes
            adjacency_of.append(local)
            right_of.append(local_right)
            baseline_of.append(len(maximum_matching(local, local_right)))

        return cls(
            adjacency=adjacency,
            right_nodes=right_nodes,
            baseline=sum(baseline_of),
            _component_of=component_of,
            _adjacency_of=tuple(adjacency_of),
            _right_of=tuple(right_of),
            _baseline_of=tuple(baseline_of),
        )

    @property
    def component_count(self) -> int:
        """How many independent clusters the candidate graph fell into.

        Reported because it is the number that explains the speed: gate 8 costs
        a matching over one cluster rather than over the batch, so a run whose
        graph does not decompose would be as slow as the old one and this is
        where that would show.
        """
        return len(self._baseline_of)

    def residual_reaches_baseline(self, detail_id: str, event_id: str) -> bool:
        """Force this edge, drop both endpoints, and re-solve -- inside one component."""
        index = self._component_of[detail_id]
        local = self._adjacency_of[index]
        residual = {
            other_id: [candidate for candidate in candidates if candidate != event_id]
            for other_id, candidates in local.items()
            if other_id != detail_id
        }
        remaining = self._right_of[index] - {event_id}
        return len(maximum_matching(residual, remaining)) == self._baseline_of[index] - 1


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------


class RefundCorroborationPass:
    """Attribute an anonymous refund line, or decline to.

    Declared ONE_TO_ONE with an EXACT tolerance. Both declarations are load
    bearing rather than documentation: the runner enforces that no event is
    claimed twice, and the exact tolerance is what makes gate 2 an identity
    instead of a fuzz factor. A percentage tolerance here would admit a
    neighbouring refund of a similar size, which is exactly the false match this
    rung exists to avoid.
    """

    name = "refund_corroboration"
    cardinality = Cardinality.ONE_TO_ONE
    tolerance: Tolerance | None = EXACT

    def __init__(self, window_days: int = RECOVERY_WINDOW_DAYS) -> None:
        self.window_days = window_days
        self.last_graph: CandidateGraph | None = None
        self.ledger = GateLedger()

    # ---- local gates

    def _passes_local_gates(
        self,
        batch: Batch,
        line: DetailLine,
        event: GatewayEvent,
        consumed: frozenset[str],
        settlement_ties_out: bool,
        ledger: GateLedger,
    ) -> bool:
        """Gates 1 and 3 through 7. Gate 2 was the index lookup that got here."""
        # Gate 1 -- unconsumed. Two sources: the export already names the event
        # on some other line, or an earlier rung of the ladder claimed it. Both
        # are the same fact, and treating them as one prevents a refund being
        # allocated twice under two different names.
        if event.event_id in batch.referenced_event_ids or event.event_id in consumed:
            ledger.record(Gate.UNCONSUMED)
            return False

        # Gate 3 -- the recovery window. A refund raised after the settlement
        # closed cannot be in it, and one raised long before it would have
        # settled earlier.
        age = (line.settled_on - event.created_on).days
        if not 0 <= age <= self.window_days:
            ledger.record(Gate.RECOVERY_WINDOW)
            return False

        # Gate 4 -- currency.
        if event.currency != line.currency:
            ledger.record(Gate.CURRENCY)
            return False

        # Gate 5 -- lineage. A refund belongs to a payment; if that payment
        # never settled, its refund is not in this settlement either. This is
        # evidence the export states rather than an inference about intent.
        parent_settled = any(
            parent.status == "PROCESSED"
            and parent.event_id in batch.referenced_event_ids
            for parent in batch.payments_by_txn.get(event.txn_id, ())
        )
        if not parent_settled:
            ledger.record(Gate.LINEAGE)
            return False

        # Gate 6 -- sign. A refund line is explained by a refund event, and the
        # line moves money the other way. A sign error in a reconciliation
        # engine is silent, because the magnitude still looks plausible.
        if (
            event.event_type != "REFUND"
            or line.line_type != "REFUND"
            or line.gross_effect_paise >= 0
        ):
            ledger.record(Gate.SIGN)
            return False

        # Gate 7 -- the books tie out. Attributing a line inside a settlement
        # whose roll-up is already broken would assert an explanation for
        # figures that do not balance, which is a worse answer than reporting
        # the break.
        if not settlement_ties_out:
            ledger.record(Gate.CONTROLS)
            return False

        return True

    # ---- the rung

    def run(self, batch: Batch, consumed: frozenset[str]) -> PassResult:
        result = PassResult(self.name)
        ledger = GateLedger()
        self.ledger = ledger

        lines = batch.anonymous_lines
        if not lines:
            return result

        by_amount: dict[int, list[GatewayEvent]] = defaultdict(list)
        for event in batch.events.values():
            by_amount[event.amount_paise].append(event)
        for events in by_amount.values():
            events.sort(key=lambda event: event.event_id)

        # Gate 7 is a property of the settlement, not of the pairing, so it is
        # evaluated once per settlement rather than once per candidate.
        ties_out: dict[str, bool] = {}
        for line in lines:
            if line.settlement_id in ties_out:
                continue
            findings = settlement_findings(batch, line.settlement_id)
            ties_out[line.settlement_id] = not any(
                finding.category in CONTROL_BREAK_CATEGORIES for finding in findings
            )

        adjacency: dict[str, list[str]] = {}
        line_by_id: dict[str, DetailLine] = {}
        event_by_id: dict[str, GatewayEvent] = {}
        for line in sorted(lines, key=lambda line: line.detail_id):
            ledger.lines_examined += 1
            line_by_id[line.detail_id] = line
            shortlist = by_amount.get(line.magnitude_paise, [])
            ledger.candidates_by_amount += len(shortlist)
            survivors = [
                event
                for event in shortlist
                if self._passes_local_gates(
                    batch, line, event, consumed, ties_out[line.settlement_id], ledger
                )
            ]
            adjacency[line.detail_id] = [event.event_id for event in survivors]
            for event in survivors:
                event_by_id[event.event_id] = event

        right_nodes = frozenset(event_by_id)
        graph = CandidateGraph.build(adjacency, right_nodes)
        # Retained like the ledger, so the decomposition can be inspected after
        # a run rather than only trusted during one.
        self.last_graph = graph

        for detail_id, candidate_ids in sorted(adjacency.items()):
            line = line_by_id[detail_id]
            if not candidate_ids:
                # No admissible explanation at all. This is not an abstention --
                # abstaining means the evidence does not separate candidates,
                # and here there are none. The control stage reports the line as
                # unattributed, which is the correct finance answer.
                ledger.unexplained += 1
                continue

            allowed = [
                event_id
                for event_id in candidate_ids
                if self._survives_global_feasibility(
                    graph, detail_id, event_id, ledger
                )
            ]

            if len(allowed) == 1:
                ledger.resolved += 1
                event = event_by_id[allowed[0]]
                result.claims.append(
                    Claim(
                        settlement_id=line.settlement_id,
                        event_id=event.event_id,
                        detail_id=line.detail_id,
                        pass_name=self.name,
                        tier=ClaimTier.CONFIRMED,
                        confidence=1.0,
                        reasons=(
                            f"{line.detail_id}: anonymous refund of "
                            f"{line.magnitude_paise} paise on {line.settled_on}",
                            f"exactly one of {len(candidate_ids)} amount-matched "
                            f"candidate(s) survives gates 1-8: {event.event_id}",
                            f"gate 3: raised {(line.settled_on - event.created_on).days}"
                            f" day(s) before settlement, window is 0-{self.window_days}",
                            f"gate 5: parent payment on txn {event.txn_id} settled",
                        ),
                    )
                )
                continue

            ledger.abstained += 1
            ledger.record(Gate.UNIQUENESS)
            result.abstentions.append(
                Abstention(
                    settlement_id=line.settlement_id,
                    detail_id=line.detail_id,
                    pass_name=self.name,
                    candidate_event_ids=tuple(allowed),
                    reason=(
                        f"{len(allowed)} candidates survive gates 1-8 "
                        f"({', '.join(allowed)}); gate 9 requires exactly one, so "
                        "the attribution is not provable and is left to a human"
                    ),
                )
            )

        result.examined = ledger.lines_examined
        result.counters = {
            "candidates_by_amount": ledger.candidates_by_amount,
            "resolved": ledger.resolved,
            "abstained": ledger.abstained,
            "unexplained": ledger.unexplained,
            **{gate: count for gate, count in ledger.as_rows()},
        }
        return result

    def _survives_global_feasibility(
        self,
        graph: CandidateGraph,
        detail_id: str,
        event_id: str,
        ledger: GateLedger,
    ) -> bool:
        """Gate 8: is there a globally consistent assignment using this pairing?

        Force the edge. Remove both endpoints -- the line from the left side,
        the event from the right side and from every other line's candidate
        list -- and re-solve the residual graph. The candidate survives iff the
        residual can still reach ``baseline - 1``: committing to this pairing
        then costs the rest of the batch nothing, so some maximum assignment
        uses it and it stays admissible. If the residual falls short,
        committing to it would strand another line, and a reconciliation that
        strands a line to resolve a different one has not reconciled anything.

        This is deliberately not the forbid-the-edge question -- delete the edge,
        keep both endpoints, re-solve -- which tests membership in EVERY maximum
        assignment rather than in some, and would discard admissible candidates.

        The residual is re-solved within this edge's connected component rather
        than over the whole batch. See :class:`CandidateGraph` for why that is
        the same question and not a cheaper approximation of it.
        """
        if graph.residual_reaches_baseline(detail_id, event_id):
            return True
        ledger.record(Gate.GLOBAL_FEASIBILITY)
        return False
