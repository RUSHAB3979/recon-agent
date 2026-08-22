# Synthetic dataset & answer key

Everything downstream is scored against the answer key, so the answer key being
correct is a precondition for any number in the README meaning anything. This
document describes what is generated, what is guaranteed about it, and where it
knowingly departs from the original plan.

## Files per dataset

| File | Contents |
|---|---|
| `gateway_ledger.csv` | Source A — payment gateway records, shuffled |
| `bank_settlement.csv` | Source B — bank statement credits, shuffled |
| `answer_key_links.csv` | Semantic ground truth: one row per relationship |
| `answer_key_pairs.csv` | Atomic ground truth: one row per `(txn_id, utr)` |
| `dataset_meta.json` | Seed, counts, scenario mix — the reproducibility record |

Two answer-key views exist because they answer different questions. **Pairs**
give honest partial credit on batch matches when computing precision and recall
— getting 7 of 8 legs of a batch right should score 7/8, not 0. **Links** are
what exception classification and duplicate detection are scored on. Reporting
only one of them would let a weak result hide behind the other.

## Schemas

**Gateway ledger** — `txn_id, order_id, amount, currency, status, created_at,
method, fee, tax, net_amount, refund_amount`

**Bank settlement** — `utr, settlement_date, credit_amount, narration, bank_ref`

Note what the bank side does *not* carry: no `txn_id`, no `order_id`, no
currency, no fee breakdown. A real statement gives you a UTR, a date, an INR
amount and a free-text narration. The narration is the only bridge back to the
gateway — which is precisely why garbled narrations are the case where an LLM
earns its place, and why everything else should be deterministic.

### Deviation from the original spec

`refund_amount` was added to the gateway schema. The original field list had no
refund column, which would have made every partial-refund case arithmetically
unsolvable by any method — the matcher would have had to guess the refund
amount. That is not a test of anything; it just depresses recall. With the
column present, partial refunds test multi-column arithmetic
(`net_amount - refund_amount`), which is a real capability. This is the only
departure from the planned schema.

## Scenario classes

Each generated case is labelled with exactly one scenario and one expected
resolution. The scenario says what was wrong; the resolution says what a correct
agent should have *done*. Scoring uses the resolution.

| Scenario | Expected resolution | Capability tested |
|---|---|---|
| `clean_1to1` | `auto_match` | Baseline |
| `date_offset` | `auto_match` | Time-window matching (T+0..T+4) |
| `fee_deduction` | `auto_match` | Fee-aware matching (gross vs net basis) |
| `rounding` | `auto_match` | Tolerance handling (±₹0.01–0.05) |
| `many_to_one` | `auto_match` | Aggregation / batch settlement |
| `partial_refund` | `auto_match` | Multi-column arithmetic |
| `fx_settlement` | `auto_match` | Currency normalisation |
| `garbled_narration` | `auto_match` | Fuzzy/LLM reference recovery |
| `missing_on_bank` | `exception:unsettled` | True exception |
| `missing_on_gateway` | `exception:unexplained_credit` | True exception |
| `duplicate_settlement` | `exception:duplicate_settlement` | Duplicate detection |
| `not_settleable` | `no_action` | **Trap** — must NOT be reported |

`not_settleable` is deliberate. Those are `failed` / `created` gateway rows that
were never going to settle. Reporting one as an unmatched exception is a false
positive, not a find, and an agent that pads its exception list with them should
be penalised for it.

The three true-exception classes are over-weighted relative to real-world
frequency. Per-category precision computed over 5 instances moves in 20% steps,
which is too coarse to report honestly; the weights put ~15–20 cases in each
exception bucket at 500 records. This is a stated property of the dataset, not a
claim about production traffic.

## Guarantees (enforced by `make test`, not assumed)

The self-validation suite runs across four seeds and asserts:

- Every `txn_id` and `utr` in the key exists in the source files
- Every gateway row and every bank row belongs to **exactly one** link
- Truth pairs are exactly the cross-product of each link's txns × credits
- `net_amount == amount - fee - tax` for every row, exact to the paisa
- Every link's `actual_inr_total` equals the sum of the credits it cites
- Per-scenario invariants — e.g. a `fee_deduction` case genuinely does *not*
  match on `net_amount`; a batch narration genuinely carries no per-txn
  reference; an FX case genuinely does not settle at face value
- Where the key says a narration's reference was **destroyed**, no prefix of ≥4
  characters survives anywhere in the string
- Same seed → byte-identical output; different seeds → disjoint identifiers
- Row order carries no matching signal (Spearman ρ < 0.2 between file positions)

That last-but-one check exists because a labelling bug got through once. An
earlier version marked short truncations as "reference destroyed" when 6–7
characters of a 14-character reference were still sitting in the narration —
recoverable by prefix lookup. It survived the first test run because no such
case happened to occur in seed 42. The fixture is now parameterised over four
seeds, and the check re-derives recoverability from the emitted string instead
of trusting the label.

## Money

`Decimal` throughout, quantised to 2dp at construction, serialised with exactly
two decimal places. Floats are never used for currency. A reconciliation engine
that reports a ₹0.01 tolerance breach caused by its own binary-float error is
worse than useless.

## Intrinsic ambiguity floor

`make ambiguity` reports how many 1:1 settled credits have more than one
indistinguishable gateway candidate on amount + date alone:

```
data/dev      30 / 385 ambiguous  (7.8%)  -> deterministic ceiling 92.2%
data/holdout  37 / 421 ambiguous  (8.8%)  -> deterministic ceiling 91.2%
```

(These move whenever the amount distribution or defect weights move. `make
stats` regenerates the README table from the data on disk so the headline
figures can never disagree with `make verify`; the numbers quoted here are
illustrative of the order of magnitude.)

**This number is the point of the whole design.** Amounts are drawn bimodally:
45% snap to catalogue price points (₹99, ₹499, ₹1999…), the rest from continuous
bands. An earlier version drew amounts continuously and produced a 0.5%
ambiguity rate — meaning `(amount, date)` was very nearly a unique key and a
trivial matcher would score ~99% on a dataset that looked rigorous. Real payment
amounts pile up on price points, and that clustering is what creates the
collisions that force a matcher to use narration evidence.

Quote the ceiling next to the match rate. "97% match rate" and "97% match rate
against a 92.2% amount+date ceiling" are very different claims, and only the
second one survives a panel asking whether the data was trivially separable.

## Regenerating

```bash
make data                                    # dev (seed 42) + held-out (seed 20260905)
make verify                                  # self-validation + ambiguity floor
python -m recon.datagen.cli --records 2000 --seed 7 --out data/scale
```

A dataset is a pure function of `(GenConfig, seed)`. Tune anything in
`src/recon/datagen/config.py`; the answer key follows automatically because it
is constructed alongside the data, never inferred from it afterwards.

## Honest limitation

The held-out set is a different seed from the same generator. It measures
whether tolerances were overfitted; it does **not** measure robustness to real
bank data, because the same code wrote both the defects and their labels. Say
this before anyone asks — claiming more is the trap.
