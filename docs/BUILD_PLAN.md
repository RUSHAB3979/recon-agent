# Build plan — revised 24 Aug 2026

Twelve days to the 5 September deadline. This revision folds in the industry
research (BlackLine, Oracle ARCS, Modern Treasury) and two corrections found
while grounding that plan against the actual repo.

Supersedes the phase list in `.claude/plans/greedy-cuddling-chipmunk.md`, which
was written against a dataset design that has since been replaced.

---

## 1. What the research changed

The research validated the architecture and gave us its vocabulary. Three
production engines — BlackLine Transaction Matching, Oracle Account
Reconciliation, Modern Treasury — converge on the same shape we had already
chosen: **an ordered ladder of deterministic passes, each declaring its own
cardinality and tolerance, with an explicit escape hatch for ambiguity.**

Modern Treasury's entire rule vocabulary is four operators — `equals`,
`contains`, `less_than_or_equals`, `greater_than_or_equals`. No model touches
the match. That is the strongest available external answer to "why isn't the
LLM doing the matching."

Four concrete adoptions, all cheap, all defensible:

| Adoption | Source | Why |
|---|---|---|
| Passes are **ordered and named**, specific before broad | MT: "rules are evaluated sequentially… it is important to order rules deliberately" | Makes the ladder auditable and lets us report per-pass yield |
| Each pass **declares cardinality**: `1:1`, `1:N`, `N:1`, `N:M` | Oracle's five rule types | Cardinality stops being implicit in the code |
| **Typed tolerance**: fixed / percentage / percentage-with-cap | Oracle: "up to 1.0% … up to a maximum amount of 100.00" | Scalar tolerance is the amateur version; the trichotomy is the standard |
| **Three match tiers**: Confirmed · Suggested · Abstain | Oracle `Suggested` / `Confirmed`; BlackLine's automatic vs suggested pass rules | Our confidence threshold gets an industry name, and the middle tier stops being invisible |

### The finding worth building around

Oracle documents its default behaviour on a tie:

> "In a 1 to 1 match, if two transactions exist that qualify as a match with a
> third transaction, but only one can be matched, the transaction with the
> lowest Transaction ID will be the one selected as the match."

Transaction IDs are assigned sequentially at load. **The default tiebreak in a
major enterprise reconciliation engine is file order.** Abstention exists only
as an opt-in flag (`Suggested (No Ambiguous)` / `Confirmed (No Ambiguous)`),
whose documented effect is that "all transactions will remain unmatched."

Our design is that flag made default and then *measured*. This is the panel
answer for the whole project:

- `AMBIGUOUS_REFUND` → abstention is the default, not a checkbox.
- `CONTESTED_REFUND` → the case Oracle resolves by file order, we resolve by
  nine named gates.
- **false-match rate** → the number that says what the file-order tiebreak
  would have cost. Nobody publishes it because production has no ground truth.
  Synthetic data is the *only* reason this metric can exist, which turns our
  stated limitation into the design rationale.

### Claims from the pasted vendor profile that must NOT be repeated

- BlackLine is a **Challenger**, not a Leader, in the 2025 Gartner MQ for
  Financial Close and Consolidation Solutions. The MQ it led (2017–2019) was
  the retired "**Cloud** Financial Close Solutions" MQ.
- "95%–99.8% match rate" is not a vendor claim. 95% is an arithmetic worked
  example on a third-party glossary. The real published figures are
  per-customer, per-account-type: **99% of GL-detail and 96% of cash-to-GL**
  matching at one named customer.
- "Hundreds of millions of daily records" is unsourced; BlackLine's own copy
  says "tens of millions of transactions in each pass."

If we quote industry match rates at all, quote them with their account type and
their denominator — the same standard we hold ourselves to.

---

## 2. What grounding changed (bigger than the research)

### A. `reference.py` and `fuzzy.py` are dead. Delete `fuzzy.py`.

Bank narration is now realistic:

```
NEFT CR: AXIS UTIBN71968999353 RAZORPAY SETTLEMENT
```

There is no per-order reference in it. `make_reference` and `garble` no longer
exist anywhere in `src/`. `src/recon/match/fuzzy.py` is 232 lines of confusable
expansion, prefix lookup and edit distance, imported by nothing except
`tests/test_fuzzy.py`.

**It is matching a field that the dataset does not contain.** Shipping it would
invite exactly one panel question, and there is no good answer to it.

This deletes the old plan's Phase 2 (S1 reference matcher) and Phase 5 (S3
fuzzy recovery) outright — roughly two days recovered.

### B. `primary` — the headline family — omits two scenario classes

`PRIMARY_CASE_SHARES` in `config.py` sums to 100 across nine scenarios and
leaves out `BANK_CREDIT_MISSING` and `BANK_CREDIT_DUPLICATE`, both of which are
present in `development` (4, 3) and `stress` (7, 5).

An agent with zero handling for bank-side defects scores identically on the
headline number. **This invalidates the headline until fixed.**

### C. The measured shape of the task

| | dev | primary | stress |
|---|---|---|---|
| settlement detail lines | 444 | 442 | 454 |
| lines carrying `event_id` (free join) | 412 | 424 | 424 |
| **anonymous REFUND lines (the whole matching problem)** | **32 (7.2%)** | **18 (4.1%)** | **30 (6.6%)** |
| answer-key allocations | 431 | 422 | 452 |

Every anonymous line is `line_type = REFUND`. Nothing else in the export is
unattributed.

**Consequence for sequencing.** The allocation axis is a small, sharp target —
in primary, B1 already reaches 0.9668 recall at 1.0000 precision by joining,
and the entire remaining allocation work is ~14 lines. The *outcome* axis is
the bigger half: B1 scores 0.820 outcome accuracy, B2 0.920, and closing that
means classification — the `NOT_SETTLEABLE` trap, `CAPTURED_UNSETTLED`,
`FEE_TAX_VARIANCE`, duplicate detection — not more matching.

**The exception classifier therefore moves forward.** The old plan had it at
31 Aug as a late nicety. It is worth more score than the recovery pass.

---

## 3. Revised phases

**Status as of 1 Sep 2026.** Phases A through E are complete and verified;
`make verify` is green at 818 tests. Phase C closed all three families at
100/100, which saturated the benchmark and forced an unplanned hardening pass
(section 3a below) before Phase E could measure anything. Phase D is closed by
Phase H, which wired the abstention curve into `make report` and found, while
doing it, that case confidence was hardcoded to 1.0. Only the demo surface
remains.

| phase | state |
|---|---|
| A — data integrity | ✅ complete |
| B — normalizer + pass ladder | ✅ complete |
| C — nine-gate recovery pass | ✅ complete |
| 3a — benchmark hardening | ✅ complete (unplanned) |
| D — full report | ✅ complete; abstention curve landed in Phase H |
| E — adjudicator | ✅ complete, off by default |
| G — exception list | ✅ complete |
| H — abstention curve | ✅ complete (unplanned defect found) |
| F — surface | 🟡 holdout run and demo outstanding |


### Phase A — data integrity (24 Aug, half a day)

- Add `BANK_CREDIT_MISSING` and `BANK_CREDIT_DUPLICATE` to
  `PRIMARY_CASE_SHARES`, rebalancing `STRAIGHT_THROUGH` down. Primary keeps a
  deliberately *easier* mix than stress — it is the realistic-prevalence
  family, not the enriched one — but it must contain every class.
- Delete `src/recon/match/fuzzy.py` and `tests/test_fuzzy.py`.
- Regenerate all three families; re-run `make verify`; **republish D**.

Gate: 273 tests green, D still > 0 on all three families, byte-identical
regeneration for a fixed seed.

### Phase B — normalizer + the pass ladder skeleton (25–26 Aug)

`src/recon/match/normalize.py` — parse all six CSVs into canonical records.
`Decimal` at the boundary, **integer paise** inside. Settleability decided once
here, so `failed`/`created` never enter a candidate pool and `NOT_SETTLEABLE`
is handled structurally rather than by a later filter.

`src/recon/match/passes.py` — the uniform pass interface:

```python
class Pass(Protocol):
    name: str
    cardinality: Cardinality      # ONE_TO_ONE | ONE_TO_MANY | MANY_TO_ONE | MANY_TO_MANY
    tolerance: Tolerance | None   # FIXED | PERCENTAGE | PERCENTAGE_CAPPED
    def run(self, pool: Pool) -> list[Claim]: ...
```

**Hybrid by design, and say so.** Simple passes are declared as
field-operator-value conditions in the industry style. The two hard passes —
nine-gate refund corroboration and the control-equation reconstruction — are
code behind the same interface. This is not a compromise: BlackLine's
"suggested" rules and Oracle's `Many to Many` are the same escape hatch from a
generic condition model. Every engine has one.

Every pass returns claims *and* the reasons for them, so the audit trail is
produced by construction rather than bolted on.

Gate: the ladder with only the join pass reproduces **B1 exactly** — 0.820
outcome, 1.0000 allocation precision on primary. Any deviation is a bug with a
known answer.

### Phase C — recovery pass + exception classifier (27–29 Aug)

`src/recon/match/recovery.py` — the nine-gate corroboration pass, emitting
`Confirmed` on a unique survivor, `Suggested` when survivors are tied but a
preference is defensible, `Abstain` when they are not. Target: beat B2's
0.9812 allocation precision *without* dropping below its 0.9905 recall.

`src/recon/match/exceptions.py` — every unclaimed record gets a named category,
evidence, confidence, recommended action. Categories intersect the industry
taxonomy where they mean the same thing (Oracle publishes exactly three:
**Ambiguous**, **Date**, **Amount**) and extend it where our domain needs more.
`NO_ACTION` records never enter the list; the harness already measures that
false-positive rate explicitly.

Gate: outcome accuracy materially above B2's 0.920 on **dev**, with the
false-match rate reported beside it, not after it.

### 3a. Benchmark hardening (26 Aug) — unplanned, and the reason it was needed

Phase C worked too well. With the join rung and the nine-gate recovery rung the
agent closed **100/100 on all three families at a 0.00% false-match rate**. That
is a saturated benchmark: it can no longer distinguish a better agent from this
one, Phase E would have had zero residual to act on, and a submission whose
headline is 100% invites exactly one question — what did you leave out?

So the benchmark gained a residual the deterministic engine provably cannot
close. The bar on that residual was set before the data was written:

> It must be unresolvable by arithmetic, unresolvable by string similarity, and
> resolvable by reading.

**`DESCRIBED_REFUND`** meets it. Structurally it is a clone of
`AMBIGUOUS_REFUND` — two parents, two same-amount anonymous refunds, two
settlements — so every gate leaves exactly two survivors and the ladder abstains.
The only difference is that its two parents come from **different** catalogue
categories and each settlement note names one, while `AMBIGUOUS`'s parents now
share a category so its note separates nothing. The answer key inverts between
them: `AMBIGUOUS` leaves its refunds unallocated so claiming is a false positive,
`DESCRIBED` allocates them so declining is a miss. Always-resolve scores zero on
one class, always-decline scores zero on the other.

Supporting work, all of it load-bearing:

- `src/recon/datagen/catalogue.py` — eight categories, thirty-two products,
  twenty-four ops notes, with `assert_no_lexical_leak()` running **at import**
  rather than in a test. A dataset generated from a leaking catalogue would be
  silently easier and no test running afterwards would say so. It fired on first
  run and caught two genuine leaks.
- `description` on **every** payment and `reference_text` on **every** detail
  line, so neither field's presence identifies a hard case; `description` on
  **no** refund, so reaching a refund's category costs a lineage hop.
- Two new generator assertions, proved per seed across 4 seeds × 3 families: for
  every described line exactly one survivor traces to the note's category, for
  every ambiguous line **all** survivors do. Both read the category back off the
  emitted `description` rather than the generator's own bookkeeping.
- **B3, the lexical baseline** — the "you only needed fuzzy string matching"
  objection shipped as runnable code, sharing its tokeniser with the leak
  invariant so it cannot be quietly weaker than the property it falsifies.

B3's case count turned out to be the wrong instrument: a different tie-break
consumes different events and shifts what later lines can claim, so on one seed
it beat B2 by four cases for reasons unrelated to ranking. The measurement is
per decision instead — over multi-candidate lines with a knowable answer, hits
against the sum of 1/k expected from a k-sided coin.

| family | lexical hits | expected by chance | lift |
|---|---:|---:|---:|
| primary | 8 / 17 | 8.5 | −0.029 |
| stress | 14 / 30 | 14.1 | −0.003 |
| development | 13 / 31 | 13.9 | −0.030 |

Maximum content-token overlap is **0** on all three families and the lift is
negative on all three. String similarity is not doing badly here; it has nothing
to rank on.

**Result.** The agent now scores 94/100 primary, 90/100 dev, 90/100 stress, still
at a 0.00% false-match rate and 1.0000 allocation precision — and **0/6, 0/10,
0/10 on `DESCRIBED_REFUND`, by abstaining**, while holding 100% on every other
refund class. That is the residual Phase E converts. A repo where the model rung
is dead code is a worse panel answer than one where it converts the cases the
gates provably cannot.

### Phase D — full report (30–31 Aug) — **schedule protection line**

`make report` produces: match rate quoted against D · precision · recall ·
**false-match rate** · per-scenario breakdown · exception precision/recall per
category · `NO_ACTION` false-positive rate · **per-pass yield table** (new,
from the research — which rule earned its keep) · throughput · precision/
coverage curve across abstention thresholds.

**Everything through 31 Aug is a complete, defensible submission**: deterministic
core, real numbers against a published floor, honest exception list, two
baselines. What follows raises the score without being load-bearing.

### Phase E — adjudicator (1–2 Sep) — **BUILT**

`src/recon/match/adjudicator.py`. Fires only on `Suggested` and `Abstain`
residuals — which, at 16 declined lines in primary, is a *tiny* call volume, and
that is the point: the LLM call rate is a headline metric and low is good.

Those 16 lines are now a genuinely mixed population, and separating them is the
job: 12 of them are `DESCRIBED_REFUND` lines the settlement note resolves, and 4
are `AMBIGUOUS_REFUND` lines where the note is present, on-topic, and separates
nothing. An adjudicator that resolves everything it is handed scores the first
group and false-matches the second. **Abstention has to survive contact with the
model**, which is precisely the behaviour the two paired classes were built to
measure.

**What was built, and how it differs from this plan.** The tool-using shape
described above — an agent that may call `verify_sum` but never compute one —
was dropped, and the reason is worth recording because it is a panel answer.
Giving the model tools presumes there is something left for it to look up. By
the time a line reaches this rung the gates have already verified the amount to
the paise, the window, the currency, the lineage, the controls and global
feasibility, for every surviving candidate. A `verify_sum` call could only
return what the gates already proved. The tool surface would have been theatre:
real code, real latency, real tokens, and no decision that depended on it.

What is there instead is smaller and does exactly one thing the gates cannot:

- `EvidenceReader` is a one-method protocol, and the reader is injected. Three
  ship — `DecliningReader` (the default), `AnthropicReader`, `ScriptedReader`.
- `AdjudicationPass.run_residual` is handed the abstention list by the runner,
  so "it never sees a resolved case" is a property of the controller rather
  than a promise made by the rung.
- The model answers with a **letter from a closed shortlist**. A letter outside
  it is discarded, not repaired.
- **Amounts and dates are absent from the prompt.** Every candidate matched the
  delta exactly, so they carry no discriminating information, and showing them
  would invite a fabricated numeric justification for a decision made on other
  grounds. That is what makes "no LLM arithmetic" structural here: there is no
  arithmetic in the input.
- Confidence below a declared floor is recorded as a decline, and declining is
  always available.

Tiered routing survives in the sense that the model is a constructor argument;
the default is Haiku 4.5, because the task is a two-way reading comprehension
question over one sentence and paying Opus rates for it would be a cost metric
own-goal.

The rung is **off by default** (`--adjudicate` turns it on) and contributes to
no published number. With the declining reader the pipeline reproduces the
deterministic figures exactly, and that equality is a test.

Measured ceiling, using a reader that answers from the answer key — the bound on
the plumbing, not a forecast of model accuracy: **100/100 on all three
families**, reached while still abstaining on the four primary
`AMBIGUOUS_REFUND` cases. The achievable band on primary is therefore 94 to 100.

Abstention remains free and never penalised.

### Phase F — surface (3–5 Sep)

Demo UI, README via `make stats` (never hand-edited), pitch video. Holdout runs
**once**, at the start of this phase.

The tamper-evident audit chain has landed: `record_hash =
SHA256(previous_hash + canonical_json(complete record))`, verified on read, with
a `make verify-audit` target and a `python -m recon.match.audit` CLI. Note the
honest limit — a chain establishes that a log was not edited *in place*; it
cannot establish that the head is the head somebody wrote, which needs a trusted
copy of the last hash.

The controller now writes one too. `recon.match.journal` derives an
`AuditLog` from a finished `RunResult` -- every attribution, abstention and
case disposition -- and `make audit` emits `runs/<family>/audit.jsonl` for
all three families (1,587 sealed records). It is derived rather than
appended per rung so that coverage is a property of one tested function
instead of a discipline every module has to keep; the cost is that it can
only record what the run retained, which is why nothing is dropped between
the decision and the record.

One bug worth remembering: `verify-audit` originally globbed its file list
with `$(wildcard)`, which make expands when it PARSES the file. `make
verify` therefore checked the logs that existed before its own `audit`
prerequisite ran -- on a clean tree, none -- and printed a reassuring
message while verifying nothing. The expansion moved into the module, and
the command now exits non-zero when there is nothing to verify, because a
check that passes over the empty set is not a check.

### Phase G -- the exception list

Step 6 of the build order turned out not to be "build a classifier". The
control equations in `controls.py` plus the first-hard-finding rule in
`_verdict` were already assigning categories, and measuring them BEFORE
building anything showed precision and recall of 1.00 on all four categories in
all three families, with a diagonal confusion matrix and no `NO_ACTION` false
positives. Writing a classifier would have been writing a second one.

What was actually missing was two things the project's own rules require and
nothing produced. `CLAUDE.md` asks for an "exception breakdown by category (not
just a total)" -- computed by the scorer, never printed by the report. And rule
4's differentiator, "these 14 I couldn't resolve, and here's why", had no
artifact at all: exceptions existed only as in-memory `Verdict` objects.

So `recon.match.exceptions` renders the workable subset of a finished run as a
ranked CSV, `make exceptions` writes one per family, and `report.py` now prints
the category table, the confusion matrix, the abstention split and the
`NO_ACTION` false-positive rate.

Ranking is by exposure, which meant `Finding` had to start carrying the size of
each break -- a report that parses a rupee figure back out of its own prose is
one message rewrite away from silently sorting by nothing. The measure is
per-category and stated at each construction site, because the categories are
not commensurable: a variance is worth the variance, an uncredited settlement
the whole settlement, a duplicate credit only the surplus.

Two figures are reported separately and never summed: control breaks (money
that does not tie out) and abstentions (money that arrived and is not yet
attributed). On every family the abstentions are the larger number, so a
combined total would be dominated by the part that is not missing.

### Phase H -- the abstention curve, and the defect it exposed

`precision_coverage_curve` had existed in the scorer, with a test, since the
metrics module was written. Nothing printed it. Wiring it into `make report`
was meant to be the last small item on Phase D and instead found the reason it
had never been missed.

**Case confidence was hardcoded to 1.0 on every resolved path.** The
adjudicator books SUGGESTED claims at whatever the reader stated, and `_verdict`
overwrote that with certainty on the way to the scorer. Measured with a reader
answering at 0.72: fourteen dev claims arrived below 1.0, and every case
resting on them was published at 1.00. Three consequences, none visible in any
headline number:

- A line the model guessed at was indistinguishable, in the published record
  and in the audit log, from one nine gates had proved.
- The SUGGESTED tier -- adopted from Oracle and BlackLine precisely so the
  middle tier would stop being invisible -- was invisible in every metric that
  quotes confidence.
- The abstention dial was inert. No threshold could move a decision, so the
  curve would have been flat under every reader, and the flatness would have
  looked like a property of the engine instead of a bug in the reporting.

A case now carries the **minimum** confidence over the claims holding it up.
Minimum rather than mean: a case with one proved leg and one guessed leg is a
guess, and averaging would let the proof launder the guess through an
operator's threshold filter. A case resting on no claims is a disposition the
engine reached alone and stays at 1.0.

Every deterministic claim is 1.0, so the published figures are byte-identical
before and after -- 818 tests green, `make verify` unchanged, the README table
regenerated to the same numbers. That invariance is what made the fix safe to
make this late.

On the deterministic ladder the curve is genuinely flat, and the report says so
in a line **computed from the run** rather than asserted in prose, so a future
graded rung retires the note by changing the data. Coverage at every threshold:
0.82 dev, 0.90 primary, 0.84 stress, each at 1.0000 precision and a 0.00%
false-match rate. Two tests hold the pair of claims that matters: the
deterministic ladder is certain or absent, and a rung supplying graded
confidence makes the dial trade coverage for precision in the expected
direction.

Still outstanding: the demo surface. If a number disappoints it gets reported,
not re-tuned.

---

## 4. Cut list

- `src/recon/match/fuzzy.py` + its tests — matching a field that no longer exists.
- The old S1 reference matcher — same reason, never written.
- pandas — never used; stdlib `csv` + `Decimal` + integer paise. pandas coerces
  numeric columns to `float64` and money never touches a float here.

## 5. Honest limitation, stated before anyone asks

The held-out families are different seeds from the same generator. They measure
whether tolerances were overfitted. They do **not** measure robustness to real
bank data, because the same code wrote both the defects and their labels.
