# Reconciliation agent — dataset and benchmark design

Supersedes `2026-08-23-reconciliation-agent-design.md`, which was written before
two measurements invalidated its central premise.

Status: **authoritative**. The generator rewrite builds from this document.

---

## 1. The finding that forced the redesign

The previous design made identification the hard part: aggregate many payments
into one bank credit, damage the reference in the narration, and require the
matcher to work out which payments compose each credit.

Two measurements killed it.

**Measurement 1 — identification ambiguity collapses at n=2.** Counting, for each
bank credit, how many settlements within a ±2 day window share its amount under
any fee basis:

| payments per settlement | 500 rows, gross | 500 rows, net | 2000 rows, gross | 2000 rows, net |
|---|---|---|---|---|
| 1 | 25.2% | 16.2% | 43.8% | 33.2% |
| 2 | 0.0% | 0.0% | 1.9% | 0.5% |
| 3 | 0.0% | 0.0% | 0.3% | 0.0% |
| 4, 6, 8, 12 | 0.0% | 0.0% | 0.0% | 0.0% |

Ambiguity is produced by *repeated values*. 45% of amounts snap to a 33-entry
price-point catalogue, so ₹499 recurs and collides. Summing two payments is a
convolution of that spiky discrete distribution with itself: pair-sums span
~561 distinct values before mixing with the 55% continuous tail. By n=2 the
total is effectively continuous; by n=3 it is unique. Aggregation does not
reduce identification difficulty — it annihilates it.

**Measurement 2 — the free-search alternative is unsolvable, not hard.** Letting
the matcher search subsets of a whole day's payments gives 89.5% ambiguity. A
structured search bounded at K exclusions/inclusions trades one failure for the
other: at K=1, 17.5% ambiguous but the true answer is only expressible in 72.5%
of cases; at K=2, 33.3% ambiguous and 84.6% expressible. There is no K where the
search both finds the answer and finds only one.

**Conclusion.** There is no dial setting where identification is the interesting
problem. Either it is a dictionary lookup or it is information-theoretically
impossible. This is not an engineering shortfall to be worked around; it is a
property of how settlement amounts are distributed.

### The reframe

**Linkage is not reconciliation.** An exact identifier proves *which* transfer a
row refers to. It proves nothing about whether the amount is correct, whether
anything is missing, whether something was counted twice, whether a payment that
should have settled did, or whether a row was ever going to settle at all.

Reconciliation has three independent axes:

1. **Identification** — "which one is it?" Measured at 0.0%. Give it away.
2. **Explanation** — "the amount does not tie; why?"
3. **Disposition** — "is this row supposed to have a counterpart at all?"

The benchmark relocates all difficulty onto axes 2 and 3. Identifiers are
published in the clear and joins are expected to succeed. The agent earns its
score on control-total failures, lifecycle states, and duplicate detection.

### The panel answer

> "Your identification problem measures 0.0% ambiguity — what is your agent doing
> that a join isn't?"

A join tells you a bank credit corresponds to settlement `setl_00412`. It does
not tell you that the credit is ₹1,240 short because a refund from the previous
cycle was netted into it; that the settlement detail export duplicated four
lines, so the naive roll-up disagrees with the summary; that eleven captured
payments never entered any settlement; or that the ₹1,240 delta is equally
explained by two different unconsumed refund rows and therefore must not be
resolved at all. We publish the join's score ourselves as baseline B1, before
anyone runs it on us, and quote every number against it.

---

## 2. Artifacts

Six input files, two answer-key files, two required outputs.

### `batch_config.csv`
| column | type |
|---|---|
| `batch_id` | str |
| `seed` | int |
| `family` | `development` \| `primary` \| `stress` |
| `n_gateway_events` | int |
| `n_cases` | int |
| `generated_at` | ISO 8601 |

### `pricing_rules.csv`
| column | type | note |
|---|---|---|
| `method` | str | upi, card, netbanking, wallet, emi |
| `fee_rate_bps` | int | basis points of gross |
| `gst_rate_bps` | int | 1800 |

Static for the whole batch. No effective dates — see cut list.

### `gateway_ledger.csv` — event rows, not transaction rows
| column | type | note |
|---|---|---|
| `event_id` | str | primary key |
| `event_type` | `PAYMENT` \| `REFUND` | |
| `txn_id` | str | refunds carry the original payment's `txn_id` |
| `order_id` | str | |
| `amount_paise` | int | always positive; sign comes from `event_type` |
| `currency` | str | `INR` only |
| `status` | `CREATED` \| `FAILED` \| `CAPTURED` \| `PROCESSED` | |
| `created_at` | ISO 8601 | |
| `method` | str | |

The move from one row per transaction to one row per **event** is what makes
refunds representable. A partial refund is a `REFUND` event sharing the parent's
`txn_id`, not a `refund_amount` column bolted onto the payment row.

`status` carries disposition: `CREATED` and `FAILED` never settle; `CAPTURED`
should settle and has not yet; `PROCESSED` has settled.

### `settlement_detail.csv`
| column | type | note |
|---|---|---|
| `detail_id` | str | primary key; **may repeat** — that is scenario `DUPLICATE_DETAIL_EXPORT` |
| `settlement_id` | str | |
| `event_id` | str \| null | null for adjustment lines |
| `line_type` | `PAYMENT` \| `REFUND` | |
| `gross_effect_paise` | int | **signed**; refunds negative |
| `fee_paise` | int | |
| `tax_paise` | int | |
| `net_effect_paise` | int | |
| `settled_at` | ISO 8601 | |
| `currency` | str | |
| `reference_text` | str \| null | |

### `settlement_summary.csv`
| column | type |
|---|---|
| `settlement_id` | str (primary key) |
| `utr` | str |
| `settlement_date` | date |
| `gross_payment_paise` | int |
| `refund_paise` | int |
| `fee_paise` | int |
| `tax_paise` | int |
| `net_amount_paise` | int |
| `line_count` | int |
| `currency` | str |
| `status` | str |

### `bank_statement.csv`
| column | type |
|---|---|
| `bank_row_id` | str (primary key) |
| `utr` | str |
| `posted_at` | ISO 8601 |
| `credit_amount_paise` | int |
| `currency` | str |
| `narration` | str |
| `bank_ref` | str |

Narration is `NEFT CR: {bank} {utr} RAZORPAY SETTLEMENT` — the real format. It
carries no per-order reference, because real settlement narrations do not. The
`utr` column is published in the clear.

### Control equations

These are what the agent verifies. They must hold exactly except where a
scenario deliberately breaks one.

```
per detail line:   net_effect_paise = gross_effect_paise - fee_paise - tax_paise
per summary:       net_amount_paise = gross_payment_paise - refund_paise
                                      - fee_paise - tax_paise
roll-up:           summary totals == sum over UNIQUE detail_id lines
bank tie-out:      credit_amount_paise == net_amount_paise
fee:               fee_paise = round_half_up(amount_paise * fee_rate_bps / 10000)
tax:               tax_paise = round_half_up(fee_paise  * gst_rate_bps / 10000)
```

`round_half_up` on integers, one implementation, shared by generator and matcher.

### `answer_key_cases.csv`
| column | type |
|---|---|
| `case_id` | str |
| `scenario` | str (see §3) |
| `expected_outcome` | `RECONCILED` \| `EXCEPTION` \| `NO_ACTION` \| `ABSTAIN` |
| `settlement_ids` | pipe-joined |
| `bank_row_ids` | pipe-joined |
| `event_ids` | pipe-joined |
| `expected_exception_category` | str \| null |
| `notes` | str |

### `answer_key_allocations.csv`
Atomic `(event_id, settlement_id, bank_row_id)` correspondences, for
precision/recall with honest partial credit — 7 of 8 legs scores 7/8, not 0.
Direct successor to `answer_key_pairs.csv`; same purpose, richer key.

### Required agent outputs

`reconciliation_decisions.csv` — one row per case with outcome, category,
confidence, and the evidence that produced it.
`audit_log.jsonl` — append-only; every decision from every stage, with the rule
or tool applied, and an `overridable` flag.

---

## 3. Scenario table

Shares are **by case, never by row**. A case is 3–7 gateway events; a 500-event
batch is ~100 cases.

| scenario | what it is | dev % | primary % | stress % | expected outcome |
|---|---|---|---|---|---|
| `STRAIGHT_THROUGH` | everything ties | 30 | 56 | 25 | RECONCILED |
| `REFUND_LATER_CYCLE` | refund netted into a later settlement than its payment | 10 | 10 | 12 | RECONCILED |
| `DUPLICATE_DETAIL_EXPORT` | detail rows repeat a `detail_id`; naive roll-up disagrees with summary | 10 | 8 | 12 | RECONCILED, warning |
| `CORROBORATED_REFUND` | delta explained by exactly one unconsumed refund row | 12 | 8 | 12 | RECONCILED |
| `CAPTURED_UNSETTLED` | `CAPTURED` events in no settlement | 7 | 6 | 7 | EXCEPTION |
| `NOT_SETTLEABLE` | `CREATED`/`FAILED` events — the trap | 6 | 6 | 5 | **NO_ACTION** |
| `FEE_TAX_VARIANCE` | fee or GST disagrees with `pricing_rules.csv` | 7 | 6 | 8 | EXCEPTION |
| `BANK_CREDIT_MISSING` | settlement exists, no bank credit | 6 | — | 7 | EXCEPTION |
| `BANK_CREDIT_DUPLICATE` | same UTR credited twice | 6 | — | 6 | EXCEPTION |
| `AMBIGUOUS_REFUND` | delta explained equally by ≥2 unconsumed refund rows | 6 | — | 6 | **ABSTAIN** |
| `DESCRIBED_REFUND` | same tie, but the settlement note names one candidate's product category | 10 | 6 | 10 | RECONCILED |

**Development carries all ten scenarios, and must.** The abstention threshold is
frozen by choosing the lowest value producing zero falsely-accepted recovery
allocations across the development seeds. If `AMBIGUOUS_REFUND` were absent from
development — as it is from primary — no threshold could ever produce a false
accept, the rule would select zero, the agent would never abstain, and it would
false-match every ambiguous case it met in the stress batches. A tuning set has
to contain the phenomenon being tuned for. Development shares also differ
deliberately from stress shares, so the frozen threshold is not fitted to the
prevalence of the batch it is later scored on. Development prevalence is never
reported.

**Why three scenarios are stress-only.** At 500 events a 1% share is one case. A
per-class precision computed over one case is not a measurement, and publishing
it as one is the same error as demoing on hand-picked rows. Anything the primary
batch cannot measure moves to the pooled stress batches, where 10 seeds put
60–70 cases in each class.

`NOT_SETTLEABLE` is a trap, not an exception. An agent that pads its exception
list with `CREATED`/`FAILED` events is generating false positives, and the
harness measures that rate by name.

---

## 4. Three dataset families

| family | batches | purpose | may tune on it |
|---|---|---|---|
| Development | 5 × 500 events, distinct seeds | freeze thresholds | **yes, only here** |
| Primary | 1 × 500 events | headline result, throughput, demo | no |
| Stress | 10 × 500 events, exception-enriched | per-class capability | no |

**Two tables, never blended.** Primary reports realistic prevalence, wall-clock
throughput, and the honest exception list. Stress reports per-class precision and
recall. Averaging them would launder enriched prevalence into the headline.

**Threshold freezing.** Choose the lowest abstention threshold that produces zero
falsely-accepted recovery allocations across all five development seeds; ties go
to the higher threshold. Stress prevalence must never influence the choice.

---

## 5. The difficulty floor

The rule is not "make it hard" — it is that the floor must be **measured** and the
score quoted against it. "We injected 15 hard cases and got 12" is a claim about
the generator's authoring choices, not a property of the data.

The previous design used subset-sum ambiguity, which satisfied that rule
beautifully — a well-defined combinatorial quantity a hostile panel member could
recompute — but which measured 0% or 89% and never anything useful.

**No taxonomy-independent scalar exists for classification difficulty.** For a
matching problem, "number of feasible hypotheses" is well defined: the hypothesis
space is subsets of a candidate pool. For a classification problem the hypothesis
space *is the set of categories the generator chose to define*. Count 6 and the
certificate says "2 of 6"; define 12 and it says something else. The number moves
when the taxonomy moves, so it cannot be the floor. State this plainly rather
than ship a fake one.

### What replaces it: a published baseline

```
D = 1 − (cases B1 resolves correctly ÷ total cases)
```

**B1 is published as runnable code** in `src/recon/metrics/baselines.py`. A panel
member can read it, run it, and reproduce D from the released CSVs without
trusting the generator. That is the property the subset-sum certificate had and
an assertion does not.

**B1 specification.** Exact `event_id` / `settlement_id` / UTR joins. Respects
signed line amounts. Validates fee and tax against `pricing_rules.csv`. Checks
all three control equations. **Deduplicates `settlement_detail` on `detail_id`
before rolling up** — deduplication on a primary key is one line of code, and a
baseline that abstains there would gift the agent 8% of its headline. B1 never
reads narration, never attempts refund-delta recovery, and never abstains.

**Measured values** (`python -m recon.metrics.baselines <dir>`, seed 42 primary,
seed 101 stress):

Measured by `make report` on the committed datasets (500 records each). D is
quoted against **B2**, not B1: B1 is forbidden from refund recovery by rule, and
a floor built on a restriction rather than on the data would be the easy number
to beat rather than the honest one.

| family | B1 | B2 | B3 | **D (vs B2)** | B2 false-match rate |
|---|---|---|---|---|---|
| primary | 76 / 100 | 89 / 100 | 90 / 100 | **11.0%** | 3.68% |
| stress | 60 / 100 | 85 / 100 | 82 / 100 | **15.0%** | 4.61% |
| development | 58 / 100 | 80 / 100 | 79 / 100 | **20.0%** | — |

B2's entire deficit sits on three scenarios: `CONTESTED_REFUND` (6/8 primary,
10/14 stress), `AMBIGUOUS_REFUND` (0/4, 0/6) and `DESCRIBED_REFUND` (1/6, 5/10).
It handles every other class, including `BANK_CREDIT_DUPLICATE`, correctly. Note
the last column: B2 buys its extra cases over B1 by guessing, and pays for them
with sixteen false attributions on the primary batch.

**B3 is the string-matching objection, shipped as code.** Once settlement lines
carry an operations note and payments carry a product description, the first
thing a sceptic says is that fuzzy string matching would have been enough. B3 is
B2 with its arbitrary tie-break replaced by the strongest cheap lexical ranking
— content-token Jaccard or character sequence ratio, whichever is higher, over
the note against each candidate's parent product description. It shares its
tokeniser with the generator's leak invariant so it cannot be quietly weaker
than the property it exists to falsify.

B3's case count is deliberately *not* the measurement. A different tie-break
consumes different events and changes what later lines can claim, so B3 lands a
few cases either side of B2 for reasons that have nothing to do with ranking —
on one seed it came out four cases ahead, which measured the knock-on effect.
The measurement is per decision: over every multi-candidate refund line with a
knowable answer, how often the top-ranked candidate is the right one, against
the sum of 1/k expected from a k-sided coin.

| family | decidable lines | lexical hits | expected by chance | lift |
|---|---:|---:|---:|---:|
| primary | 17 | 8 | 8.5 | −0.029 |
| stress | 30 | 14 | 14.1 | −0.003 |
| development | 31 | 13 | 13.9 | −0.030 |

Maximum content-token overlap between a note and any candidate description is
**0** on all three families. String similarity is not doing poorly here; it has
nothing to rank on, and the lift is negative on every family.

**Two consequences, both uncomfortable, both stated rather than hidden.**

First, D is small — 11% on the primary batch. Inflating the scenario mix to
manufacture a larger gap would be exactly the dishonesty this benchmark exists
to avoid. The primary batch demonstrates throughput and prevalence realism; the
stress batches demonstrate capability. Both tables are load-bearing.

Second, and more serious: **the entire measured gap now lives on one axis** --
attributing an anonymous refund line, and knowing when attribution is not
provable. Every other capability in the agent is matched by a competent SQL
script. This is a real concentration risk. The defence is that this axis is
where reconciliation actually is hard, and that the two halves of it are
genuinely different skills: `CORROBORATED_REFUND` rewards proving a unique
allocation, `AMBIGUOUS_REFUND` rewards refusing to allocate at all. An agent
that solves the first by guessing scores zero on the second.

**Third, and the reason `DESCRIBED_REFUND` was added after the fact.** With two
deterministic rungs the agent closed all three families at 100/100. A saturated
benchmark measures nothing further, and a headline of 100% invites exactly one
question: what did you leave out? So the benchmark gained a residual the
deterministic engine provably cannot close — and the bar on that residual is
narrow:

> It must be unresolvable by arithmetic, unresolvable by string similarity, and
> resolvable by reading.

The first clause keeps rule one of this project intact: anything an arithmetic
rule can close belongs in the deterministic core, and the model never does
reconciliation arithmetic. The second clause is what stops the class being a
regex benchmark in a costume — the exact failure that forced the original
redesign, when a fifteen-line reference-token regex scored 81.5%. B3 is the
published evidence for it. The third clause is what gives the adjudicator rung
something real to convert.

The measured result is that the deterministic ladder now scores 0/6 primary,
0/10 stress and 0/10 development on `DESCRIBED_REFUND` — by abstaining, at a
0.00% false-match rate — while holding 100% on every other refund class. A repo
where the model rung is dead code is a worse panel answer than one where it
converts the cases the gates provably cannot.

---

## 6. The corroboration rule

Abstention is the default when several explanations fit. One exception is
permitted, and its boundary is exact.

A missing-reference or unexplained-delta case resolves **only** when exactly one
global allocation survives every gate below. If two or more survive, the outcome
is ABSTAIN and resolving either is scored as a false match.

1. The candidate record exists and is unconsumed by any other allocation.
2. Its amount reproduces the observed delta exactly, in integer paise.
3. Its date lies within the permitted settlement window.
4. Its currency matches.
5. Its `txn_id` lineage is consistent (a refund's parent payment exists and settled).
6. Sign is consistent with `line_type`.
7. Accepting it leaves every control equation satisfied.
8. Accepting it does not force another case into infeasibility (global, not local).
9. No second candidate satisfies gates 1–8.

**A tie may never be broken by frequency, by prior, by batch history, or by the
model's judgement of plausibility.** "This merchant has 40 refunds and 0
chargebacks" is a prior and is inadmissible. "There exists a concrete unconsumed
row whose amount and date reproduce this delta" is a record and is admissible.
The existence of a matching record is *necessary but not sufficient* — gate 9 is
what makes it a proof rather than a guess.

The LLM may propose which record to test. Only the deterministic engine may
decide whether it passes. This makes "no LLM arithmetic" structural rather than
aspirational.

---

## 7. Cut list — explicitly not built

UTR corruption and generic fuzzy UTR matching · split and merge settlements ·
transfer-allocation table · multiple amount bases · per-credit basis inference ·
fee-plan effective dates · fee caps and minimums · chargebacks and reversals ·
generic adjustment lines · FX and multi-currency · OCR · holiday calendars ·
unrelated bank credits · the generic fuzzy matcher module · LLM arithmetic or
final adjudication · a broad exception taxonomy · any claim of production-realistic
prevalence.

**Consequence for existing code.** `src/recon/match/fuzzy.py` (232 lines, 10
passing tests) becomes near-dead: with UTRs published in the clear there is
nothing to fuzzy-match. `prefix_candidates` and `recover_reference` are salvaged
for the corroborated-refund path; the rest is deleted rather than left as
decoration.

`tools/ambiguity.py` must be **replaced, not left alone**. Its `measure()`
filters `n_credits == "1" and n_txns == "1"`, which after the schema change
matches almost nothing. It will not error — it will silently report a meaningless
number over a tiny sample, and that number would reach the README.

---

## 8. Metrics

Reported separately for primary and stress, never averaged:

Reconciliation rate quoted against D · precision · recall · **false-match rate,
explicitly** · abstention rate and abstention precision · exception breakdown by
category · `NOT_SETTLEABLE` false-positive rate · throughput in cases/sec and
wall clock · LLM call rate as a percentage of cases · **cost per 500 records**.

Per-class numbers come from the 10 pooled stress seeds. The primary batch
supplies the headline, throughput, and the exception list.

---

## 9. Stated limitation

The held-out set is a different seed from the same generator. It measures whether
tolerances were overfitted. It does **not** measure robustness to real bank data,
because the same code wrote both the defects and their labels. This is stated in
the README before anyone asks; claiming more is the trap.
