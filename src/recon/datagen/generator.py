"""Case-first generation keeps defects local and labels constructive.

The answer key is built at the same moment as each case, never inferred from
the exports. That makes every deliberate break reviewable in the builder that
caused it, and makes a dataset a pure function of ``(GenConfig, seed)`` rather
than of wall-clock time or a post-hoc matching heuristic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .config import (
    GST_RATE_BPS,
    METHOD_FEE_RATE,
    METHOD_WEIGHTS,
    PAYMENT_METHODS,
    PRICE_POINTS,
    SCENARIO_OUTCOMES,
    GenConfig,
    Resolution,
    Scenario,
)
from .entities import (
    AnswerKeyAllocation,
    AnswerKeyCase,
    BankStatementRow,
    BatchConfigRow,
    Dataset,
    GatewayEvent,
    PricingRule,
    SettlementDetail,
    SettlementSummary,
    round_half_up,
)
from .catalogue import CATEGORIES, PRODUCTS, REFUND_NOTES, category_of
from .narration import settlement_narration

ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BANKS = ("HDFC", "ICICI", "AXIS", "KOTAK", "YESBANK")
CONTESTED_DISTRACTOR_ARCHETYPES = ("GATE_1", "GATE_3", "GATE_5")
# Later-cycle refunds cannot exceed 10,000,000 paise under the configured bands.
# Starting just above that ceiling makes the batch-global cardinality proof
# constructive rather than vulnerable to an unrelated random refund collision.
CONTESTED_AMOUNT_BASE_PAISE = 10_000_100
# Same trick, a decade higher, for the same reason: a described pair must not
# collide with a contested amount, an ambiguous amount, or an unrelated random
# refund, or the batch-global cardinality proof stops being constructive.
# Disjointness is asserted per seed rather than assumed.
DESCRIBED_AMOUNT_BASE_PAISE = 20_000_100

# Payment lines carry a note too. It has to be there -- a reference_text
# populated only on the hard lines would identify them by its own presence, and
# the agent could then tell a resolvable line from an unresolvable one without
# reading either. These phrasings deliberately carry nothing.
PAYMENT_LINE_NOTES = ("sale settlement", "settlement credit", "card sale credit")
UNATTRIBUTED_REFUND_NOTE = "refund adjustment"


@dataclass(frozen=True)
class _PlanItem:
    scenario: Scenario
    n_events: int
    ambiguous_group: int | None = None
    single_ambiguous_case: bool = False


@dataclass
class _BuiltCase:
    events: list[GatewayEvent]
    details: list[SettlementDetail]
    summaries: list[SettlementSummary]
    bank: list[BankStatementRow]
    allocations: list[AnswerKeyAllocation]
    key: AnswerKeyCase


class _IdFactory:
    """Seed-derived identifiers prevent both collisions and cross-seed overlap."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._seen: set[str] = set()

    def _unique(self, prefix: str, alphabet: str = ALNUM, length: int = 14) -> str:
        for _ in range(1000):
            candidate = prefix + "".join(self._rng.choice(alphabet) for _ in range(length))
            if candidate not in self._seen:
                self._seen.add(candidate)
                return candidate
        raise RuntimeError("identifier space exhausted")

    def event_id(self) -> str:
        return self._unique("evt_")

    def txn_id(self) -> str:
        return self._unique("pay_")

    def order_id(self) -> str:
        return self._unique("order_")

    def detail_id(self) -> str:
        return self._unique("dtl_")

    def settlement_id(self) -> str:
        return self._unique("setl_")

    def utr(self) -> str:
        return self._unique(
            self._rng.choice(("HDFC", "ICIC", "UTIB", "KKBK", "YESB")) + "N",
            "0123456789",
            11,
        )

    def bank_row_id(self) -> str:
        return self._unique("bank_")

    def bank_ref(self) -> str:
        return self._unique("S", "0123456789", 10)


class Generator:
    """Build every artifact from a single private seeded random stream."""

    def __init__(self, config: GenConfig | None = None) -> None:
        self.cfg = config or GenConfig()
        self.rng = random.Random(self.cfg.seed)
        self.ids = _IdFactory(self.rng)
        self._case_seq = 0
        self._corroborated_seq = 0
        self._contested_seq = 0
        self._contested_gate3_seq = 0
        self._described_seq = 0
        # txn_id -> catalogue category of the payment that opened it. A refund
        # row carries no description of its own -- it references its payment,
        # not the catalogue -- so a settlement line for a refund has to reach
        # the category through the transaction. That hop is deliberate: it is
        # what makes the evidence require lineage rather than a column read.
        self._txn_category: dict[str, str] = {}

    # ---------- deterministic planning ----------

    def _case_count(self) -> int:
        target = (self.cfg.n_records + 2) // 5
        minimum = (self.cfg.n_records + 6) // 7
        maximum = self.cfg.n_records // 3
        return min(max(target, minimum), maximum)

    def _scenario_counts(self, n_cases: int) -> list[tuple[Scenario, int]]:
        apportioned: list[list[object]] = []
        assigned = 0
        for index, (scenario, share) in enumerate(self.cfg.case_shares):
            scaled = n_cases * share
            count = scaled // 100
            apportioned.append([scenario, count, scaled % 100, index])
            assigned += count

        missing = n_cases - assigned
        order = sorted(
            range(len(apportioned)),
            key=lambda i: (-int(apportioned[i][2]), int(apportioned[i][3])),
        )
        for index in order[:missing]:
            apportioned[index][1] = int(apportioned[index][1]) + 1
        return [(row[0], int(row[1])) for row in apportioned]  # type: ignore[misc]

    def _plan(self) -> list[_PlanItem]:
        scenarios = [
            scenario
            for scenario, count in self._scenario_counts(self._case_count())
            for _ in range(count)
        ]
        self.rng.shuffle(scenarios)

        ambiguous_indexes = [
            index for index, scenario in enumerate(scenarios)
            if scenario is Scenario.AMBIGUOUS_REFUND
        ]
        ambiguous_groups: dict[int, int] = {}
        for ordinal, index in enumerate(ambiguous_indexes):
            if len(ambiguous_indexes) > 1 and len(ambiguous_indexes) % 2 and ordinal == len(ambiguous_indexes) - 1:
                group = (ordinal - 1) // 2
            else:
                group = ordinal // 2
            ambiguous_groups[index] = group

        minimum_sizes = []
        for scenario in scenarios:
            if scenario is Scenario.CONTESTED_REFUND:
                # Five rows leave room for two refund candidates, settled
                # lineage, a positive target settlement, and a gate-5 parent.
                minimum_sizes.append(5)
            elif scenario is Scenario.AMBIGUOUS_REFUND and len(ambiguous_indexes) == 1:
                minimum_sizes.append(4)
            elif scenario is Scenario.DESCRIBED_REFUND:
                # Both halves of the collision live in one case: two parents and
                # two refunds, with nothing left over.
                minimum_sizes.append(4)
            else:
                minimum_sizes.append(3)
        remaining = self.cfg.n_records - sum(minimum_sizes)
        if remaining < 0:
            raise ValueError("record budget cannot satisfy the 3-7 event case contract")

        sizes = list(minimum_sizes)
        while remaining:
            candidates = [index for index, size in enumerate(sizes) if size < 7]
            if not candidates:
                raise ValueError("record budget exceeds the 3-7 event case contract")
            sizes[self.rng.choice(candidates)] += 1
            remaining -= 1

        return [
            _PlanItem(
                scenario=scenario,
                n_events=sizes[index],
                ambiguous_group=ambiguous_groups.get(index),
                single_ambiguous_case=(len(ambiguous_indexes) == 1),
            )
            for index, scenario in enumerate(scenarios)
        ]

    # ---------- primitive construction ----------

    def _next_case_id(self) -> str:
        self._case_seq += 1
        return f"case_{self._case_seq:05d}"

    def _draw_amount_paise(self) -> int:
        """Draw money as paise; float draws select distributions, never values."""
        if self.rng.random() < self.cfg.price_point_share:
            return int(self.rng.choice(PRICE_POINTS) * Decimal(100))
        band = self.rng.choices(
            self.cfg.amount_bands,
            weights=[candidate[2] for candidate in self.cfg.amount_bands],
        )[0]
        lo_rupees, hi_rupees, _ = band
        if self.rng.random() < 0.60:
            return self.rng.randint(lo_rupees, hi_rupees) * 100
        return self.rng.randint(lo_rupees * 100, hi_rupees * 100)

    def _draw_method(self) -> str:
        return self.rng.choices(PAYMENT_METHODS, weights=METHOD_WEIGHTS)[0]

    def _draw_created_at(self) -> datetime:
        day = self.cfg.start_date + timedelta(days=self.rng.randrange(self.cfg.horizon_days))
        return datetime.combine(
            day,
            time(
                self.rng.randrange(0, 24),
                self.rng.randrange(0, 60),
                self.rng.randrange(0, 60),
            ),
        )

    def _settled_at(self, created_at: datetime, lag_days: int | None = None) -> datetime:
        lag = self.cfg.standard_lag_days if lag_days is None else lag_days
        day = created_at.date() + timedelta(days=lag)
        if self.cfg.skip_weekend_settlement:
            while day.weekday() == 6:
                day += timedelta(days=1)
        return datetime.combine(day, time(11, 30))

    def _fees(self, amount_paise: int, method: str) -> tuple[int, int]:
        fee = round_half_up(amount_paise * METHOD_FEE_RATE[method], 10_000)
        tax = round_half_up(fee * GST_RATE_BPS, 10_000)
        return fee, tax

    def _payment(
        self,
        *,
        created_at: datetime,
        status: str = "PROCESSED",
        amount_paise: int | None = None,
        method: str | None = None,
        category: str | None = None,
    ) -> GatewayEvent:
        # Every payment gets a catalogue description, not only the ones whose
        # category is load-bearing. A field populated selectively would let an
        # agent find the hard cases by looking for the populated field, which
        # would hand it the answer to the question the field exists to ask.
        chosen = category or self.rng.choice(CATEGORIES)
        txn_id = self.ids.txn_id()
        self._txn_category[txn_id] = chosen
        return GatewayEvent(
            event_id=self.ids.event_id(),
            event_type="PAYMENT",
            txn_id=txn_id,
            order_id=self.ids.order_id(),
            amount_paise=amount_paise if amount_paise is not None else self._draw_amount_paise(),
            currency="INR",
            status=status,
            created_at=created_at,
            method=method or self._draw_method(),
            description=self.rng.choice(PRODUCTS[chosen]),
        )

    def _refund(
        self,
        parent: GatewayEvent,
        *,
        amount_paise: int,
        created_at: datetime,
    ) -> GatewayEvent:
        return GatewayEvent(
            event_id=self.ids.event_id(),
            event_type="REFUND",
            txn_id=parent.txn_id,
            order_id=parent.order_id,
            amount_paise=amount_paise,
            currency=parent.currency,
            status="PROCESSED",
            created_at=created_at,
            method=parent.method,
        )

    def _detail(
        self,
        event: GatewayEvent,
        settlement_id: str,
        settled_at: datetime,
        *,
        anonymous: bool = False,
        variance: str | None = None,
    ) -> SettlementDetail:
        fee, tax = self._fees(event.amount_paise, event.method)
        if variance == "fee":
            fee += 100
            tax = round_half_up(fee * GST_RATE_BPS, 10_000)
        elif variance == "tax":
            tax += 1
        gross = event.amount_paise if event.event_type == "PAYMENT" else -event.amount_paise
        return SettlementDetail(
            detail_id=self.ids.detail_id(),
            settlement_id=settlement_id,
            event_id=None if anonymous else event.event_id,
            line_type=event.event_type,
            gross_effect_paise=gross,
            fee_paise=fee,
            tax_paise=tax,
            net_effect_paise=gross - fee - tax,
            settled_at=settled_at,
            currency=event.currency,
            reference_text=self._reference_text(event),
        )

    def _reference_text(self, event: GatewayEvent) -> str:
        """The operations note a settlement team would write on this line.

        Refund lines get a reason note in the vocabulary of the category the
        original purchase belongs to. Payment lines get a note that says
        nothing, because they must carry one: a column present only on refund
        lines would separate the anonymous refunds from everything else without
        anyone reading a word of it.

        The note never contains a reference, an identifier, or a product name.
        It shares no token with any catalogue string -- ``catalogue`` proves
        that at import -- so the bridge from note to payment is world knowledge
        and nothing else.
        """
        if event.event_type != "REFUND":
            return self.rng.choice(PAYMENT_LINE_NOTES)
        category = self._txn_category.get(event.txn_id)
        if category is None:
            return UNATTRIBUTED_REFUND_NOTE
        return self.rng.choice(REFUND_NOTES[category])

    def _summary(
        self,
        settlement_id: str,
        utr: str,
        settled_at: datetime,
        details: list[SettlementDetail],
    ) -> SettlementSummary:
        unique: list[SettlementDetail] = []
        seen: set[str] = set()
        for detail in details:
            if detail.detail_id not in seen:
                seen.add(detail.detail_id)
                unique.append(detail)

        gross_payment = sum(
            detail.gross_effect_paise for detail in unique if detail.line_type == "PAYMENT"
        )
        refund = -sum(
            detail.gross_effect_paise for detail in unique if detail.line_type == "REFUND"
        )
        fee = sum(detail.fee_paise for detail in unique)
        tax = sum(detail.tax_paise for detail in unique)
        return SettlementSummary(
            settlement_id=settlement_id,
            utr=utr,
            settlement_date=settled_at.date(),
            gross_payment_paise=gross_payment,
            refund_paise=refund,
            fee_paise=fee,
            tax_paise=tax,
            net_amount_paise=gross_payment - refund - fee - tax,
            line_count=len(unique),
            currency="INR",
            status="PROCESSED",
        )

    def _bank_rows(self, summary: SettlementSummary, count: int) -> list[BankStatementRow]:
        rows: list[BankStatementRow] = []
        for _ in range(count):
            bank = self.rng.choice(BANKS)
            rows.append(
                BankStatementRow(
                    bank_row_id=self.ids.bank_row_id(),
                    utr=summary.utr,
                    posted_at=datetime.combine(summary.settlement_date, time(14, 30)),
                    credit_amount_paise=summary.net_amount_paise,
                    currency=summary.currency,
                    narration=settlement_narration(bank, summary.utr),
                    bank_ref=self.ids.bank_ref(),
                )
            )
        return rows

    def _settlement(
        self,
        events: list[GatewayEvent],
        settled_at: datetime,
        *,
        anonymous_event_ids: set[str] | None = None,
        duplicate_export: bool = False,
        variance_event_id: str | None = None,
        bank_count: int = 1,
    ) -> tuple[list[SettlementDetail], SettlementSummary, list[BankStatementRow]]:
        settlement_id = self.ids.settlement_id()
        anonymous_ids = anonymous_event_ids or set()
        variance_kind = self.rng.choice(("fee", "tax")) if variance_event_id else None
        details = [
            self._detail(
                event,
                settlement_id,
                settled_at,
                anonymous=event.event_id in anonymous_ids,
                variance=variance_kind if event.event_id == variance_event_id else None,
            )
            for event in events
        ]
        unique_details = list(details)
        if duplicate_export:
            duplicate_count = min(2, len(unique_details))
            details.extend(unique_details[:duplicate_count])
        utr = self.ids.utr()
        summary = self._summary(settlement_id, utr, settled_at, details)
        return details, summary, self._bank_rows(summary, bank_count)

    @staticmethod
    def _allocations(
        event_ids: list[str],
        summary: SettlementSummary,
        bank_rows: list[BankStatementRow],
    ) -> list[AnswerKeyAllocation]:
        if not bank_rows:
            return [
                AnswerKeyAllocation(event_id, summary.settlement_id, None)
                for event_id in event_ids
            ]
        return [
            AnswerKeyAllocation(event_id, summary.settlement_id, bank.bank_row_id)
            for event_id in event_ids
            for bank in bank_rows
        ]

    @staticmethod
    def _category(scenario: Scenario) -> str | None:
        categories = {
            Scenario.DUPLICATE_DETAIL_EXPORT: "DUPLICATE_DETAIL_EXPORT_WARNING",
            Scenario.CAPTURED_UNSETTLED: "CAPTURED_UNSETTLED",
            Scenario.FEE_TAX_VARIANCE: "FEE_TAX_VARIANCE",
            Scenario.BANK_CREDIT_MISSING: "BANK_CREDIT_MISSING",
            Scenario.BANK_CREDIT_DUPLICATE: "BANK_CREDIT_DUPLICATE",
            Scenario.AMBIGUOUS_REFUND: "AMBIGUOUS_REFUND",
        }
        return categories.get(scenario)

    def _key(
        self,
        case_id: str,
        scenario: Scenario,
        events: list[GatewayEvent],
        summaries: list[SettlementSummary],
        bank: list[BankStatementRow],
        notes: str,
    ) -> AnswerKeyCase:
        return AnswerKeyCase(
            case_id=case_id,
            scenario=scenario,
            expected_outcome=SCENARIO_OUTCOMES[scenario],
            settlement_ids=tuple(summary.settlement_id for summary in summaries),
            bank_row_ids=tuple(row.bank_row_id for row in bank),
            event_ids=tuple(event.event_id for event in events),
            expected_exception_category=self._category(scenario),
            notes=notes,
        )

    # ---------- case builders ----------

    def _ordinary_case(self, case_id: str, scenario: Scenario, n_events: int) -> _BuiltCase:
        created_at = self._draw_created_at()
        events = [self._payment(created_at=created_at) for _ in range(n_events)]
        duplicate_export = scenario is Scenario.DUPLICATE_DETAIL_EXPORT
        variance_id = events[0].event_id if scenario is Scenario.FEE_TAX_VARIANCE else None
        bank_count = 0 if scenario is Scenario.BANK_CREDIT_MISSING else 1
        if scenario is Scenario.BANK_CREDIT_DUPLICATE:
            bank_count = 2
        details, summary, bank = self._settlement(
            events,
            self._settled_at(created_at),
            duplicate_export=duplicate_export,
            variance_event_id=variance_id,
            bank_count=bank_count,
        )
        allocations = self._allocations([event.event_id for event in events], summary, bank)
        notes = {
            Scenario.STRAIGHT_THROUGH: "all identifiers and control totals tie",
            Scenario.DUPLICATE_DETAIL_EXPORT: (
                "repeated detail_id rows are export duplicates; unique-id roll-up ties"
            ),
            Scenario.FEE_TAX_VARIANCE: (
                "one detail line violates exactly one pricing-rule equation"
            ),
            Scenario.BANK_CREDIT_MISSING: "settlement exists but has no bank row",
            Scenario.BANK_CREDIT_DUPLICATE: "the settlement UTR appears in two bank rows",
        }[scenario]
        return _BuiltCase(
            events,
            details,
            [summary],
            bank,
            allocations,
            self._key(case_id, scenario, events, [summary], bank, notes),
        )

    def _refund_later_case(self, case_id: str, n_events: int) -> _BuiltCase:
        created_at = self._draw_created_at()
        parent = self._payment(
            created_at=created_at,
            amount_paise=max(self._draw_amount_paise(), 100_000),
        )
        later_created = created_at + timedelta(days=2)
        later_payments = [
            self._payment(
                created_at=later_created,
                amount_paise=(
                    max(self._draw_amount_paise(), 100_000)
                    if index == 0 else self._draw_amount_paise()
                ),
            )
            for index in range(n_events - 2)
        ]
        refund_amount = max(
            5_000,
            min(parent.amount_paise, later_payments[0].amount_paise) // 4,
        )
        refund = self._refund(parent, amount_paise=refund_amount, created_at=later_created)
        events = [parent, refund, *later_payments]

        first = self._settlement([parent], self._settled_at(created_at))
        second_events = [refund, *later_payments]
        second = self._settlement(second_events, self._settled_at(later_created))
        details = [*first[0], *second[0]]
        summaries = [first[1], second[1]]
        bank = [*first[2], *second[2]]
        allocations = [
            *self._allocations([parent.event_id], first[1], first[2]),
            *self._allocations([event.event_id for event in second_events], second[1], second[2]),
        ]
        key = self._key(
            case_id,
            Scenario.REFUND_LATER_CYCLE,
            events,
            summaries,
            bank,
            "refund settles in the next cycle, after its parent payment",
        )
        return _BuiltCase(events, details, summaries, bank, allocations, key)

    def _corroborated_refund_case(self, case_id: str, n_events: int) -> _BuiltCase:
        created_at = self._draw_created_at()
        recovery_amount = 100 + self._corroborated_seq
        self._corroborated_seq += 1
        parent = self._payment(
            created_at=created_at,
            amount_paise=max(self._draw_amount_paise(), recovery_amount * 4, 5_000),
            method="upi",
        )
        refund = self._refund(
            parent,
            amount_paise=recovery_amount,
            created_at=created_at + timedelta(hours=1),
        )
        extras = [self._payment(created_at=created_at) for _ in range(n_events - 2)]
        events = [parent, refund, *extras]
        details, summary, bank = self._settlement(
            [parent, *extras, refund],
            self._settled_at(created_at),
            anonymous_event_ids={refund.event_id},
        )
        allocations = self._allocations([event.event_id for event in events], summary, bank)
        key = self._key(
            case_id,
            Scenario.CORROBORATED_REFUND,
            events,
            [summary],
            bank,
            "anonymous refund line has exactly one globally admissible event",
        )
        return _BuiltCase(events, details, [summary], bank, allocations, key)

    def _draw_contested_archetypes(self, n_events: int) -> list[str]:
        """Keep all three gates observable without making the remaining mix cyclic."""
        required = CONTESTED_DISTRACTOR_ARCHETYPES[
            self._contested_seq % len(CONTESTED_DISTRACTOR_ARCHETYPES)
        ]
        for _ in range(100):
            candidate_count = self.rng.randint(2, min(4, n_events - 2))
            archetypes = [required]
            archetypes.extend(
                self.rng.choice(CONTESTED_DISTRACTOR_ARCHETYPES)
                for _ in range(candidate_count - 2)
            )
            captured_parent_rows = int("GATE_5" in archetypes)
            if candidate_count + 2 + captured_parent_rows <= n_events:
                return archetypes
        raise RuntimeError("could not draw a contested-refund shape within its event budget")

    def _failed_recovery_gates_135(
        self,
        event: GatewayEvent,
        settlement_date: date,
        referenced_event_ids: set[str],
        payments_by_txn: dict[str, list[GatewayEvent]],
    ) -> set[str]:
        """Name failures so a distractor is evidence, not just generator intent."""
        failures: set[str] = set()
        if event.event_id in referenced_event_ids:
            failures.add("GATE_1")
        age = (settlement_date - event.created_at.date()).days
        if not 0 <= age <= self.cfg.recovery_window_days:
            failures.add("GATE_3")
        parent_settled = any(
            parent.status == "PROCESSED" and parent.event_id in referenced_event_ids
            for parent in payments_by_txn[event.txn_id]
        )
        if not parent_settled:
            failures.add("GATE_5")
        return failures

    def _assert_contested_draw(
        self,
        *,
        recovery_amount: int,
        admissible: GatewayEvent,
        distractors: list[tuple[GatewayEvent, str]],
        events: list[GatewayEvent],
        details: list[SettlementDetail],
        target: SettlementSummary,
        allocations: list[AnswerKeyAllocation],
    ) -> None:
        """Reject a label if observable gates do not prove exactly one allocation."""
        referenced = {detail.event_id for detail in details if detail.event_id is not None}
        payments_by_txn: dict[str, list[GatewayEvent]] = {}
        for event in events:
            if event.event_type == "PAYMENT":
                payments_by_txn.setdefault(event.txn_id, []).append(event)

        amount_candidates = [
            event
            for event in events
            if event.event_type == "REFUND" and event.amount_paise == recovery_amount
        ]
        assert 2 <= len(amount_candidates) <= 4
        survivors = [
            event
            for event in amount_candidates
            if not self._failed_recovery_gates_135(
                event,
                target.settlement_date,
                referenced,
                payments_by_txn,
            )
        ]
        assert [event.event_id for event in survivors] == [admissible.event_id]

        for event, label in distractors:
            named_gate = "_".join(label.split("_")[:2])
            failures = self._failed_recovery_gates_135(
                event,
                target.settlement_date,
                referenced,
                payments_by_txn,
            )
            assert failures == {named_gate}, (event.event_id, label, failures)

        allocated_event_ids = {allocation.event_id for allocation in allocations}
        admissible_allocations = [
            allocation
            for allocation in allocations
            if allocation.event_id == admissible.event_id
        ]
        assert admissible_allocations
        assert {
            allocation.settlement_id for allocation in admissible_allocations
        } == {target.settlement_id}
        # A distractor may never be allocated to the TARGET settlement -- that
        # would make it a second legitimate answer to the contested delta and
        # destroy the uniqueness the case is built to test.  It may however be
        # allocated elsewhere, and a GATE_1 distractor always is: failing gate 1
        # MEANS some other settlement already consumed it, and that consumption
        # is a fact the export states. Asserting it was allocated nowhere at all
        # is what left those pairs out of the key.
        distractor_ids = {event.event_id for event, _ in distractors}
        assert not distractor_ids & {
            allocation.event_id
            for allocation in allocations
            if allocation.settlement_id == target.settlement_id
        }

    def _contested_refund_case(self, case_id: str, n_events: int) -> _BuiltCase:
        archetypes = self._draw_contested_archetypes(n_events)
        recovery_amount = CONTESTED_AMOUNT_BASE_PAISE + self._contested_seq
        self._contested_seq += 1

        target_clock = self._settled_at(self._draw_created_at())
        target_date = target_clock.date()
        admissible_at = datetime.combine(
            target_date - timedelta(days=self.rng.randint(0, self.cfg.recovery_window_days)),
            time(9, 0),
        )

        distractor_specs: list[tuple[str, str, datetime]] = []
        for archetype in archetypes:
            if archetype == "GATE_1":
                created_at = datetime.combine(
                    target_date
                    - timedelta(days=self.rng.randint(0, self.cfg.recovery_window_days)),
                    time(9, 15),
                )
                label = "GATE_1_CONSUMED"
            elif archetype == "GATE_3":
                if self._contested_gate3_seq % 2 == 0:
                    created_at = datetime.combine(
                        target_date
                        - timedelta(
                            days=self.cfg.recovery_window_days + self.rng.randint(1, 3)
                        ),
                        time(9, 30),
                    )
                    label = "GATE_3_TOO_OLD"
                else:
                    created_at = datetime.combine(
                        target_date + timedelta(days=self.rng.randint(1, 3)),
                        time(9, 30),
                    )
                    label = "GATE_3_AFTER_SETTLEMENT"
                self._contested_gate3_seq += 1
            else:
                created_at = datetime.combine(
                    target_date
                    - timedelta(days=self.rng.randint(0, self.cfg.recovery_window_days)),
                    time(9, 45),
                )
                label = "GATE_5_BROKEN_LINEAGE"
            distractor_specs.append((archetype, label, created_at))

        settled_lineage_times = [admissible_at]
        settled_lineage_times.extend(
            created_at
            for archetype, _, created_at in distractor_specs
            if archetype != "GATE_5"
        )
        parent = self._payment(
            created_at=min(settled_lineage_times) - timedelta(days=1),
            amount_paise=(len(archetypes) + 3) * recovery_amount,
            method="upi",
        )
        admissible = self._refund(
            parent,
            amount_paise=recovery_amount,
            created_at=admissible_at,
        )

        captured_parent: GatewayEvent | None = None
        gate5_times = [
            created_at
            for archetype, _, created_at in distractor_specs
            if archetype == "GATE_5"
        ]
        if gate5_times:
            captured_parent = self._payment(
                created_at=min(gate5_times) - timedelta(days=1),
                status="CAPTURED",
                amount_paise=(len(gate5_times) + 2) * recovery_amount,
                method="upi",
            )

        distractors: list[tuple[GatewayEvent, str]] = []
        for archetype, label, created_at in distractor_specs:
            lineage = captured_parent if archetype == "GATE_5" else parent
            assert lineage is not None
            distractors.append(
                (
                    self._refund(
                        lineage,
                        amount_paise=recovery_amount,
                        created_at=created_at,
                    ),
                    label,
                )
            )

        carrier = self._payment(
            created_at=datetime.combine(target_date - timedelta(days=1), time(8, 0)),
            amount_paise=3 * recovery_amount,
            method="upi",
        )
        used_rows = 2 + 1 + len(distractors) + int(captured_parent is not None)
        extras = [
            self._payment(created_at=carrier.created_at)
            for _ in range(n_events - used_rows)
        ]

        target_events = [carrier, *extras, admissible]
        target_details, target_summary, target_bank = self._settlement(
            target_events,
            target_clock,
            anonymous_event_ids={admissible.event_id},
        )
        gate1_refunds = [
            event for event, label in distractors if label == "GATE_1_CONSUMED"
        ]
        support_events = [parent, *gate1_refunds]
        support_details, support_summary, support_bank = self._settlement(
            support_events,
            target_clock,
        )

        events = [
            parent,
            carrier,
            admissible,
            *(event for event, _ in distractors),
            *([captured_parent] if captured_parent is not None else []),
            *extras,
        ]
        details = [*target_details, *support_details]
        summaries = [target_summary, support_summary]
        bank = [*target_bank, *support_bank]
        allocations = [
            *self._allocations(
                [event.event_id for event in target_events],
                target_summary,
                target_bank,
            ),
            # The support settlement consumes the gate-1 distractors, and
            # consuming one means its detail line names the event outright.
            # Registering only the parent left those pairs absent from the key
            # while present in the export, so an agent that simply read the
            # file was scored as having invented them -- the published
            # false-match rate was measuring an incomplete key rather than any
            # error the agent made.
            *self._allocations(
                [parent.event_id, *(event.event_id for event in gate1_refunds)],
                support_summary,
                support_bank,
            ),
        ]
        self._assert_contested_draw(
            recovery_amount=recovery_amount,
            admissible=admissible,
            distractors=distractors,
            events=events,
            details=details,
            target=target_summary,
            allocations=allocations,
        )

        labels = "|".join(
            f"{event.event_id}:{label}" for event, label in distractors
        )
        key = self._key(
            case_id,
            Scenario.CONTESTED_REFUND,
            events,
            summaries,
            bank,
            (
                "amount collision resolves uniquely after gates 1, 3, and 5; "
                f"target={target_summary.settlement_id}; distractors={labels}"
            ),
        )
        return _BuiltCase(events, details, summaries, bank, allocations, key)

    def _ambiguous_time(self, group: int) -> datetime:
        span = max(1, self.cfg.horizon_days - 2)
        day = self.cfg.start_date + timedelta(days=(group * 3) % span)
        return datetime.combine(day, time(10, 0))

    def _ambiguous_category(self, group: int) -> str:
        """The single category both halves of an ambiguous pair must share.

        A function of the group rather than of the case, because the two
        colliding refunds usually live in two different cases and never see one
        another. Derived deterministically for the same reason ``_ambiguous_time``
        is: it is the only way two independently built cases can agree without
        passing state between them.

        Sharing the category is what keeps this class unresolvable now that
        every line carries an operations note. If the two parents came from
        different categories the note would name one of them, and AMBIGUOUS
        would quietly become DESCRIBED -- an agent rewarded for resolving a case
        the answer key says it must decline.
        """
        return CATEGORIES[group % len(CATEGORIES)]

    def _ambiguous_refund_case(self, case_id: str, item: _PlanItem) -> _BuiltCase:
        assert item.ambiguous_group is not None
        created_at = self._ambiguous_time(item.ambiguous_group)
        category = self._ambiguous_category(item.ambiguous_group)
        recovery_amount = 2_500 + item.ambiguous_group
        pair_count = 2 if item.single_ambiguous_case else 1

        parents: list[GatewayEvent] = []
        refunds: list[GatewayEvent] = []
        for offset in range(pair_count):
            parent = self._payment(
                created_at=created_at + timedelta(minutes=offset),
                amount_paise=max(self._draw_amount_paise(), recovery_amount * 4, 5_000),
                method="upi",
                category=category,
            )
            parents.append(parent)
            refunds.append(
                self._refund(
                    parent,
                    amount_paise=recovery_amount,
                    created_at=created_at + timedelta(hours=1, minutes=offset),
                )
            )

        extras = [
            self._payment(created_at=created_at)
            for _ in range(item.n_events - pair_count * 2)
        ]
        events = [*parents, *refunds, *extras]
        details: list[SettlementDetail] = []
        summaries: list[SettlementSummary] = []
        bank: list[BankStatementRow] = []
        allocations: list[AnswerKeyAllocation] = []

        for index, (parent, refund) in enumerate(zip(parents, refunds)):
            settlement_events = [parent, refund]
            if index == 0:
                settlement_events[1:1] = extras
            built = self._settlement(
                settlement_events,
                self._settled_at(created_at),
                anonymous_event_ids={refund.event_id},
            )
            details.extend(built[0])
            summaries.append(built[1])
            bank.extend(built[2])
            allocations.extend(
                self._allocations(
                    [event.event_id for event in settlement_events if event.event_type == "PAYMENT"],
                    built[1],
                    built[2],
                )
            )

        key = self._key(
            case_id,
            Scenario.AMBIGUOUS_REFUND,
            events,
            summaries,
            bank,
            "each anonymous refund delta has at least two globally admissible "
            "events, and both share a product category so the settlement note "
            "cannot separate them",
        )
        return _BuiltCase(events, details, summaries, bank, allocations, key)

    def _described_refund_case(self, case_id: str, n_events: int) -> _BuiltCase:
        """Two identical anonymous refunds that only the settlement note separates.

        Structurally this is AMBIGUOUS_REFUND: two parent payments, two refunds
        of the same amount, two settlements, one anonymous refund line in each.
        Every gate in the corroboration pass behaves identically on both
        candidates -- both unconsumed, both exact on amount, both inside the
        window, both with settled parents, both signed correctly, both globally
        feasible -- so gate 9 finds a second survivor and the deterministic
        ladder abstains. That abstention is scored as a miss here, and it is
        supposed to be.

        The one difference from AMBIGUOUS is that the two parents come from
        DIFFERENT catalogue categories, and each settlement line carries an
        operations note written in the vocabulary of its own category. The note
        shares no token with any product string, so lexical similarity ranks the
        right candidate exactly level with the wrong one. Reading the note and
        knowing that a Hawkins pressure cooker is kitchenware resolves it.

        Both cases are built inside a single case rather than paired across two,
        because the category assignment has to be jointly constrained and doing
        that across independent cases would need shared mutable state.
        """
        self._described_seq += 1
        recovery_amount = DESCRIBED_AMOUNT_BASE_PAISE + self._described_seq
        created_at = self._draw_created_at()
        left, right = self.rng.sample(CATEGORIES, 2)

        parents: list[GatewayEvent] = []
        refunds: list[GatewayEvent] = []
        for offset, category in enumerate((left, right)):
            parent = self._payment(
                created_at=created_at + timedelta(minutes=offset),
                # Comfortably above the refund so the settlement still nets to a
                # positive credit; upi so the fee is zero and the margin is the
                # whole story.
                amount_paise=max(self._draw_amount_paise(), recovery_amount + 100_000),
                method="upi",
                category=category,
            )
            parents.append(parent)
            refunds.append(
                self._refund(
                    parent,
                    amount_paise=recovery_amount,
                    created_at=created_at + timedelta(hours=1, minutes=offset),
                )
            )

        extras = [self._payment(created_at=created_at) for _ in range(n_events - 4)]
        events = [*parents, *refunds, *extras]
        details: list[SettlementDetail] = []
        summaries: list[SettlementSummary] = []
        bank: list[BankStatementRow] = []
        allocations: list[AnswerKeyAllocation] = []

        for index, (parent, refund) in enumerate(zip(parents, refunds)):
            settlement_events = [parent, refund]
            if index == 0:
                settlement_events[1:1] = extras
            built = self._settlement(
                settlement_events,
                self._settled_at(created_at),
                anonymous_event_ids={refund.event_id},
            )
            details.extend(built[0])
            summaries.append(built[1])
            bank.extend(built[2])
            # Unlike AMBIGUOUS, the refund events ARE allocated. The correct
            # attribution is knowable, so resolving it scores as a true positive
            # and abstaining scores as a miss. That asymmetry is the whole point
            # of the pair of classes: an agent that always resolves scores zero
            # on AMBIGUOUS, one that always declines scores zero here, and only
            # one that actually reads the note scores on both.
            allocations.extend(
                self._allocations(
                    [event.event_id for event in settlement_events],
                    built[1],
                    built[2],
                )
            )

        key = self._key(
            case_id,
            Scenario.DESCRIBED_REFUND,
            events,
            summaries,
            bank,
            "two indistinguishable refund deltas separated only by the product "
            "category named in each settlement note",
        )
        return _BuiltCase(events, details, summaries, bank, allocations, key)

    def _unsettled_case(self, case_id: str, scenario: Scenario, n_events: int) -> _BuiltCase:
        created_at = self._draw_created_at()
        if scenario is Scenario.CAPTURED_UNSETTLED:
            events = [
                self._payment(created_at=created_at, status="CAPTURED")
                for _ in range(n_events)
            ]
            notes = "captured payments should settle but occur in no settlement"
        else:
            events = [
                self._payment(
                    created_at=created_at,
                    status=self.rng.choice(("CREATED", "FAILED")),
                )
                for _ in range(n_events)
            ]
            notes = "created/failed events are intentionally not settleable"
        key = self._key(case_id, scenario, events, [], [], notes)
        return _BuiltCase(events, [], [], [], [], key)

    def _build_case(self, item: _PlanItem) -> _BuiltCase:
        case_id = self._next_case_id()
        if item.scenario in {
            Scenario.STRAIGHT_THROUGH,
            Scenario.DUPLICATE_DETAIL_EXPORT,
            Scenario.FEE_TAX_VARIANCE,
            Scenario.BANK_CREDIT_MISSING,
            Scenario.BANK_CREDIT_DUPLICATE,
        }:
            return self._ordinary_case(case_id, item.scenario, item.n_events)
        if item.scenario is Scenario.REFUND_LATER_CYCLE:
            return self._refund_later_case(case_id, item.n_events)
        if item.scenario is Scenario.CORROBORATED_REFUND:
            return self._corroborated_refund_case(case_id, item.n_events)
        if item.scenario is Scenario.CONTESTED_REFUND:
            return self._contested_refund_case(case_id, item.n_events)
        if item.scenario is Scenario.AMBIGUOUS_REFUND:
            return self._ambiguous_refund_case(case_id, item)
        if item.scenario is Scenario.DESCRIBED_REFUND:
            return self._described_refund_case(case_id, item.n_events)
        if item.scenario in {Scenario.CAPTURED_UNSETTLED, Scenario.NOT_SETTLEABLE}:
            return self._unsettled_case(case_id, item.scenario, item.n_events)
        raise ValueError(f"unhandled scenario {item.scenario}")

    def _assert_recovery_class_distinction(
        self,
        events: list[GatewayEvent],
        details: list[SettlementDetail],
        cases: list[AnswerKeyCase],
        allocations: list[AnswerKeyAllocation],
    ) -> None:
        """Prove globally that corroboration resolves contested but not ambiguous ties."""
        referenced = {detail.event_id for detail in details if detail.event_id is not None}
        payments_by_txn: dict[str, list[GatewayEvent]] = {}
        for event in events:
            if event.event_type == "PAYMENT":
                payments_by_txn.setdefault(event.txn_id, []).append(event)
        details_by_settlement: dict[str, list[SettlementDetail]] = {}
        for detail in details:
            details_by_settlement.setdefault(detail.settlement_id, []).append(detail)

        allocated_event_ids = {allocation.event_id for allocation in allocations}
        contested_amounts: set[int] = set()
        ambiguous_amounts: set[int] = set()
        described_amounts: set[int] = set()
        contested_ids: set[str] = set()
        ambiguous_ids: set[str] = set()
        described_ids: set[str] = set()

        note_category = {
            note: category
            for category, notes in REFUND_NOTES.items()
            for note in notes
        }

        def parent_category(event: GatewayEvent) -> str | None:
            """The catalogue category of the payment that opened this refund.

            Read back off the emitted description rather than off the
            generator's own bookkeeping, so the assertion proves a property of
            the data an agent will actually receive. Checking ``_txn_category``
            instead would prove only that the generator agrees with itself.
            """
            for payment in payments_by_txn.get(event.txn_id, []):
                category = category_of(payment.description)
                if category is not None:
                    return category
            return None

        for case in cases:
            if case.scenario not in {
                Scenario.CONTESTED_REFUND,
                Scenario.AMBIGUOUS_REFUND,
                Scenario.DESCRIBED_REFUND,
            }:
                continue
            anonymous = [
                detail
                for settlement_id in case.settlement_ids
                for detail in details_by_settlement.get(settlement_id, [])
                if detail.line_type == "REFUND" and detail.event_id is None
            ]
            if case.scenario is Scenario.CONTESTED_REFUND:
                assert len(anonymous) == 1
                contested_ids.add(case.case_id)
            elif case.scenario is Scenario.DESCRIBED_REFUND:
                assert len(anonymous) == 2
                described_ids.add(case.case_id)
            else:
                assert anonymous
                ambiguous_ids.add(case.case_id)

            for detail in anonymous:
                amount = -detail.gross_effect_paise
                candidates = [
                    event
                    for event in events
                    if event.event_type == "REFUND" and event.amount_paise == amount
                ]
                survivors = [
                    event
                    for event in candidates
                    if not self._failed_recovery_gates_135(
                        event,
                        detail.settled_at.date(),
                        referenced,
                        payments_by_txn,
                    )
                ]
                if case.scenario is Scenario.CONTESTED_REFUND:
                    contested_amounts.add(amount)
                    assert 2 <= len(candidates) <= 4
                    assert len(survivors) == 1
                    survivor_allocations = [
                        allocation
                        for allocation in allocations
                        if allocation.event_id == survivors[0].event_id
                    ]
                    assert survivor_allocations
                    assert {
                        allocation.settlement_id for allocation in survivor_allocations
                    } == {detail.settlement_id}
                    case_refunds = {
                        event.event_id
                        for event in events
                        if event.event_id in case.event_ids
                        and event.event_type == "REFUND"
                        and event.amount_paise == amount
                    }
                    # Same narrowing as the per-case check: a losing candidate
                    # must not be allocated to the settlement carrying the
                    # anonymous line, because that would give the delta a second
                    # valid answer.  Being allocated to a DIFFERENT settlement is
                    # exactly how a gate-1 candidate loses, and the key has to
                    # say so -- the export already does.
                    assert not (case_refunds - {survivors[0].event_id}) & {
                        allocation.event_id
                        for allocation in allocations
                        if allocation.settlement_id == detail.settlement_id
                    }
                elif case.scenario is Scenario.DESCRIBED_REFUND:
                    described_amounts.add(amount)
                    # Arithmetic and the gates leave the tie standing -- if they
                    # did not, this class would be closed by the deterministic
                    # ladder and would measure nothing about reading.
                    assert len(survivors) >= 2
                    # ...and the note resolves it, uniquely. Exactly one
                    # surviving candidate traces back to a payment in the
                    # category the note is written about. Without this the class
                    # would be unfair rather than hard: an agent could read
                    # perfectly and still have no defensible answer.
                    wanted = note_category.get(detail.reference_text or "")
                    assert wanted is not None, (
                        f"described line {detail.detail_id} carries no category note"
                    )
                    named = [
                        event for event in survivors
                        if parent_category(event) == wanted
                    ]
                    assert len(named) == 1, (
                        f"described line {detail.detail_id} note names "
                        f"{len(named)} of {len(survivors)} surviving candidates"
                    )
                    assert named[0].event_id in allocated_event_ids
                    assert any(
                        allocation.event_id == named[0].event_id
                        and allocation.settlement_id == detail.settlement_id
                        for allocation in allocations
                    )
                else:
                    ambiguous_amounts.add(amount)
                    assert len(survivors) >= 2
                    # The mirror image of the assertion above, and the reason
                    # this class survived the arrival of the note column: every
                    # surviving candidate sits in the SAME category the note is
                    # written about, so reading it perfectly still separates
                    # nothing. An agent is expected to decline here, and it has
                    # to be genuinely undecidable for that to be the right call.
                    wanted = note_category.get(detail.reference_text or "")
                    assert wanted is not None
                    assert all(
                        parent_category(event) == wanted for event in survivors
                    ), (
                        f"ambiguous line {detail.detail_id} note separates its "
                        f"candidates by category, which makes it resolvable"
                    )

        assert contested_ids.isdisjoint(ambiguous_ids)
        assert contested_ids.isdisjoint(described_ids)
        assert ambiguous_ids.isdisjoint(described_ids)
        assert contested_amounts.isdisjoint(ambiguous_amounts)
        assert contested_amounts.isdisjoint(described_amounts)
        assert ambiguous_amounts.isdisjoint(described_amounts)

    # ---------- public orchestration ----------

    def generate(self) -> Dataset:
        plan = self._plan()
        events: list[GatewayEvent] = []
        details: list[SettlementDetail] = []
        summaries: list[SettlementSummary] = []
        bank: list[BankStatementRow] = []
        cases: list[AnswerKeyCase] = []
        allocations: list[AnswerKeyAllocation] = []

        for item in plan:
            built = self._build_case(item)
            events.extend(built.events)
            details.extend(built.details)
            summaries.extend(built.summaries)
            bank.extend(built.bank)
            allocations.extend(built.allocations)
            cases.append(built.key)

        self._assert_recovery_class_distinction(
            events,
            details,
            cases,
            allocations,
        )

        # File positions are not evidence. Separate shuffles prevent accidental
        # case-wise alignment while remaining deterministic under the one RNG.
        self.rng.shuffle(events)
        self.rng.shuffle(details)
        self.rng.shuffle(summaries)
        self.rng.shuffle(bank)
        self.rng.shuffle(allocations)

        generated_at = datetime.combine(self.cfg.start_date, time(0, 0))
        batch_id = f"batch_{self.cfg.family.value}_{self.cfg.seed}"
        batch_config = [
            BatchConfigRow(
                batch_id=batch_id,
                seed=self.cfg.seed,
                family=self.cfg.family,
                n_gateway_events=len(events),
                n_cases=len(cases),
                generated_at=generated_at,
            )
        ]
        pricing_rules = [
            PricingRule(method, METHOD_FEE_RATE[method], GST_RATE_BPS)
            for method in PAYMENT_METHODS
        ]

        scenario_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        for case in cases:
            scenario_counts[case.scenario.value] = scenario_counts.get(case.scenario.value, 0) + 1
            outcome_counts[case.expected_outcome.value] = outcome_counts.get(case.expected_outcome.value, 0) + 1

        meta: dict[str, object] = {
            "batch_id": batch_id,
            "seed": self.cfg.seed,
            "family": self.cfg.family.value,
            "n_records_requested": self.cfg.n_records,
            "n_gateway_events": len(events),
            "n_gateway_rows": len(events),
            "n_cases": len(cases),
            "n_settlement_detail_rows": len(details),
            "n_settlements": len(summaries),
            "n_bank_rows": len(bank),
            "n_allocations": len(allocations),
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "scenario_case_counts": dict(sorted(scenario_counts.items())),
            "expected_outcome_case_counts": dict(sorted(outcome_counts.items())),
        }
        return Dataset(
            batch_config=batch_config,
            pricing_rules=pricing_rules,
            gateway=events,
            details=details,
            summaries=summaries,
            bank=bank,
            cases=cases,
            allocations=allocations,
            config_meta=meta,
        )


def generate(config: GenConfig | None = None) -> Dataset:
    return Generator(config).generate()
