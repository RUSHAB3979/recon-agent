# Multi-source Reconciliation Agent

[![CI](https://github.com/RUSHAB3979/recon-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RUSHAB3979/recon-agent/actions/workflows/ci.yml)

Reconciles payment records across a gateway ledger, a settlement report and a
bank statement — resolves what it can prove, declines what it cannot, and
produces an explained, categorised list of the remainder.

**Razorpay AI Buildathon — AI Finance Controller track.**

> **Status.** The benchmark, the published baselines and the deterministic
> engine are complete and measured. On the held-out primary batch the agent
> resolves **94 of 100 cases at a 0.00% false-match rate** and 1.0000 allocation
> precision, against a floor where the strongest arithmetic baseline reaches 89
> and pays 16 false attributions for it.
>
> The six cases it does not resolve are **declined on purpose**. The
> evidence-reading rung that acts on them is now built and tested
> (`src/recon/match/adjudicator.py`); it is **off by default and excluded from
> every number above**, because the published figures must not depend on a
> network call. The exception classifier is still outstanding.
>
> Everything claimed below is produced by a command in this repo and re-run by
> CI on every push.

## Results

Every number here comes out of `make report`, which scores the agent and both
baselines through **the same scorer**. A benchmark that measured the floor with
one instrument and the agent with another would be measuring the instruments.

| | dev | primary | stress |
|---|---|---|---|
| B1 — exact joins only | 58 / 100 | 76 / 100 | 60 / 100 |
| B2 — B1 + amount lookup | 80 / 100 | 89 / 100 | 85 / 100 |
| B3 — B2 + fuzzy string matching | 79 / 100 | 90 / 100 | 82 / 100 |
| **agent (deterministic only)** | **90 / 100** | **94 / 100** | **90 / 100** |
| agent false-match rate | **0.00%** | **0.00%** | **0.00%** |
| agent allocation precision | 1.0000 | 1.0000 | 1.0000 |
| B2 false attributions | 26 | 16 | 20 |

The two rows that matter are the last two. **B2 buys its cases by guessing** and
books 16 wrong attributions on the primary batch to do it; the agent buys none
that way. In a finance-ops loop a wrong match is worse than no match, because it
becomes an invisible `RECONCILED` line that nobody ever looks at again, whereas
an abstention becomes a named item on somebody's desk.

Per-scenario, the deterministic engine closes `CORROBORATED_REFUND`,
`CONTESTED_REFUND`, `REFUND_LATER_CYCLE` and `AMBIGUOUS_REFUND` at 100%. It
scores **0/6 on `DESCRIBED_REFUND` — by abstaining on all six.** That is the
designed residual, and the section below explains why it is there.

### The holdout

Pre-registered in `tools/holdout.py` and committed *before* the run, so the
commit order is the evidence rather than the assurance. Five contiguous seeds
(70001–70005) that nothing in this repository had ever generated, scored or
looked at, on the realistic-prevalence mix, against the frozen ladder, run once:

| | held-out result |
|---|---|
| outcome accuracy | **94.0%** on all five seeds (mean, min and max) |
| false-match rate | **0.00%** on all five |
| allocation precision | **1.0000** on all five |
| difficulty floor D vs B2 | 8.0% – 13.0% |
| headroom over B2 | **+2 to +7 cases** |

Identical to the published primary figure, so nothing was fitted to the batches
in `data/`. **But the constancy is structural, not stability**, and saying so is
the point: `PRIMARY_CASE_SHARES` is a fixed partition of 100 cases, so every seed
draws exactly 6 `DESCRIBED_REFUND` cases and the agent abstains on all of them.
94 is 100 − 6 by construction, on any seed. A holdout returning the same number
five times measures less than it appears to.

What genuinely moved is the more interesting half. **D moved five points and the
agent did not.** Across five unseen batches all of the seed-to-seed variance sat
in the guesser and none in the prover — which is the difference this whole
project is about, stated in one measurement. Full record, including what it does
not show: [`docs/HOLDOUT.md`](docs/HOLDOUT.md).

## Linkage is not reconciliation

The obvious way to build this benchmark is to make identification hard: damage
the reference in the bank narration and force the matcher to work out which
payments compose each credit. That was the original design, and two
measurements killed it.

Identification ambiguity **collapses at n=2**. Ambiguity comes from repeated
values, and 45% of amounts here snap to a price-point catalogue, so ₹499 recurs
and collides. But summing two such payments is a convolution of that spiky
discrete distribution with itself — the pair-sums spread across hundreds of
distinct values, and by n=3 every total is unique. Measured: 25.2% ambiguity at
one payment per settlement, **0.0% at two**. Aggregation does not make
identification harder; it annihilates it. Letting the matcher search freely over
a whole day instead gives 89.5% ambiguity — unsolvable rather than hard. There
is no dial setting in between.

So identifiers here are **published in the clear** and joins are expected to
succeed. That is not a concession, it is the point:

> An exact identifier proves *which* transfer a row refers to. It proves nothing
> about whether the amount is correct, whether anything is missing, whether
> something was counted twice, whether a payment that should have settled did,
> or whether a row was ever going to settle at all.

The difficulty lives on the two axes that survive: **explanation** ("the amount
does not tie — why?") and **disposition** ("is this row supposed to have a
counterpart at all?").

## Quick start

```bash
make data          # generate all three families (development, primary, stress)
make agent         # run the pipeline, print per-pass yield and per-gate eliminations
make report        # score the agent and both baselines through one scorer
make verify        # tests + baselines + report + ambiguity + audit chain + stats
make audit         # write runs/<family>/audit.jsonl -- every sealed decision
make exceptions    # write runs/<family>/exceptions.csv -- the operator queue
make demo          # write runs/demo/index.html -- the whole run, as one page
make holdout       # run the frozen agent on never-seen seeds (see docs/HOLDOUT.md)
```

The evidence-reading rung is opt-in and never contributes to a published
number:

```bash
python -m recon.match.controller data/dev --adjudicate
```

With no `ANTHROPIC_API_KEY` set it selects the declining reader, so the command
still runs — and still produces the deterministic result.

## Architecture

```
gateway ledger ────┐
settlement detail  ├──> 1. Normalizer          integer paise, canonical dates,
settlement summary │                            settleability decided once
bank statement ────┘        │
pricing rules               ▼
                   2. Control-equation engine  line, summary, roll-up, tie-out
                            │ residual deltas
                            ▼
                   3. Pass ladder              ordered, each pass consuming what
                            │                   the one before it could not
                            ├── exact join         identifier + amount + window
                            └── refund corroboration
                                   nine admissibility gates; resolves a delta
                                   only when EXACTLY ONE global allocation
                                   survives all nine, otherwise ABSTAINS
                            │ declined residual  (16 lines on primary)
                            ▼
                   4. Evidence reader  (built; opt-in via --adjudicate)
                            reads the evidence the gates cannot read;
                            may name one survivor or confirm the abstention;
                            never re-does arithmetic the gates already did
                            │
                            ▼
                   5. Exception classifier  ⟵ NOT BUILT YET
                            │
                            ▼
                   6. Report + audit trail    hash-chained, every decision
                                              overridable
```

**The model is not in the arithmetic path, and it is not in the search path
either.** An earlier design had the adjudicator propose which record to test
while the engine ruled on it. That was cut: proposing candidates is a search
problem the gates already solve exhaustively and deterministically, so the model
was being asked to guess at something already known, and its answer could only
be right by accident or redundant by construction.

What replaces it is narrower and honest. The ladder runs to completion first.
The adjudicator is handed **only the lines the gates could not separate**,
together with the surviving candidates and the evidence that is not arithmetic —
and it either names one or says it cannot. It never sees a case the gates
resolved, never re-opens one, and never computes a sum. On the primary batch
that is 16 lines out of 451, which is the point: **LLM call rate is a reported
metric and low is good.**

Those 16 are deliberately a mixed population — 12 are resolvable from the
settlement note, 4 are not — so an adjudicator that resolves everything handed
to it scores the first group and false-matches the second. **Abstention has to
survive contact with the model.**

### What the reader can be worth, measured

The rung is a one-method protocol, `EvidenceReader`, and the reader is injected.
Three ship: a **declining** reader (the default), an **Anthropic** reader, and a
**scripted** one for tests. With the default, the pipeline runs with no API key
and reproduces the deterministic numbers **exactly** — that equality is a test,
not an assurance.

To bound what the rung can be worth, `tests/test_adjudicator.py` runs a reader
that answers from the answer key. It is not a forecast of model accuracy; it is
the ceiling on the plumbing, and if a perfect reader could not lift the score
through this rung then the rung would be broken regardless of what is attached
to it. Measured, on the residual and with the real scorer:

| | dev | primary | stress |
|---|---|---|---|
| deterministic engine | 90 / 100 | 94 / 100 | 90 / 100 |
| lines handed to the reader | 28 | 16 | 26 |
| **perfect reader (ceiling)** | 100 / 100 | 100 / 100 | 100 / 100 |

The ceiling is reached **while still abstaining** on the four primary
`AMBIGUOUS_REFUND` cases, whose refunds are unallocated in the answer key — a
reader that claimed those would score below the ceiling, not above it. So the
achievable band on primary is **94 to 100**, and where a real model lands inside
it is an empirical question this repo answers by running the command, not by
asserting it.

Five properties are structural rather than promised, and each has a test:

- the rung is handed the abstention list by the runner, so it **cannot see a
  resolved case**;
- it answers with a letter from a closed shortlist, and a letter outside it is
  **discarded, not repaired** — it cannot name an event the gates never admitted;
- amounts and dates are **absent from the prompt**, because every candidate
  matched the delta exactly and they carry no discriminating information;
- a confidence below the floor is recorded as a **decline**; and
- the claim it produces is sealed into the audit chain naming **which reader
  decided it**, so a model-made attribution is never mistaken for a proof.

Cost is metered per call (input, output and cached tokens) and printed by the
command that incurs it. The stable system prompt is marked cacheable, since
paying full input price for the same preamble on every line would make the
reported cost per batch wrong in the flattering direction.

**Ties are never broken.** Not by frequency, not by a prior, not by batch
history, not by model plausibility. Gate 9 — that no second candidate survives
gates 1–8 — is what makes a resolution a proof rather than a guess.

### The nine gates

An anonymous settlement line resolves only when exactly one candidate survives
all nine. `make agent` prints how many candidates each gate eliminated, so a
gate that does nothing is visible rather than asserted.

| # | gate | question |
|---|---|---|
| 1 | unconsumed | is the event still unclaimed by another allocation? |
| 2 | exact amount | does it reproduce the delta exactly, in integer paise? |
| 3 | recovery window | does it fall inside the declared settlement window? |
| 4 | currency | do the line and the event agree? |
| 5 | lineage | does the parent payment exist and did it itself settle? |
| 6 | sign | is a refund line explained by a refund event? |
| 7 | controls hold | does the settlement still tie out after the allocation? |
| 8 | global feasibility | does committing to it leave every other line solvable? |
| 9 | uniqueness | does **no** second candidate survive gates 1–8? |

Gate 8 is the only global one. It is decided by **forcing** the pairing, not by
ranking it: take the candidate edge, remove both endpoints, re-solve the
residual assignment problem, and keep the candidate only if the residual still
reaches the baseline matching size minus one. That asks whether *some*
consistent global assignment uses this pairing — deleting the edge instead would
ask whether *every* one does, which is strictly stronger and would throw away
admissible candidates. Whether the pairing is the *only* one is gate 9's job,
and keeping the two questions in two gates is what keeps "could explain this
line" from being reported as "explains this line".

## Dataset at a glance

Six input files per batch — gateway ledger (event rows), settlement detail,
settlement summary, bank statement, pricing rules, batch config — plus two
answer-key views: `answer_key_cases.csv` (what the right answer is) and
`answer_key_allocations.csv` (the atomic view, which is what makes an
*attribution* checkable rather than merely an outcome). Full schema and
guarantees in [`docs/DATA_SPEC.md`](docs/DATA_SPEC.md).

Three families, three jobs. **development** is the only surface any threshold
may be tuned against, and it carries every scenario class — a tuning set that
lacks the phenomenon being tuned for freezes the abstention threshold at zero,
and the agent then never abstains. **primary** produces the headline number.
**stress** enriches the rare classes so a per-class rate rests on more than
three cases. Neither of the latter two is inspected while tuning.

<!-- STATS:START -->
| | dev (development, seed 42) | primary (primary, seed 20260905) | stress (stress, seed 101) |
|---|---|---|---|
| Gateway events | 500 | 500 | 500 |
| Reconciliation cases | 100 | 100 | 100 |
| Settlements | 122 | 112 | 124 |
| Settlement detail lines | 436 | 451 | 454 |
| Bank credits | 121 | 111 | 122 |
| Refund deltas with >1 exact candidate | 28 / 52 | 16 / 30 | 26 / 50 |
| B1 — exact joins only | 58 / 100 | 76 / 100 | 60 / 100 |
| B2 — B1 + amount lookup | 80 / 100 | 89 / 100 | 85 / 100 |
| B3 — B2 + fuzzy string matching | 79 / 100 | 90 / 100 | 82 / 100 |
| B3 lexical hits vs chance | 13 / 31 vs 13.9 | 8 / 17 vs 8.5 | 14 / 30 vs 14.1 |
| B2 false attributions | 26 | 16 | 20 |
| **Difficulty floor D (vs B2)** | **20.0%** | **11.0%** | **15.0%** |

Case mix by scenario:

| scenario | dev (development, seed 42) | primary (primary, seed 20260905) | stress (stress, seed 101) |
|---|---|---|---|
| `AMBIGUOUS_REFUND` | 8 | 4 | 6 |
| `BANK_CREDIT_DUPLICATE` | 3 | 2 | 5 |
| `BANK_CREDIT_MISSING` | 4 | 3 | 7 |
| `CAPTURED_UNSETTLED` | 6 | 6 | 7 |
| `CONTESTED_REFUND` | 14 | 8 | 14 |
| `CORROBORATED_REFUND` | 10 | 6 | 10 |
| `DESCRIBED_REFUND` | 10 | 6 | 10 |
| `DUPLICATE_DETAIL_EXPORT` | 9 | 8 | 10 |
| `FEE_TAX_VARIANCE` | 6 | 4 | 8 |
| `NOT_SETTLEABLE` | 5 | 6 | 3 |
| `REFUND_LATER_CYCLE` | 9 | 10 | 10 |
| `STRAIGHT_THROUGH` | 16 | 37 | 10 |

<sub>Table generated by `make stats` from the data in `data/`. Do not edit by hand.</sub>
<!-- STATS:END -->

Prevalence is counted **by case, not by row**, so a scenario that multiplies
rows (a duplicated detail export) cannot quietly inflate its own share of the
benchmark.

## The difficulty floor

Every match rate is quoted against **D**, the fraction of cases a trivial script
cannot resolve:

```
D = 1 - (cases the baseline resolves correctly / total cases)
```

The baselines ship as **runnable code**, not as an asserted percentage
([`src/recon/metrics/baselines.py`](src/recon/metrics/baselines.py)). A sceptic
can run them against the released CSVs and reproduce D without trusting the
generator. An asserted floor would be worth nothing.

**B1** is allowed exact joins, signed amounts, deduplication on the detail
primary key, fee and tax validation, every control equation, and lifecycle
disposition. It may not read narration, attempt refund recovery, or abstain.

**B2 is B1 plus exactly one rule**: attribute an anonymous refund line to any
unconsumed refund event whose amount reproduces it exactly — no window, no
lineage, no uniqueness test, no abstention.

**B3 is B2 with fuzzy string matching**, described in the next section.

**D is published against B2, and that is deliberate.** B1 flatters the
benchmark. B2 is the attack a sceptical reviewer would actually run, and running
it here is cheaper than having it run on us. An earlier version of this benchmark
scored B1 92/100 (D = 8.0%) and looked healthy — until B2 scored 100/100 and put
the real floor at **zero**. The whole batch was solvable by a slightly longer SQL
script, and a direct measurement showed the nine corroboration gates were
eliminating no candidates at all. `CONTESTED_REFUND` exists because of that
measurement: several unconsumed refund events share the delta amount exactly and
only one is admissible, so the amount-lookup shortcut guesses wrong and the gates
become load-bearing. The regression is a test
(`test_the_benchmark_is_not_trivially_solvable`), not a memory.

Scoring checks **attributions, not just outcomes**. A contested delta resolved to
the wrong refund event still yields `RECONCILED`, so a scorer comparing outcomes
alone would award full marks to a false match. Claimed
`(settlement_id, event_id)` pairs are checked against
`answer_key_allocations.csv`, and a right outcome with a wrong attribution is
scored wrong.

## The residual, and what it does and does not prove

`DESCRIBED_REFUND` is the class the deterministic ladder abstains on. It is
structurally identical to `AMBIGUOUS_REFUND` — two parent payments, two refunds
of the same amount, two settlements each carrying one anonymous refund line — so
every gate leaves exactly two survivors. The only difference is that its parents
come from different product categories and the settlement note is written in the
vocabulary of one of them, while `AMBIGUOUS_REFUND`'s parents share a category
so its note is present, on-topic, and separates nothing.

The answer key inverts between the pair: `AMBIGUOUS_REFUND` leaves its refunds
unallocated, so claiming one is a false match; `DESCRIBED_REFUND` allocates them,
so declining is a miss. **An agent that always resolves scores zero on the first
class, and one that always declines scores zero on the second.**

### What has actually been measured

The obvious objection is that a note column means fuzzy string matching would
have been enough. That objection ships as **runnable code** — B3 is B2 with its
arbitrary tie-break replaced by the strongest cheap lexical ranking, content-token
Jaccard or character sequence ratio, whichever is higher. Its accuracy is not the
measurement, because a different tie-break consumes different events and shifts
what later lines can claim. The measurement is per decision: over every
multi-candidate line with a knowable answer, how often the top-ranked candidate
is the right one, against the sum of 1/k expected from a k-sided coin.

| family | decidable lines | lexical hits | expected by chance | lift |
|---|---:|---:|---:|---:|
| primary | 17 | 8 | 8.5 | −0.029 |
| stress | 30 | 14 | 14.1 | −0.003 |
| development | 31 | 13 | 13.9 | −0.030 |

Maximum content-token overlap between a note and any candidate's description is
**0** on all three families, and the lift is negative on all three. String
similarity is not doing badly here; it has nothing to rank on.

### What that does not prove

Three things have been shown: **arithmetic cannot separate these candidates,
exact matching cannot, and lexical or fuzzy string similarity performs at
chance.** That is the whole of the claim.

It does **not** show that a language model is necessary. A semantic embedding
model, a trained classifier, a product ontology, or a sufficiently maintained
hand-written map from product name to category could each plausibly close this
class too — the last one certainly could, at the cost of an entry for every
product in every merchant's catalogue and a re-edit on every catalogue change.
That is an argument about maintenance cost, not about capability, and it is
stated as one.

The defensible claim is narrower and sufficient: **this residual requires
semantic interpretation of evidence that the deterministic accounting ladder
cannot reach.** Which mechanism supplies that interpretation is an engineering
choice, and a language model is the one this repo makes.

## Control equations

These are what the agent verifies. They hold exactly, except where a scenario
deliberately breaks one — and then it breaks exactly the one its scenario names.

```
per detail line:   net_effect = gross_effect − fee − tax
per summary:       net_amount = gross_payment − refund − fee − tax
roll-up:           summary totals == sum over UNIQUE detail_id lines
bank tie-out:      credit_amount == net_amount
fee:               fee = round_half_up(amount × fee_rate_bps / 10000)
tax:               tax = round_half_up(fee    × gst_rate_bps / 10000)
```

Money is **integer paise** everywhere, with `Decimal` confined to construction
boundaries. A reconciliation engine that reports a ₹0.01 tolerance breach caused
by its own binary-float error is worse than useless.

## The exception list

The brief asks for "an honest exception list", and rule 4 of this project is
that unmatched rows are never hidden. `make exceptions` writes
`runs/<family>/exceptions.csv` -- one row per case a human has to work, with
the evidence, the records behind it, and a named next step.

| family | open items | control breaks | exposure | unattributed | value |
|---|---:|---:|---:|---:|---:|
| dev | 37 | 19 | 310,567.23 | 18 | 4,000,221.22 |
| primary | 25 | 15 | 898,027.62 | 10 | 2,400,112.44 |
| stress | 43 | 27 | 802,579.12 | 16 | 4,000,171.16 |

**The two totals are never added together.** A control break is money that does
not tie out. An abstention is money that arrived and has not been attributed to
the right refund event -- unallocated, not missing. One combined figure would
report the second as a loss, and here it is the larger of the two, so the total
would be dominated by the part that is not actually gone.

The queue is ranked by **exposure**: the money behind each break, measured per
category rather than uniformly, because the categories are not commensurable. A
fee variance is worth the size of the variance; an uncredited settlement is
worth the whole settlement; a double credit is worth only the surplus that has
to go back. Each measure is stated at the point it is computed. Exposure zero
means the released files do not *size* the break -- a settlement with no summary
row is the clear case -- and those sort last, because inventing a figure to rank
one by would put a fabricated amount at the top of somebody's morning.

Two things are deliberately **not** in the list. `NO_ACTION` cases never appear:
those are `CREATED` and `FAILED` payments that were never going to settle and
are owed to nobody, and padding the list with them is a false positive the
scorer measures by name. Neither does a reconciled case carrying only a
duplicate-export warning, because the roll-up already used the deduplicated set
and no money moved.

### Classifying them, and what that is worth knowing

Precision and recall per category, from `make report`, on the same scorer that
produces every other number here:

| category | dev | primary | stress |
|---|---|---|---|
| BANK_CREDIT_DUPLICATE | 1.00 / 1.00 (3) | 1.00 / 1.00 (2) | 1.00 / 1.00 (5) |
| BANK_CREDIT_MISSING | 1.00 / 1.00 (4) | 1.00 / 1.00 (3) | 1.00 / 1.00 (7) |
| CAPTURED_UNSETTLED | 1.00 / 1.00 (6) | 1.00 / 1.00 (6) | 1.00 / 1.00 (7) |
| FEE_TAX_VARIANCE | 1.00 / 1.00 (6) | 1.00 / 1.00 (4) | 1.00 / 1.00 (8) |

`NO_ACTION` false positives: **0/5, 0/6, 0/3**. The confusion matrix is diagonal
in all three families.

That looks better than it is, and the reason should be stated rather than
defended when asked. Each of these categories is decided by a **control equation
that either holds or does not** -- a missing bank row, a second credit against
one UTR, a fee that disagrees with the rate card. There is no judgement in any
of them, so a perfect score is what a correct implementation is *supposed* to
produce; it measures that the equations are right, not that classification is
hard. The hard part of this dataset is attribution, and that is scored
separately, as a false-match rate and as the abstentions in the table above.

One number in the ranking is an artifact and is called out here rather than left
to be discovered. The refund scenarios draw their amounts from fixed bases in
the generator, because those classes need amounts provably disjoint from every
other refund for the cardinality argument to hold. So every `AMBIGUOUS_REFUND`
row lands within a few paise of every other one, and the ranking separates the
categories from each other -- the comparison an operator makes first -- while
separating almost nothing *within* the refund class. The mechanism is the
deliverable; the specific rupee magnitudes of those rows are a property of how
the benchmark had to be built.

### The abstention dial, and why its curve is flat

`make report` sweeps the confidence threshold and prints coverage, precision and
false-match rate at each stop, because a single accuracy figure hides the trade
every reconciliation team actually argues about: how much of the batch the
machine may close, against how often it may be wrong.

| threshold | dev | primary | stress |
|---|---|---|---|
| any | 0.82 coverage @ 1.0000 | 0.90 coverage @ 1.0000 | 0.84 coverage @ 1.0000 |

**Every row is identical, and that is the finding.** The deterministic ladder
emits exactly two confidences — 1.0 when the nine gates leave one survivor, 0.0
when it abstains — so no threshold in (0, 1] moves a single decision. The curve
is flat because there is no ranked middle to spend. That is what *proof or
abstain* looks like once you measure it instead of asserting it, and it is the
reason precision is 1.0000 at every stop rather than bought back by tightening.

Two things keep that honest rather than convenient. The note printed under the
table is **computed from the run**, so a rung that later emits graded confidence
retires it by changing the data rather than by someone remembering to delete a
paragraph. And the flatness itself is a test — `test_the_deterministic_ladder_is_certain_or_absent`
— not a sentence.

The dial is real on the evidence-reading rung, where `--min-confidence` decides
how sure the model must be before its reading is allowed to stand. Getting there
required fixing a defect worth recording: case confidence was hardcoded to 1.0
on every resolved path, so a line the model had *guessed* at was published as
though nine gates had proved it: with a reader answering at 0.72, fourteen dev
claims arrived below certainty and every case resting on them was still
published at 1.00, so the curve could not move no matter what the reader did.

A case now carries the **minimum** confidence of the claims holding it up —
minimum rather than mean, because averaging lets one proved leg launder a
guessed one straight through an operator's threshold filter. Every
deterministic claim is 1.0, so every published number above is byte-identical
before and after the fix, and that equality is what makes it safe.

## The demo surface

`make demo` writes `runs/demo/index.html`: the scoreboard, the difficulty floor,
per-pass yield, per-gate eliminations, the abstention curve, the classification
table, the operator queue with its evidence, and the run's audit-chain head —
all three families, one file.

It is **one HTML file with no server, no build step, no network access and no
JavaScript.** It opens from disk, prints to PDF and attaches to an email. That
is not minimalism for its own sake: a reconciliation result is something you
hand to somebody, and a demo that needs a running process is a demo that is
broken the day after the deadline.

Two properties matter more than how it looks, and both are tests rather than
intentions:

- **It agrees with the terminal.** The page, `make report` and `make exceptions`
  all go through the same `compare()` call and the same exception builder. A
  dashboard that computed its own figures could flatter the run in ways the
  command line never showed, so `test_the_page_and_the_terminal_report_agree`
  renders both and requires the numbers that carry the claim to appear in each.
- **It tracks the run.** Rendered from a deliberately weaker ladder, the page
  has to change — `test_a_weaker_ladder_renders_a_different_page`. Without that,
  every other assertion about the page would be checking that a constant is
  still a constant.

No figure on the page is typed into it, there is no rounded headline written by
hand beside a computed one, and where a number would mislead on its own the
qualifying number shares its row rather than sitting in a footnote — because a
reader looking at a dashboard reads rows, not footnotes. CI builds the page on
every run and uploads it as an artifact, so a judge is never the first person to
discover it broke.

## Audit trail

Every decision records what it saw, which rule fired, what it concluded, with
what confidence, and whether it is overridable. A decision a model made records
**which reader made it** — `adjudication/SUGGESTED by claude-haiku-4-5-20251001`
— because confidence and reasoning say what was concluded and not whose
judgement it was, and after a model swap the log could not otherwise answer
which attributions the old one made. Declines the rung reaches on its own rules,
with no reader consulted, name nobody. Two hashes do two jobs:

- **`decision_id`** is an *identity*. It covers stage, subject and action only,
  which is what makes it stable enough to be referenced by an override and
  deduplicated on append.
- **`record_hash`** is a *seal*. It covers the complete record and chains to its
  predecessor: `SHA256(previous_hash ‖ canonical_json(record))`. Editing an
  amount, a rule, a confidence or a reasoning string breaks it; so does deleting,
  inserting or reordering a record, because every downstream link then points at
  a hash that no longer exists.

### The pipeline writes one, and CI re-reads it

A module that *can* seal decisions while the pipeline never emits any is a
library, not an audit trail. `make audit` runs the agent over all three
families with journalling on and writes `runs/<family>/audit.jsonl` --
**1,587 sealed decisions** across the three, every attribution, every
abstention with the shortlist it could not separate, and every case
disposition. `make verify-audit` then recomputes the whole chain, and both
steps run in CI.

The log is **derived from the finished run**, not appended as a side effect
by each rung. A rung that forgot to log would be undetectable, and the
contents would depend on the order each pass happened to iterate its inputs.
Deriving it makes *every decision is logged* one function with one test
instead of a discipline six modules have to keep. The cost, stated before
anyone finds it: a derived log can only record what the run retained --
which is why `PassResult` keeps abstentions with their candidates and the
runner keeps the claims it refused, with the reason for each.

Journals live under `runs/`, never in `data/`. A dataset directory is an
input and stays read-only; writing run output into it would make CI's
byte-for-byte regeneration check compare a run against itself.

Two runs over the same data seal to **different** head hashes, because the
timestamp is part of the record. That is deliberate: a log that hashed the
same whether it was written yesterday or today would be a checksum of the
input, not a record of when a decision was taken.

Attributions, abstentions and dispositions are all overridable. The one
decision that is not is the runner refusing a claim because the event was
already consumed -- reinstating that would allocate one event to two lines,
which is the single invariant the runner exists to hold.

The final `head_hash` is a single value that attests to the entire log.

**What this does not do:** it does not stop someone who can rewrite the whole
file, since they can recompute the chain. Detecting that needs the head hash held
where the writer cannot reach it. The chain makes tampering *detectable given a
trusted head*, not impossible.

## Layout

```
src/recon/datagen/      generator, catalogue, config, serialisation
src/recon/match/
  normalize.py          parsing, integer paise, settleability
  controls.py           the control equations
  passes.py             the ordered pass ladder
  recovery.py           the nine-gate corroboration pass + bipartite solver
  adjudicator.py        the evidence-reading rung (opt-in, injected reader)
  caseload.py           grouping rows into scoreable cases
  controller.py         the pipeline, per-pass yield, per-gate eliminations
  audit.py              hash-chained decision log + human override layer
  journal.py            turns a finished run into that log
  exceptions.py         the operator queue, ranked by exposure
src/recon/metrics/
  score.py              one scorer, used by both the agent and the baselines
  baselines.py          B1, B2, B3 and the lexical hit-rate measurement
  report.py             the published comparison table
  dashboard.py          the demo surface: one self-contained generated page
tests/                  answer-key self-validation + engine tests, 4 seeds
tools/ambiguity.py      independent recovery-ambiguity measurement
tools/refresh_stats.py  regenerates the README table from data/
docs/DATA_SPEC.md       schema, scenario classes, guarantees, limitations
docs/BUILD_PLAN.md      phase status and what is deliberately not built
docs/HOLDOUT.md         the pre-registered holdout run and its honest reading
docs/superpowers/specs/ the authoritative benchmark design record
data/dev/               development family (the only tuning surface)
data/primary/           headline batch      (never tune here)
data/stress/            rare-class batch    (never tune here)
```

Tests run across four seeds, not one. A labelling bug once passed because the
defective case did not occur in seed 42.

## Continuous integration

CI re-runs the answer-key suite, regenerates all three datasets and fails if a
single byte differs from the committed CSVs, re-derives every published baseline
and the agent's score on a machine that is not the author's, verifies the audit
chain, builds the exception queue, and fails the build if the generated README
table above has gone stale.
The intent is that no number in this repo has to be taken on the author's word.

## Honest limitation

The reported families are different seeds from the same generator. They measure
whether tolerances were overfitted; they do **not** measure robustness to real
bank data, because the same code wrote both the defects and their labels.
Claiming more than that is the trap this benchmark exists to avoid.

The same honesty applies to the floor above. That defect was caught by
attacking this benchmark rather than by a reviewer finding it — but it did
survive, unnoticed, through a version considered finished. The safeguard is
that the attack now runs as a test, on every seed.

`DESCRIBED_REFUND` carries one more. Real operations notes sometimes *do* quote
the product name, and those cases would be lexically solvable. Forcing token
overlap to exactly zero isolates the semantic channel rather than simulating how
often it occurs, so the class measures whether an agent can read — not how often
real reconciliation needs it to.

## License

MIT
