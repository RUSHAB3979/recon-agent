# Architecture and design decisions

## Problem

A gateway event, a settlement line, and a bank credit describe different parts
of a payment's lifecycle. Joining their identifiers does not establish whether
the amount is right, the refund belongs to that payment, or the settlement
actually reached the bank.

The project reconciles these sources and gives an operator a list of cases
that need investigation. It is a CSV-based prototype evaluated on synthetic
data; there is no live payment or banking integration.

## Pipeline

| Stage | Implementation | Responsibility |
|---|---|---|
| Normalize | `src/recon/match/normalize.py` | Parse amounts and dates; identify settleable events |
| Controls | `src/recon/match/controls.py` | Check line amounts, totals, fees, taxes, and bank credits |
| Matching | `src/recon/match/passes.py`, `recovery.py` | Join references and allocate anonymous refunds |
| Evidence reader | `src/recon/match/adjudicator.py` | Interpret notes for remaining candidates, when enabled |
| Case decisions | `src/recon/match/controller.py` | Assign outcomes, categories, evidence, and confidence |
| Outputs | `exceptions.py`, `journal.py`, `src/recon/metrics/dashboard.py` | Produce a review queue, audit journal, and report |

Amounts remain integer paise throughout the pipeline. Decimal arithmetic and
half-up rounding are used where fees and taxes are constructed.

```text
detail net       = gross effect - fee - tax
settlement net   = gross payments - refunds - fees - tax
summary totals  == sum of unique detail lines
bank credit     == settlement net
fee              = round_half_up(amount * fee_rate_bps / 10000)
tax              = round_half_up(fee * gst_rate_bps / 10000)
```

`CREATED` and `FAILED` payments require no settlement action. Duplicate exports
are deduplicated by detail ID before totals are calculated.

## Refund recovery

An anonymous refund can have several events with the same amount. The recovery
pass filters candidates through these checks:

1. The event has not already been allocated.
2. Its amount matches the delta exactly.
3. Its date is inside the recovery window.
4. The currency agrees.
5. Its parent payment exists and settled.
6. Its event type and sign agree with the line.
7. The accounting controls remain satisfied.
8. The pairing is feasible alongside the other allocations.
9. Only one feasible candidate remains.

For the feasibility check, the solver forces a candidate pairing, removes its
endpoints, and verifies that the remaining graph still supports the required
matching size. It solves only the affected connected component, since the
other components cannot be changed by that pairing.

The deterministic pass abstains when more than one candidate survives. The
runner also rejects attempts to consume an event twice.

## Optional note interpretation

The `EvidenceReader` protocol has declining, Anthropic, OpenRouter, and scripted
implementations. Scripted readers are used in tests. The default run needs no
model; `--adjudicate` enables the additional pass.

Only unresolved lines reach this pass. The request contains the settlement
note and candidate labels with the parent-payment descriptions. Amounts and
dates are omitted because the accounting checks already tested them.

The model can select a listed candidate or decline. An unknown label or
confidence below the configured threshold is rejected. Accepted claims are
marked `SUGGESTED` and retain their reader, confidence, and explanation. Case
confidence is the minimum confidence among the claims supporting it.

These constraints restrict the model's choices; they do not prove its reading
is correct. Incorrect allocations are measured by the scorer. Failed requests
and unavailable readers leave the affected lines unresolved.

## Exception queue and audit

The exception CSV includes the category, source records, evidence, and a
recommended action. It separates control-break exposure from the value of
unattributed refunds: an unallocated refund is not automatically missing money.
`NO_ACTION` cases and resolved duplicate-export warnings are excluded.

The journal records attributions, abstentions, rejected claims, and case
decisions. Each record is sealed with SHA-256 and the previous record's hash.
The audit module supports overrides, but a duplicate-consumption rejection
cannot be overridden into allocating one event twice. There is no operator
override UI in the demo.

The chain is verifiable against a trusted final hash. Someone who can rewrite
both the journal and that hash can replace the chain.

## Benchmark design

An early version was fully solvable by an amount lookup. Adding a more capable
baseline exposed that weakness. The dataset now includes same-amount candidates
that fail specific checks, cases that remain ambiguous, and cases whose notes
provide enough evidence to distinguish them.

The scorer validates event-to-settlement allocations as well as case outcomes.
Otherwise, a wrong refund allocation labelled `RECONCILED` could receive credit.
The same scorer evaluates the agent and the baseline allocations.

The generated note scenarios deliberately avoid direct product-name overlap.
This tests semantic interpretation, not how frequently it is needed in real
operations. A maintained product-to-category map could also solve these cases.

See the [README results](README.md#results), [dataset specification](docs/DATA_SPEC.md),
[holdout record](docs/HOLDOUT.md), [live-model experiment](docs/LIVE_MODEL.md),
and [robustness review](docs/ADVERSARIAL_REVIEW.md).
