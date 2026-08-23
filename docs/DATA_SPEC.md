# Reconciliation benchmark data

This dataset publishes identification keys in the clear and measures what an
exact join cannot answer: whether settlement controls hold, whether lifecycle
state requires action, and whether an unexplained refund has one admissible
source or several. Scenario prevalence is counted by **case**, not by exported
row. Every case contains 3–7 gateway event rows, so a 500-event batch contains
100 cases.

## Dataset families

| Family | Published use | Scenario shares |
|---|---|---|
| `development` | five 500-event seeds used to freeze thresholds | development |
| `primary` | one 500-event headline, throughput, and demo batch | primary |
| `stress` | ten pooled 500-event exception-enriched batches | stress |

Development is the only family on which thresholds may be tuned. Primary and
stress results are reported separately; averaging them would mix realistic and
enriched prevalence.

## Files

Each run emits six input files, two answer-key files, and metadata.

### `batch_config.csv`

| Column | Type |
|---|---|
| `batch_id` | string |
| `seed` | integer |
| `family` | `development`, `primary`, or `stress` |
| `n_gateway_events` | integer |
| `n_cases` | integer |
| `generated_at` | ISO 8601 timestamp |

`generated_at` is a deterministic batch timestamp. It is not wall-clock time,
because identical `(config, seed)` inputs must produce byte-identical files.

### `pricing_rules.csv`

| Column | Type | Meaning |
|---|---|---|
| `method` | string | `upi`, `card`, `netbanking`, `wallet`, or `emi` |
| `fee_rate_bps` | integer | basis points of event amount |
| `gst_rate_bps` | integer | `1800` |

### `gateway_ledger.csv`

The ledger is one row per **event**, not one row per transaction. A refund is a
positive-valued `REFUND` row sharing the payment's `txn_id` and `order_id`.

| Column | Type |
|---|---|
| `event_id` | string, primary key |
| `event_type` | `PAYMENT` or `REFUND` |
| `txn_id` | string |
| `order_id` | string |
| `amount_paise` | positive integer |
| `currency` | `INR` |
| `status` | `CREATED`, `FAILED`, `CAPTURED`, or `PROCESSED` |
| `created_at` | ISO 8601 timestamp |
| `method` | string |

`CREATED` and `FAILED` do not settle. `CAPTURED` should settle but has not.
`PROCESSED` has settled, including refunds represented by anonymous settlement
detail lines in the three refund-recovery scenarios.

### `settlement_detail.csv`

| Column | Type | Meaning |
|---|---|---|
| `detail_id` | string | economic line key; deliberately repeats only in `DUPLICATE_DETAIL_EXPORT` |
| `settlement_id` | string |
| `event_id` | string or empty | empty on anonymous refund lines |
| `line_type` | `PAYMENT` or `REFUND` |
| `gross_effect_paise` | signed integer | refunds are negative |
| `fee_paise` | integer |
| `tax_paise` | integer |
| `net_effect_paise` | integer |
| `settled_at` | ISO 8601 timestamp |
| `currency` | `INR` |
| `reference_text` | string or empty |

### `settlement_summary.csv`

| Column | Type |
|---|---|
| `settlement_id` | string, primary key |
| `utr` | string |
| `settlement_date` | ISO date |
| `gross_payment_paise` | integer |
| `refund_paise` | integer |
| `fee_paise` | integer |
| `tax_paise` | integer |
| `net_amount_paise` | integer |
| `line_count` | integer |
| `currency` | `INR` |
| `status` | string |

### `bank_statement.csv`

| Column | Type |
|---|---|
| `bank_row_id` | string, primary key |
| `utr` | string |
| `posted_at` | ISO 8601 timestamp |
| `credit_amount_paise` | integer |
| `currency` | `INR` |
| `narration` | string |
| `bank_ref` | string |

Narration has one format:

```text
NEFT CR: {bank} {utr} RAZORPAY SETTLEMENT
```

It never carries an order reference. The UTR is also present in its own column,
so bank-to-summary identification is an exact join rather than a fuzzy task.

### `answer_key_cases.csv`

| Column | Type |
|---|---|
| `case_id` | string |
| `scenario` | scenario name below |
| `expected_outcome` | `RECONCILED`, `EXCEPTION`, `NO_ACTION`, or `ABSTAIN` |
| `settlement_ids` | pipe-joined strings |
| `bank_row_ids` | pipe-joined strings |
| `event_ids` | pipe-joined strings |
| `expected_exception_category` | string or empty |
| `notes` | string |

The duplicate-detail warning is recorded as
`DUPLICATE_DETAIL_EXPORT_WARNING` even though the expected outcome remains
`RECONCILED`. Exception scenarios use their scenario name as the category.

### `answer_key_allocations.csv`

Atomic correspondences have columns `event_id, settlement_id, bank_row_id`.
They provide partial-credit scoring at event-leg granularity. A blank
`bank_row_id` means the event is allocated to an existing settlement whose bank
credit is deliberately missing. Ambiguous refund candidates receive no chosen
allocation, because choosing one would manufacture truth that the evidence does
not contain. A contested refund allocates its one admissible candidate; its
distractor refunds do not appear in this file, including a gate-1 distractor
whose visible support-settlement line exists only to prove prior consumption.
Visible non-distractor legs in the same case remain allocated and scoreable.

### `dataset_meta.json`

Metadata records the seed, family, artifact row counts, expected-outcome counts,
and achieved `scenario_case_counts`. That last value makes the published shares
auditable without inferring cases from row counts.

## Exact money and controls

Every emitted money column ends in `_paise` and contains an integer. Decimal is
used only to turn the unchanged price-point catalogue into paise at construction;
no float ever represents or participates in a money value. Fee and tax use the
single integer helper `round_half_up(numerator, denominator)`.

The following controls hold exactly unless the named scenario deliberately
targets the control:

```text
detail:   net_effect_paise = gross_effect_paise - fee_paise - tax_paise

summary:  net_amount_paise = gross_payment_paise - refund_paise
                             - fee_paise - tax_paise

roll-up:  summary totals = totals over UNIQUE detail_id rows

bank:     sum(credit_amount_paise for rows with the summary UTR)
          = net_amount_paise

fee:      fee_paise = round_half_up(amount_paise * fee_rate_bps, 10000)

tax:      tax_paise = round_half_up(fee_paise * gst_rate_bps, 10000)
```

`BANK_CREDIT_MISSING` makes the settlement-level bank sum zero;
`BANK_CREDIT_DUPLICATE` makes it twice the summary amount; and
`FEE_TAX_VARIANCE` breaks exactly one of the fee or tax equations while detail,
summary, roll-up, and bank arithmetic continue to agree. Duplicate detail rows
break a naive all-row sum, but the required unique-`detail_id` roll-up still
ties.

## Scenario shares (by case)

| Scenario | Development | Primary | Stress | Expected outcome |
|---|---:|---:|---:|---|
| `STRAIGHT_THROUGH` | 26% | 48% | 20% | `RECONCILED` |
| `CONTESTED_REFUND` | 14% | 8% | 14% | `RECONCILED` |
| `REFUND_LATER_CYCLE` | 9% | 10% | 10% | `RECONCILED` |
| `DUPLICATE_DETAIL_EXPORT` | 9% | 8% | 10% | `RECONCILED` with warning |
| `CORROBORATED_REFUND` | 10% | 6% | 10% | `RECONCILED` |
| `FEE_TAX_VARIANCE` | 6% | 4% | 8% | `EXCEPTION` |
| `CAPTURED_UNSETTLED` | 6% | 6% | 7% | `EXCEPTION` |
| `BANK_CREDIT_MISSING` | 4% | — | 7% | `EXCEPTION` |
| `AMBIGUOUS_REFUND` | 8% | 4% | 6% | `ABSTAIN` |
| `BANK_CREDIT_DUPLICATE` | 3% | — | 5% | `EXCEPTION` |
| `NOT_SETTLEABLE` | 5% | 6% | 3% | `NO_ACTION` |

Shares are apportioned deterministically by largest remainder. At exactly 100
cases the achieved counts equal the percentages above; smaller batches record
their achieved integer counts in metadata.

`REFUND_LATER_CYCLE` emits the payment in one settlement and its refund event in
a later settlement. `CORROBORATED_REFUND` uses an anonymous refund detail line
whose settlement delta has exactly one amount candidate. `CONTESTED_REFUND`
creates two to four exact-amount refund candidates, exactly one of which remains
after consumption (gate 1), the four-day settlement window (gate 3), and settled
parent lineage (gate 5) are applied. `AMBIGUOUS_REFUND` pairs deltas and
candidates so at least two global allocations still survive those gates; no
frequency or prior breaks the tie.

Contested-case notes record every distractor as
`event_id:GATE_1_CONSUMED`, `event_id:GATE_3_TOO_OLD`,
`event_id:GATE_3_AFTER_SETTLEMENT`, or
`event_id:GATE_5_BROKEN_LINEAGE`. Gate-3 construction deliberately exercises
both sides of the window. A gate-5 CAPTURED parent is support evidence owned by
the contested case, appears in no detail line, and is not a separately labelled
`CAPTURED_UNSETTLED` case.

## Measuring ambiguity

`python tools/ambiguity.py [data_dir]` deduplicates detail rows and derives each
settlement's anonymous refund delta. It reports one candidate histogram using
amount alone and a second after gates 1, 3, and 5, plus the total number of
candidates those gates eliminate. The legacy `candidate_multiplicities`,
`ambiguous`, and `ambiguity_rate` values remain the after-gates view so existing
statistics tooling keeps its meaning.

## Honest limitation

The held-out set is a different seed from the same generator. It measures
whether tolerances were overfitted. It does **not** measure robustness to real
bank data, because the same code writes both the defects and their labels.
