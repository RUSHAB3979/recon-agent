# Reconciliation agent — architecture design

**Date:** 2026-08-23
**Status:** approved for implementation
**Supersedes:** the six-stage pipeline sketched in `CLAUDE.md`

---

## 1. Goal

Build an agent that reconciles a payment gateway ledger against a bank
settlement statement, auto-matches what it can, and produces a categorised,
explained list of what it cannot. Success is measured against a ground-truth
answer key, not asserted.

Submission target: Razorpay AI Buildathon, AI Finance Controller track,
deadline 5 September 2026. The stated grading bar is *"throughput plus measured
accuracy plus an honest exception list."*

---

## 2. Findings that forced the redesign

Two measurements taken on `data/dev` (seed 42; 500 gateway rows, 445 bank rows,
458 links, 487 truth pairs) invalidated parts of the original plan.

### 2.1 A fifteen-line regex scores 81.5%

Extracting alphanumeric tokens of six or more characters from each bank
narration and looking them up in a dictionary of gateway `order_id`s — with no
amount check, no date check, no fuzzy logic and no model — yields:

```
correct = 397    wrong = 0    truth pairs = 487
recall  = 81.5%  precision = 100.0%
```

The residual after that baseline is startlingly concentrated:

| Scenario | Pairs remaining | Pairs total |
|---|---:|---:|
| `many_to_one` | 74 | 74 |
| `garbled_narration` | 16 | 16 |
| everything else | 0 | 397 |

`date_offset`, `fee_deduction`, `rounding`, `partial_refund` and
`fx_settlement` are fully solved by reference lookup, because those defects
perturb the amount or the date and reference lookup reads neither.

### 2.2 The narration format is not realistic

The generator embeds the gateway `order_id` in the bank narration:

```
gateway:  order_fOzuNFOlWymq2T
bank:     NEFT CR-YESB0000262-RAZORPAY SOFTWARE PVT LTD-FOZUNFOLWYMQ2T
```

Razorpay's documented settlement narration carries the **UTR issued by the
correspondent bank** and the words `RAZORPAY SETTLEMENT` — not a
per-transaction reference. Reconciliation in production is a three-way match of
Order ID (gateway) to Settlement ID (settlement report) to UTR (bank), and
settlements are **aggregated**: one bank credit covers a whole cycle of
payments, netted of MDR, GST and refunds. The standard cycle is T+2, not the
T+1 the generator uses as its happy path.

The two findings are the same fact. The benchmark's difficulty currently rests
on a field that does not exist in the data it claims to model, and the
interviewing panel works at the company whose format it is.

### 2.3 The hard residual is uniquely solvable

For each batch settlement, taking the payments not already claimed by a
reference match, filtering to the settlement date window, and searching for a
subset summing to the credit gives:

```
UNIQUE = 10    MULTIPLE = 0    NONE = 0    (4 candidate pools exceeded the brute-force cap)
```

No ambiguous cases. Where a subset closes, it is the only subset that closes.
Batch recovery is therefore an *engineering* problem — search efficiently — and
not an *information* problem. The candidate pools were small enough to search
only because reference resolution ran first and removed the claimed payments.

---

## 3. Decisions

### D1 — Model settlement cycles, not per-transaction settlements

**Decision.** Batch settlement becomes the default case. Bank narrations for
settlements carry UTR plus `RAZORPAY SETTLEMENT` and no per-order reference.
Per-order references survive only on flows that genuinely carry them (instant
settlements, refunds, chargebacks). The happy-path lag moves from T+1 to T+2.

**Why.** Realism and difficulty point the same way for once: the change fixes
the format flaw *and* collapses the trivial baseline, from one edit. Measured
effect of raising batch weight on the current generator:

| Configuration | Regex baseline |
|---|---:|
| current | 81.5% |
| defect 0.55, batches up | 51.9% |
| defect 0.75, batches up | 29.5% |
| plus batches of 2–15 | 23.7% |

Precision stayed at 100% throughout: a present reference is never wrong.

**Rejected — keep the dataset and report the baseline honestly.** Cheapest, and
the honesty would be real, but it leaves a realism flaw that this specific
audience is uniquely equipped to find.

**Rejected — add the settlement report as a third source.** Most faithful to the
documented process and the richest defect surface, but the largest build, and it
makes the easy path a trivial join on `settlement_id`. With thirteen days it
spends the schedule on data rather than on the agent.

### D2 — Keep `instant_settlement` as a minority class that does carry a reference

Reference resolution stays a live pipeline stage rather than dead code, the
dataset keeps a difficulty gradient, and it matches the documented behaviour
that instant settlements "appear as multiple entries" while aggregated ones
appear as a single credit.

### D3 — Search for the excluded payments, not the included ones

A cycle is nearly the whole window; one or two payments are deferred. Finding
which twelve of thirteen are included costs 2^13 subset tests. Finding which one
is excluded costs 13.

```
gap = Σ(candidates under basis) − credit_amount
find the minimal subset summing to gap
```

Complements of size 1, 2 and 3 cost O(n), O(n²) and O(n³) with hashing. The
assumption is that cycles are nearly complete; when it fails, fall back to
meet-in-the-middle or a DP over paise, and escalate if that also fails.

### D4 — Resolve the amount basis per credit, not per payment

Three settlement conventions exist (`net`, `gross`, `gross_minus_fee`). Basis is
a property of the settlement, not of the payment, so try each basis once per
credit and keep the one that closes: three attempts instead of 3^n.

### D5 — Iterate the matcher to a fixpoint

Resolve every currently unambiguous credit, then re-run. Each claim shrinks
other candidate pools and can make a previously ambiguous credit resolvable.
Repeat until a pass changes nothing. Reference resolution is not merely a fast
path — it is a constraint propagator that makes the combinatorial stage
tractable.

### D6 — The adjudicator is a tool-using agent with no arithmetic of its own

The model receives a case packet and a set of deterministic tools:

```python
get_candidates(date_window, amount_range) -> shortlist
verify_sum(txn_ids, target)               -> bool      # exact, Decimal
lookup_reference(prefix)                  -> matches
check_basis(txn_id, credit_amount)        -> basis | None
```

It must call `verify_sum` before asserting any match. It cannot compute a sum,
only ask whether one holds. This makes the "no LLM arithmetic" rule structural
rather than aspirational, and it answers the "is this actually an agent?"
question without weakening the rule.

### D7 — Give the model the linguistic work, not the matching

With subset-sums coming out unique, the adjudicator's matching role is small,
which is the correct engineering outcome. The genuinely linguistic work is in
the exception report:

| The model does | The model never does |
|---|---|
| Classify unexplained credits from free-text narration | Compute or compare amounts |
| Recover garbled references | Assert a match without tool verification |
| Break genuine ties on non-numeric evidence | Override an S1–S3 result |
| Write the operator-facing explanation and recommended action | Touch anything already resolved |

### D8 — Arithmetic gates every text-derived match

A fuzzy or model-proposed reference match is accepted only if the amount also
closes under some basis and the date falls in the window. Text proposes;
arithmetic disposes. This is the primary defence against false matches.

### D9 — Report precision as a curve

Abstention is a tunable threshold, so publish the trade-off across thresholds
(coverage, precision, false-match rate) rather than a single operating point.

### D10 — Money is `Decimal` at the boundaries and integer paise in the solver

`Decimal` quantised to 2dp for parsing, storage and reporting. Integer paise
inside the subset solver, where exact integer arithmetic is both faster and
free of comparison subtleties, and where a DP over amounts becomes possible.
Floats are never used for currency anywhere.

---

## 4. Dataset design

Shape moves from roughly 500 gateway / 445 bank rows to 500 gateway / 60–80
bank rows. That is not a loss of data; it is the production shape — one credit,
many payments, no per-transaction reference.

**Retained:** `Decimal` money, the dual answer-key views (pairs for
precision/recall partial credit, links for classification), seed determinism,
row shuffling, the `not_settleable` trap class, and the four-seed test
parameterisation.

**Changed:** narration templates, T+1 to T+2, and `_plan()` becomes cycle-based
rather than case-based.

**Retired:** `make ambiguity` in its current form. It measures collisions on
amount plus date, which is no longer the binding channel.

### Scenario table

| Scenario | Effect | Expected resolution |
|---|---|---|
| `clean_cycle` | settles in its normal T+2 cycle | `auto_match` |
| `deferred` | held to the next cycle; breaks the clean sum | `auto_match` |
| `instant_settlement` | settled individually; narration carries a reference | `auto_match` |
| `fee_basis` | cycle credited gross or gross-minus-fee | `auto_match` |
| `partial_refund` | refund netted inside the cycle | `auto_match` |
| `fx_settlement` | foreign currency, separate per-currency cycle | `auto_match` |
| `rounding` | paise delta on the cycle total | `auto_match` |
| `garbled_narration` | applies to instant settlements | `auto_match` |
| `duplicate_settlement` | cycle credited twice | `exception:duplicate_settlement` |
| `missing_on_bank` | cycle never credited | `exception:unsettled` |
| `missing_on_gateway` | chargeback, other merchant, interest, cash | `exception:unexplained_credit` |
| `not_settleable` | `failed` / `created` | `no_action` — must not be reported |

### Replacement difficulty metric

**Cycle ambiguity:** for each settlement credit, how many distinct subsets of
the candidate window sum to it? Report the fraction with more than one. That is
the information-theoretic floor of the new dataset and the figure to quote
beside the match rate.

---

## 5. Pipeline

Six stages. Each strictly shrinks the input to the next; cost rises
monotonically down the stack, so anything resolved early never reaches the
expensive path.

### S0 — Normalizer

Parses both files into canonical records. Money to `Decimal` 2dp then integer
paise. Dates parsed; each credit gets a candidate capture window. Settleability
is decided once, here: `failed` and `created` are marked not settleable and
never enter a candidate pool, which is how the trap class is handled
structurally rather than by a later filter. Each payment exposes its value under
all three bases, with `refund_amount` already folded in, so partial refunds stop
being a special case and become ordinary arithmetic. Narrations are uppercased
and tokenised into alphanumeric runs of six or more characters.

### S1 — Reference resolution

Three indices over the gateway side: exact `order_id`, exact `txn_id`, and a
six-character prefix map used later by S3.

A reference hit alone is not a match. The rule is: reference matches **and** the
amount closes under some basis **and** the date sits in the window. Requiring
arithmetic confirmation on top of an exact reference hit catches two exception
classes for free — a reference that matches while the amount does not close is a
short or over settlement, and a payment referenced by two credits is a duplicate
settlement.

### S2 — Cycle reconstruction

The core stage, carrying roughly 80% of the match rate.

```
1. Window       payments captured in [credit_date − max_lag, credit_date − min_lag]
2. Filter       settleable, unclaimed, matching currency group
3. Per basis    total = Σ settle_value(basis)   in integer paise
                gap   = total − credit_amount
4. gap == 0     the whole window is the cycle
   gap  > 0     find the minimal subset summing to gap — the excluded payments
   gap  < 0     credit exceeds the window; widen once, else flag over-settlement
5. Uniqueness   two distinct minimal subsets both closing → ambiguous → S4
6. Claim        remove those payments and this credit from all later pools
```

FX cycles group by currency first; the rate is unknown, so derive
`implied_rate = credit ÷ Σ(foreign settle values)` and accept only if it lands
within a plausible band for that currency. An implausible rate means the wrong
candidate set.

Rounding tolerance is applied at closure, never during search. Search on exact
integers; if nothing closes, retry allowing `|Σ − credit| ≤ tolerance` and record
the delta in the audit trail. Tolerance is tuned on dev and reported.

### S3 — Fuzzy reference recovery

Fires only when a credit carries something that looks like a reference but does
not resolve. Confusable expansion (`0↔O 1↔I 5↔S 8↔B 2↔Z 6↔G`, bounded
substitutions), prefix lookup on six or more surviving characters, and edit
distance of at most two restricted to the prefix bucket. Then the arithmetic
gate from D8. One survivor resolves; none becomes an exception; two or more
escalate to S4.

### S4 — LLM adjudicator

Receives only what S1–S3 could not resolve, as a case packet: the credit, a
candidate shortlist with attributes, and a note on what earlier stages tried and
why they failed. Tools per D6. Structured output:

```json
{ "decision": "match" | "abstain",
  "txn_ids": ["..."], "confidence": 0.0,
  "reasoning": "...", "evidence": ["..."] }
```

Abstention is always available and never penalised. Confidence below the
threshold is treated as abstention.

### S5 — Exception classifier

Every unclaimed record receives a named category, evidence, confidence and a
recommended action.

| Category | Meaning | Action |
|---|---|---|
| `unsettled` | settleable payment, never credited | chase gateway |
| `unexplained_credit` | credit with no gateway source | subtyped below |
| `unexplained_credit/chargeback_reversal` | | route to disputes |
| `unexplained_credit/other_merchant` | | wrong-account credit, return |
| `unexplained_credit/bank_interest`, `/cash_deposit` | | not reconciliation, treasury |
| `duplicate_settlement` | payment credited twice | recover overpayment |
| `short_settlement`, `over_settlement` | matched, amount off beyond tolerance | investigate delta |
| `no_action` | `failed` / `created` | never appears in the exception list |

### S6 — Audit trail and override

Every decision from every stage logs `decision_id`, stage, inputs, rule or tool
applied, result, confidence, timestamp, and an overridable flag. Overrides apply
as a layer on top and the metrics re-run with them.

---

## 6. Metrics

Three systems are scored, so each layer's contribution is visible:

| System | What it establishes |
|---|---|
| Naive regex | how much is trivially solvable — stated first, by us |
| Deterministic core (S1–S3) | what careful engineering buys |
| Full system (S1–S4) | what the model adds, and what it costs |

Reported figures: match rate, precision and recall on **both** answer-key views;
**false-match rate, explicitly**; per-scenario breakdown; `not_settleable`
false-positive rate; exception precision and recall per category; throughput in
records per second and wall clock; LLM call rate as a percentage of records and
cost per 500 records; and the precision/coverage curve across abstention
thresholds.

Holdout runs **once**, at the end. All tuning happens on dev.

---

## 7. Build order

```
Aug 23–25   dataset rework (section 4) + revalidate the answer-key suite
Aug 26      S0 normalizer + S1 reference resolution
Aug 27      metrics harness + all three baselines
Aug 28–30   S2 cycle reconstruction
Aug 31      S5 exception classifier
Sep 1       S3 fuzzy recovery
Sep 2       S4 adjudicator + tools
Sep 3       S6 audit trail + override; holdout run (once)
Sep 4       demo UI, README, make stats
Sep 5       pitch video, submit
```

The metrics harness moves from position eight to day three because S2 cannot be
tuned without it and S2 carries most of the match rate.

Everything through 31 August constitutes a complete and defensible submission:
deterministic core, real numbers, honest exception list, three baselines. S3, S4
and the demo UI raise the score without being load-bearing. That is the line to
protect if the schedule slips.

---

## 8. Risks

**The dataset rework invalidates parts of the answer-key suite.** The link and
pair structure is unchanged — a cycle is a `many_to_one` link with more legs —
so most of the ninety-one checks survive, but the per-scenario invariants need
rewriting alongside the new scenario table. Budgeted at three days; if it runs
long, the fallback is D1-rejected option one, keeping the current dataset and
reporting the regex baseline honestly.

**Cycle reconstruction may prove ambiguous at higher batch weights.** Measured
ambiguity is zero on the current configuration but was not re-measured after the
weights move. The replacement difficulty metric in section 4 must be computed
before tuning begins; if ambiguity is materially above zero, batch sizes come
down.

**Thirteen days is tight with no slack.** The build order is sequenced so that
the submission is viable from 31 August onward.

---

## 9. Honest limitation

The held-out set is a different seed from the same generator. It measures
whether the tolerances were overfitted. It does **not** measure robustness to
real bank data, because the same code wrote both the defects and their labels.
This is stated before anyone asks; claiming more is the trap.
