"""The synthetic dataset generator.

Design contract
---------------
1. A dataset is a pure function of (GenConfig, seed).  Same inputs, byte-identical
   outputs.  This is what makes "held-out seed" a meaningful claim.
2. The answer key is *constructed alongside* the data, never inferred from it
   afterwards.  Recovering ground truth by re-reading the CSVs would mean
   writing a matcher to label the test set for the matcher, which is circular.
3. Row order in both output files is shuffled.  Positional alignment between the
   two files must never be exploitable as a matching signal.
4. Every gateway txn and every bank credit belongs to exactly one TruthLink.
   The self-validation suite in tests/ enforces this rather than trusting it.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from .config import (
    AmountBasis,
    FX_RATES,
    FX_SPREAD,
    GST_RATE,
    GenConfig,
    METHOD_FEE_RATE,
    METHOD_WEIGHTS,
    PAYMENT_METHODS,
    PRICE_POINTS,
    Resolution,
    Scenario,
)
from .entities import BankCredit, Dataset, GatewayTxn, TruthLink, TruthPair, money
from .narration import clean_narration, foreign_narration, garble, make_reference

ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BANK_PREFIXES = ["HDFC", "ICIC", "UTIB", "KKBK", "YESB"]

# Scenarios that need a fee-bearing method for the defect to be observable at
# all: netting a 0% UPI fee changes nothing.
NEEDS_FEE = {Scenario.FEE_DEDUCTION}


class _IdFactory:
    """Collision-free, seed-deterministic identifier minting."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._seen: set[str] = set()

    def _unique(self, make) -> str:
        for _ in range(1000):
            candidate = make()
            if candidate not in self._seen:
                self._seen.add(candidate)
                return candidate
        raise RuntimeError("identifier space exhausted")

    def txn_id(self) -> str:
        return self._unique(
            lambda: "pay_" + "".join(self._rng.choice(ALNUM) for _ in range(14))
        )

    def order_id(self) -> str:
        return self._unique(
            lambda: "order_" + "".join(self._rng.choice(ALNUM) for _ in range(14))
        )

    def utr(self) -> str:
        return self._unique(
            lambda: self._rng.choice(BANK_PREFIXES)
            + "N"
            + "".join(self._rng.choice("0123456789") for _ in range(11))
        )

    def bank_ref(self) -> str:
        return self._unique(
            lambda: "S" + "".join(self._rng.choice("0123456789") for _ in range(10))
        )

    def batch_ref(self) -> str:
        return self._unique(
            lambda: "BATCH"
            + "".join(self._rng.choice("0123456789") for _ in range(8))
        )


class Generator:
    def __init__(self, config: GenConfig | None = None) -> None:
        self.cfg = config or GenConfig()
        self.rng = random.Random(self.cfg.seed)
        self.ids = _IdFactory(self.rng)
        self._link_seq = 0

    # ---------- primitive draws ----------

    def _next_link_id(self) -> str:
        self._link_seq += 1
        return f"L{self._link_seq:05d}"

    def _draw_amount(self) -> Decimal:
        """Draw a gross ticket amount.

        Deliberately bimodal: a catalogue price point most of the time, a
        continuous draw otherwise.  See PRICE_POINTS in config.py for why the
        clustering is load-bearing rather than cosmetic.
        """
        if self.rng.random() < self.cfg.price_point_share:
            return money(self.rng.choice(PRICE_POINTS))
        bands = self.cfg.amount_bands
        lo, hi, _ = self.rng.choices(bands, weights=[b[2] for b in bands])[0]
        raw = self.rng.uniform(lo, hi)
        if self.rng.random() < 0.60:            # most real prices are whole rupees
            return money(Decimal(int(raw)))
        return money(Decimal(f"{raw:.2f}"))

    def _draw_method(self, needs_fee: bool = False) -> str:
        if needs_fee:
            options = [m for m in PAYMENT_METHODS if METHOD_FEE_RATE[m] > 0]
            weights = [METHOD_WEIGHTS[PAYMENT_METHODS.index(m)] for m in options]
            return self.rng.choices(options, weights=weights)[0]
        return self.rng.choices(PAYMENT_METHODS, weights=METHOD_WEIGHTS)[0]

    def _draw_created_at(self) -> datetime:
        day = self.cfg.start_date + timedelta(
            days=self.rng.randrange(self.cfg.horizon_days)
        )
        return datetime(
            day.year, day.month, day.day,
            self.rng.randrange(0, 24), self.rng.randrange(0, 60), self.rng.randrange(0, 60),
        )

    def _settle_date(self, created: datetime, lag_days: int) -> date:
        d = created.date() + timedelta(days=lag_days)
        if self.cfg.skip_weekend_settlement:
            while d.weekday() == 6:             # banks do not run Sunday batches
                d += timedelta(days=1)
        return d

    # ---------- record construction ----------

    def _make_txn(
        self,
        *,
        scenario: Scenario,
        currency: str = "INR",
        status: str = "captured",
        created_at: datetime | None = None,
    ) -> GatewayTxn:
        method = self._draw_method(needs_fee=scenario in NEEDS_FEE)
        amount = self._draw_amount()
        if currency != "INR":
            # Foreign-currency tickets are denominated in the foreign unit, so
            # scale the INR-shaped draw down into a plausible range.
            amount = money(amount / FX_RATES[currency])
        fee = money(amount * METHOD_FEE_RATE[method])
        tax = money(fee * GST_RATE)
        return GatewayTxn(
            txn_id=self.ids.txn_id(),
            order_id=self.ids.order_id(),
            amount=amount,
            currency=currency,
            status=status,
            created_at=created_at or self._draw_created_at(),
            method=method,
            fee=fee,
            tax=tax,
            net_amount=money(amount - fee - tax),
            refund_amount=money(Decimal(0)),
        )

    def _basis_amount(self, txn: GatewayTxn, basis: AmountBasis) -> Decimal:
        if basis is AmountBasis.NET:
            return txn.net_amount
        if basis is AmountBasis.GROSS:
            return txn.amount
        return money(txn.amount - txn.fee)

    def _to_inr(self, txn: GatewayTxn, amount: Decimal) -> tuple[Decimal, Decimal | None]:
        """Convert a settle-basis amount into the INR the bank actually credits."""
        if txn.currency == "INR":
            return money(amount), None
        rate = money(FX_RATES[txn.currency] * (Decimal(1) - FX_SPREAD))
        return money(amount * rate), rate

    def _make_credit(
        self,
        *,
        inr_amount: Decimal,
        settlement_date: date,
        narration: str,
    ) -> BankCredit:
        return BankCredit(
            utr=self.ids.utr(),
            settlement_date=settlement_date,
            credit_amount=money(inr_amount),
            narration=narration,
            bank_ref=self.ids.bank_ref(),
        )

    # ---------- scenario builders ----------
    # Each returns (txns, credits, link).  The link is the ground truth; nothing
    # downstream may re-derive it from the records.

    def _simple_settled(
        self,
        scenario: Scenario,
        *,
        lag: int | None = None,
        basis: AmountBasis = AmountBasis.NET,
        currency: str = "INR",
        rounding_delta: Decimal = Decimal("0"),
        garbled: bool = False,
    ) -> tuple[list[GatewayTxn], list[BankCredit], TruthLink]:
        txn = self._make_txn(scenario=scenario, currency=currency)
        basis_amount = self._basis_amount(txn, basis)
        expected_inr, rate = self._to_inr(txn, basis_amount)
        actual_inr = money(expected_inr + rounding_delta)

        lag_days = self.cfg.standard_lag_days if lag is None else lag
        settle_on = self._settle_date(txn.created_at, lag_days)

        ref = make_reference(txn.order_id)
        narration = clean_narration(self.rng, ref)
        recoverable = True
        if garbled:
            narration, recoverable = garble(self.rng, narration, ref)

        credit = self._make_credit(
            inr_amount=actual_inr, settlement_date=settle_on, narration=narration
        )

        notes = []
        if lag_days != self.cfg.standard_lag_days:
            notes.append(f"settlement lag T+{lag_days}")
        if rounding_delta:
            notes.append(f"rounding delta {rounding_delta:+.2f}")
        if rate is not None:
            notes.append(f"fx {txn.currency}->INR @ {rate}")
        if garbled:
            notes.append(
                f"narration garbled; reference {'recoverable' if recoverable else 'destroyed'}"
            )

        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=scenario,
            resolution=Resolution.AUTO_MATCH,
            txn_ids=[txn.txn_id],
            utrs=[credit.utr],
            amount_basis=basis,
            expected_inr_total=expected_inr,
            actual_inr_total=actual_inr,
            notes="; ".join(notes),
        )
        return [txn], [credit], link

    def _many_to_one(self, k: int):
        capture_day = self._draw_created_at()
        txns = [
            self._make_txn(
                scenario=Scenario.MANY_TO_ONE,
                created_at=capture_day.replace(
                    hour=self.rng.randrange(0, 24), minute=self.rng.randrange(0, 60)
                ),
            )
            for _ in range(k)
        ]
        total = money(sum((t.net_amount for t in txns), Decimal(0)))
        settle_on = self._settle_date(capture_day, self.cfg.standard_lag_days)
        credit = self._make_credit(
            inr_amount=total,
            settlement_date=settle_on,
            narration=clean_narration(self.rng, self.ids.batch_ref(), batch=True),
        )
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.MANY_TO_ONE,
            resolution=Resolution.AUTO_MATCH,
            txn_ids=[t.txn_id for t in txns],
            utrs=[credit.utr],
            amount_basis=AmountBasis.NET,
            expected_inr_total=total,
            actual_inr_total=total,
            notes=f"batch settlement of {k} txns; narration carries no per-txn reference",
        )
        return txns, [credit], link

    def _duplicate_settlement(self):
        txn = self._make_txn(scenario=Scenario.DUPLICATE_SETTLEMENT)
        ref = make_reference(txn.order_id)
        first_date = self._settle_date(txn.created_at, self.cfg.standard_lag_days)
        second_date = self._settle_date(txn.created_at, self.cfg.standard_lag_days + self.rng.randint(0, 2))
        credits = [
            self._make_credit(
                inr_amount=txn.net_amount,
                settlement_date=d,
                narration=clean_narration(self.rng, ref),
            )
            for d in (first_date, second_date)
        ]
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.DUPLICATE_SETTLEMENT,
            resolution=Resolution.EXCEPTION_DUPLICATE,
            txn_ids=[txn.txn_id],
            utrs=[c.utr for c in credits],
            amount_basis=AmountBasis.NET,
            expected_inr_total=txn.net_amount,
            actual_inr_total=money(txn.net_amount * 2),
            notes="settled twice; agent must link both credits AND flag the overpayment",
        )
        return [txn], credits, link

    def _missing_on_bank(self):
        txn = self._make_txn(scenario=Scenario.MISSING_ON_BANK)
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.MISSING_ON_BANK,
            resolution=Resolution.EXCEPTION_UNSETTLED,
            txn_ids=[txn.txn_id],
            utrs=[],
            amount_basis=AmountBasis.NET,
            expected_inr_total=txn.net_amount,
            actual_inr_total=None,
            notes="captured but never settled; true exception",
        )
        return [txn], [], link

    def _missing_on_gateway(self):
        credit = self._make_credit(
            inr_amount=self._draw_amount(),
            settlement_date=self._settle_date(self._draw_created_at(), 0),
            narration=foreign_narration(self.rng),
        )
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.MISSING_ON_GATEWAY,
            resolution=Resolution.EXCEPTION_UNEXPLAINED_CREDIT,
            txn_ids=[],
            utrs=[credit.utr],
            amount_basis=None,
            expected_inr_total=None,
            actual_inr_total=credit.credit_amount,
            notes="credit with no gateway counterpart; true exception",
        )
        return [], [credit], link

    def _partial_refund(self):
        txn = self._make_txn(scenario=Scenario.PARTIAL_REFUND, status="partially_refunded")
        lo, hi = self.cfg.partial_refund_fraction
        refund = money(txn.amount * Decimal(str(self.rng.uniform(lo, hi))))
        txn = replace(txn, refund_amount=refund)
        settled = money(txn.net_amount - refund)
        settle_on = self._settle_date(txn.created_at, self.cfg.standard_lag_days)
        credit = self._make_credit(
            inr_amount=settled,
            settlement_date=settle_on,
            narration=clean_narration(self.rng, make_reference(txn.order_id)),
        )
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.PARTIAL_REFUND,
            resolution=Resolution.AUTO_MATCH,
            txn_ids=[txn.txn_id],
            utrs=[credit.utr],
            amount_basis=AmountBasis.NET,
            expected_inr_total=settled,
            actual_inr_total=settled,
            notes=f"partial refund of {refund:.2f} deducted before settlement",
        )
        return [txn], [credit], link

    def _not_settleable(self):
        status = self.rng.choice(["failed", "created"])
        txn = self._make_txn(scenario=Scenario.NOT_SETTLEABLE, status=status)
        link = TruthLink(
            link_id=self._next_link_id(),
            scenario=Scenario.NOT_SETTLEABLE,
            resolution=Resolution.NO_ACTION,
            txn_ids=[txn.txn_id],
            utrs=[],
            amount_basis=None,
            expected_inr_total=None,
            actual_inr_total=None,
            notes=f"status={status}; never settleable, must NOT be reported as an exception",
        )
        return [txn], [], link

    # ---------- orchestration ----------

    def _plan(self) -> list[tuple[Scenario, int]]:
        """Choose the sequence of cases and how many gateway txns each consumes."""
        cfg = self.cfg
        defects = list(cfg.defect_weights.keys())
        weights = [cfg.defect_weights[s] for s in defects]
        total_w = sum(weights)

        share = cfg.defect_weights.get(Scenario.MISSING_ON_GATEWAY, 0.0) / total_w
        max_unexplained = max(1, round(cfg.n_records * cfg.defect_rate * share))

        plan: list[tuple[Scenario, int]] = []
        consumed = 0
        unexplained = 0

        while consumed < cfg.n_records:
            if self.rng.random() >= cfg.defect_rate:
                plan.append((Scenario.CLEAN_1TO1, 1))
                consumed += 1
                continue

            scenario = self.rng.choices(defects, weights=weights)[0]

            if scenario is Scenario.MISSING_ON_GATEWAY:
                if unexplained >= max_unexplained:
                    continue
                unexplained += 1
                plan.append((scenario, 0))
                continue

            k = 1
            if scenario is Scenario.MANY_TO_ONE:
                lo, hi = cfg.many_to_one_batch_size
                k = self.rng.randint(lo, hi)
                if consumed + k > cfg.n_records:
                    # Not enough budget left for a meaningful batch.
                    plan.append((Scenario.CLEAN_1TO1, 1))
                    consumed += 1
                    continue

            plan.append((scenario, k))
            consumed += k

        return plan

    def _build_case(self, scenario: Scenario, k: int):
        cfg = self.cfg
        if scenario is Scenario.CLEAN_1TO1:
            return self._simple_settled(scenario)
        if scenario is Scenario.DATE_OFFSET:
            return self._simple_settled(scenario, lag=self.rng.choice(cfg.offset_lag_choices))
        if scenario is Scenario.FEE_DEDUCTION:
            basis = self.rng.choice([AmountBasis.GROSS, AmountBasis.GROSS_MINUS_FEE])
            return self._simple_settled(scenario, basis=basis)
        if scenario is Scenario.ROUNDING:
            paise = self.rng.randint(*cfg.rounding_paise)
            delta = Decimal(paise) / Decimal(100) * self.rng.choice([Decimal(1), Decimal(-1)])
            return self._simple_settled(scenario, rounding_delta=delta)
        if scenario is Scenario.FX_SETTLEMENT:
            return self._simple_settled(scenario, currency=self.rng.choice(list(FX_RATES)))
        if scenario is Scenario.GARBLED_NARRATION:
            return self._simple_settled(scenario, garbled=True)
        if scenario is Scenario.MANY_TO_ONE:
            return self._many_to_one(k)
        if scenario is Scenario.DUPLICATE_SETTLEMENT:
            return self._duplicate_settlement()
        if scenario is Scenario.MISSING_ON_BANK:
            return self._missing_on_bank()
        if scenario is Scenario.MISSING_ON_GATEWAY:
            return self._missing_on_gateway()
        if scenario is Scenario.PARTIAL_REFUND:
            return self._partial_refund()
        if scenario is Scenario.NOT_SETTLEABLE:
            return self._not_settleable()
        raise ValueError(f"unhandled scenario {scenario}")

    def generate(self) -> Dataset:
        gateway: list[GatewayTxn] = []
        bank: list[BankCredit] = []
        links: list[TruthLink] = []

        for scenario, k in self._plan():
            txns, credits, link = self._build_case(scenario, k)
            gateway.extend(txns)
            bank.extend(credits)
            links.append(link)

        pairs = [
            TruthPair(txn_id=t, utr=u, link_id=link.link_id, scenario=link.scenario)
            for link in links
            for t in link.txn_ids
            for u in link.utrs
        ]

        # Order must carry no information.
        self.rng.shuffle(gateway)
        self.rng.shuffle(bank)

        meta = {
            "seed": self.cfg.seed,
            "n_records_requested": self.cfg.n_records,
            "n_gateway_rows": len(gateway),
            "n_bank_rows": len(bank),
            "n_links": len(links),
            "n_truth_pairs": len(pairs),
            "defect_rate": self.cfg.defect_rate,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        return Dataset(gateway=gateway, bank=bank, links=links, pairs=pairs, config_meta=meta)


def generate(config: GenConfig | None = None) -> Dataset:
    return Generator(config).generate()
