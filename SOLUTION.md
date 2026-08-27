# The solution, end to end

A reading document. The formal design record is
[`docs/superpowers/specs/2026-08-23-reconciliation-benchmark-design.md`](docs/superpowers/specs/2026-08-23-reconciliation-benchmark-design.md);
this file explains the same system in the order you would explain it to someone
out loud.

---

## 1. What problem this solves

When a customer pays a merchant online, the money does not travel from the
customer's bank to the merchant's bank. It goes through a payment gateway.

1. The customer pays. The gateway authorises the transaction and records it in
   its own ledger, with a status such as `created`, `captured`, `failed` or
   `refunded`.
2. The gateway does not forward each payment individually. It **batches** a
   settlement cycle's worth of payments, deducts its fee (MDR) and the GST on
   that fee, nets off any refunds, and sends the merchant **one bulk transfer**.
3. The merchant's bank receives that transfer and writes **one line** on the
   statement.

The merchant now holds two independent records of the same money — the gateway's
transaction-level ledger and the bank's settlement-level statement — and has to
prove they agree. Every cycle. That proof is reconciliation, and done by hand it
is slow, tedious and error-prone at any real volume.

### Why it is specifically hard for an AI system

The obvious move is to hand the whole job to a language model. That is the wrong
answer, and knowing why is the point of this project.

Reconciliation is arithmetic over money. A language model is non-deterministic,
cannot be audited line by line, costs money per record, and — the part that
matters — if it silently mismatches two payments, the books still look clean and
nobody notices.

> **A wrong match is worse than no match.** An unmatched row gets a human to look
> at it. A confidently wrong one does not.

---

## 2. The governing principle

> **Deterministic code does all arithmetic. The model handles language and
> judgment, and is structurally prevented from asserting a number it did not
> have verified.**

Every design decision below serves that sentence.

---

## 3. Two measurements that shaped the design

Both of these overturned a design I had already committed to. They are recorded
here in the order they happened, because the reasoning matters more than the
conclusion.

### 3.1 Making identification hard is not possible

The intended difficulty was identification: damage the reference in the bank
narration, and make the matcher work out which payments compose each credit.

That design does not survive contact with a measurement. Ambiguity comes from
repeated values, and 45% of the amounts here snap to a price-point catalogue, so
₹499 recurs and collides. But summing two such payments is a **convolution of a
spiky discrete distribution with itself** — the pair-sums spread across hundreds
of distinct values, and by three payments every total is unique.

| payments per settlement | ambiguity |
|---|---|
| 1 | 25.2% |
| 2 | **0.0%** |

Aggregation does not make identification harder. It annihilates it. Widening the
search to a whole day instead gives 89.5% ambiguity — unsolvable rather than
hard. There is no dial setting in between, so identifiers are **published in the
clear** and joins are expected to succeed.

That is not a concession. It is the actual claim of this project:

> An exact identifier proves *which* transfer a row refers to. It proves nothing
> about whether the amount is correct, whether anything is missing, whether
> something was counted twice, whether a payment that should have settled did,
> or whether a row was ever going to settle at all.

The difficulty lives on the two axes that survive: **explanation** ("the amount
does not tie — why?") and **disposition** ("is this row supposed to have a
counterpart at all?").

### 3.2 The benchmark's own difficulty floor was zero

This is the more important finding, because I found it in my own work after
believing the benchmark was finished.

Every match rate here is quoted against **D**, the fraction of cases a plain
exact-join script cannot resolve. B1 is that script, and it ships as runnable
code rather than as an asserted percentage:

```
D = 1 − (cases B1 resolves correctly ÷ total cases)
```

B1 scored 92/100 on the primary batch — D = 8.0%, which looked healthy. Then I
asked the question that a hostile reviewer would ask: *is B1 actually the best
trivial attack?* So I built **B2 — B1 plus exactly one rule**: attribute an
anonymous refund line to any unconsumed refund event whose amount reproduces it
exactly. No window check, no lineage check, no uniqueness requirement.

```
primary   B1  92/100  D = 8.0%   →   B2  100/100  D = 0.0%
```

**The benchmark was entirely solvable by a slightly longer SQL script.** Its real
difficulty floor was zero.

The diagnosis was worse than the symptom. The centrepiece of the architecture is
a nine-gate corroboration rule, and a direct measurement showed the candidate
counts were byte-identical before and after applying all nine gates — `{1: 8}`
either way, on every family and seed. **Gates 2 through 8 never eliminated a
single candidate.** The entire corroboration architecture was decorative.

The root cause: no case existed where amount was ambiguous *but the gates
resolved it*. Deltas were either amount-unique (gates redundant) or amount-tied
and still tied afterwards (gates insufficient). The middle case — the whole
justification for the rule — was never generated.

The fix was a new scenario class, `CONTESTED_REFUND`: several unconsumed refund
events share the delta amount exactly, and exactly one is admissible. The others
are distractors that each fail one **named** gate — already consumed (gate 1),
outside the recovery window (gate 3), or lineage broken because the parent
payment never settled (gate 5). Now the amount-lookup shortcut guesses wrong,
the gates earn their place, and D measures capability rather than a handicap.

Two things were required to make that fix real, and both are worth stating:

- **Outcome-only scoring would have hidden it.** A contested delta attributed to
  the *wrong* refund event still produces the expected outcome, `RECONCILED`. So
  the scorer checks the claimed `(settlement_id, event_id)` allocations against
  `answer_key_allocations.csv`, and a case with a right outcome and a wrong
  attribution is scored **wrong**. Without that, adding the class would have
  changed nothing and I would have believed it worked.
- **The regression is a test, not a memory.** `test_the_benchmark_is_not_trivially_solvable`
  asserts D > 0 under B2 on every family, and `test_gates_are_load_bearing`
  asserts B2 cannot solve the classes that require uniqueness or abstention.

The general lesson is the one I would defend in a room: **a benchmark you wrote
yourself is not evidence until you have attacked it yourself.** Publishing B1
alone would have been an honest-looking number resting on a benchmark that a
reviewer could have collapsed in ten minutes.

---

## 4. The pipeline

```
gateway ledger ──┐
settlement detail ├──> 1. Normalizer            integer paise, canonical dates
settlement summary│
bank statement ──┘         │
pricing rules              ▼
                  2. Control-equation engine    line, summary, roll-up, tie-out
                           │ residual deltas
                           ▼
                  3. Corroboration              nine admissibility gates;
                     resolves a delta only when EXACTLY ONE
                     global allocation survives all nine
                           │ still ambiguous
                           ▼
                  4. LLM adjudicator            proposes which record to TEST;
                     the deterministic engine decides whether it passes
                           │
                           ▼
                  5. Exception classifier       named categories, not a total
                           │
                           ▼
                  6. Report + audit trail       every decision overridable
```

**Stage 1 — Normalizer.** Parses everything into integer paise, with `Decimal`
confined to the construction boundary. Settleability is decided once, here:
`failed` and `created` rows never enter a candidate pool, which handles the
`not_settleable` trap structurally rather than by a filter applied later.

**Stage 2 — Control-equation engine.** This is where the arithmetic lives, and
it is all deterministic:

```
per detail line:   net_effect = gross_effect − fee − tax
per summary:       net_amount = gross_payment − refund − fee − tax
roll-up:           summary totals == sum over UNIQUE detail_id lines
bank tie-out:      credit_amount == net_amount
fee:               fee = round_half_up(amount × fee_rate_bps / 10000)
tax:               tax = round_half_up(fee    × gst_rate_bps / 10000)
```

Anything that does not close leaves a **residual delta** — a specific number of
paise that is unexplained. Reconciliation from here is the job of explaining
deltas, not of finding partners.

**Stage 3 — Corroboration.** Given a delta, find records that could explain it.
A candidate is admissible only if it passes all nine gates: unconsumed; amount
exact in integer paise; date inside the recovery window; currency matching; sign
consistent with the line type; `txn_id` lineage intact; control equations still
satisfied afterwards; no other case forced into infeasibility; and **no second
candidate satisfies the first eight**.

The infeasibility gate is the only global one, and it is decided by forcing the
pairing rather than by ranking it: take the candidate edge, remove both of its
endpoints, re-solve the residual assignment problem, and keep the candidate only
if the residual still reaches the baseline matching size minus one. That asks
whether *some* consistent global assignment uses this pairing. Whether it is the
*only* one is gate 9's job, and splitting the two is what keeps "could explain
this line" from being reported as "explains this line".

That last gate is the one that matters. **Ties are never broken** — not by
frequency, not by a prior, not by batch history, not by model plausibility.
Where two candidates survive, the agent abstains.

**Stage 4 — Adjudicator.** Described in §6.

**Stage 5 — Exception classifier.** Described in §7.

**Stage 6 — Audit trail.** Every decision from every stage records what it saw,
which rule fired, what it concluded, and with what confidence — and every one is
human-overridable, with metrics re-runnable under the overrides.

---

## 5. Why the deterministic core is allowed to be boring

A fair objection: stages 1, 2 and 3 are joins, subtraction and a filter. A
competent engineer writes them in SQL.

That is true and it is the point. The measured floor says so out loud: B1 solves
every scenario except the refund-attribution axis, and 0% of that axis. So the
gap between a SQL script and this agent is exactly one capability — attributing
an anonymous refund line, or proving that it cannot be attributed and declining.

Stating that concentration is not a weakness in the writeup, it is the defence
against the obvious attack. And the two halves of that gap are genuinely
different skills:

- `CORROBORATED_REFUND` and `CONTESTED_REFUND` reward **proving** a unique
  allocation.
- `AMBIGUOUS_REFUND` rewards **refusing** to allocate.

An agent that solves the first by guessing scores zero on the second. That is
the whole design, compressed into one sentence.

---

## 6. Where the model fits

The model never computes. It is a **tool-using agent** whose tools are the
deterministic engine:

```
get_candidates(delta, window)     -> shortlist of admissible records
verify_allocation(event, settle)  -> bool   # runs all nine gates, integer paise
check_control_equations(case)     -> which equations close, which do not
explain(case)                     -> natural-language reasoning for a human
```

It cannot compute a sum. It can only ask whether one holds. That makes
**"no LLM arithmetic" structural rather than aspirational** — the model proposes
which record to test, and the engine alone decides whether the test passes.

### Division of labour

| The model does | The model may never |
|---|---|
| Propose which candidate to test | Compute or compare amounts |
| Read evidence a rule cannot express | Assert a match without tool verification |
| Write the operator-facing explanation | Break a tie |
| Decline, and say why | Overrule an abstention |

**Tiered routing**, because capability should be spent where a false match is
expensive: a cheap model for exception classification and explanation writing, a
frontier model only for genuine ties. Cost per 500 records is reported.

---

## 7. The exception list is a feature

The instinct is to treat unmatched rows as a failure to hide. In finance ops it
is the opposite: an exception list is the deliverable, because it is the queue a
human works. So every unresolved record gets a **named category**, the evidence
behind it, a confidence, and a recommended action — never a bare total.

The subtlety that separates a real exception list from a padded one: cases whose
correct answer is **no action at all**. `failed` and `created` gateway rows were
never going to settle. An agent that lists them has generated false positives
and inflated its own apparent thoroughness, so the `NO_ACTION` false-positive
rate is measured explicitly rather than assumed to be zero.

---

## 8. What gets measured

Three systems are scored, so each layer's contribution is visible:

| System | What it establishes |
|---|---|
| **B1** — exact joins only | what a plain script already solves |
| **B2** — B1 + amount lookup | the *honest* adversary; the number to quote against |
| Full system | what corroboration and the model add, and what they cost |

Publishing B2 rather than B1 is deliberate. B1 flatters the result; B2 is the
attack a reviewer would actually run, and running it on myself is cheaper than
having it run on me.

Reported: match rate; precision and recall at the **allocation** level, so seven
of eight legs scores 7/8 rather than 0; **false-match rate, explicitly**;
per-scenario breakdown; `NO_ACTION` false-positive rate; exception precision and
recall per category; throughput; model call rate; **cost per 500 records**; and
precision as a **curve** across abstention thresholds rather than a single point,
because coverage and correctness trade off and the operator chooses where to sit.

**No figure appears in this document that was not produced by a command in the
repository.** The README table is generated by `make stats` and is not editable
by hand — it went stale twice when it was typed.

**Holdout runs exactly once, at the end.** All tuning happens on the development
family. If a number disappoints, it gets reported, not re-tuned.

---

## 9. The honest limitation

> The held-out set is a different seed from the same generator. It measures
> whether the tolerances were overfitted. It does **not** measure robustness to
> real bank data, because the same code wrote both the defects and their labels.

Say this before anyone asks. Claiming more is the trap.

The same honesty applies to §3.2. That defect was found by attacking my own
benchmark, which is the right process — but it had been present, unnoticed,
through a version I considered complete. The safeguard is that the attack is now
a test that runs on every seed, not a thing I remembered to do once.
