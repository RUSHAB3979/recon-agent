# Multi-source Reconciliation Agent

[![CI](https://github.com/RUSHAB3979/recon-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RUSHAB3979/recon-agent/actions/workflows/ci.yml)

Reconcile a payment gateway ledger, settlement reports, and a bank statement.
The agent checks amounts, fees, taxes, and refund allocations, then produces
an exception queue with evidence and a recommended next step for each item.

Built for the **Razorpay AI Buildathon · AI Finance Controller track**.

## What it does

- Checks settlement totals against gateway events and bank credits.
- Detects missing or duplicate credits, fee/tax differences, and captured
  payments that have not settled.
- Recovers anonymous refund allocations using amount, timing, payment lineage,
  and consistency across the batch. Unresolved ties go to review.
- Optionally uses a language model to interpret settlement notes when the
  accounting checks leave multiple valid candidates.
- Exports an exception CSV, a standalone HTML report, and a hash-chained
  decision journal.

This is a Python prototype with a synthetic benchmark. It runs from CSV files;
it does not connect to a live gateway or bank account.

## Run it

Use Python 3.13, the version tested in CI. The sample datasets are included.

### macOS / Linux

```bash
git clone https://github.com/RUSHAB3979/recon-agent.git
cd recon-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH=src
python -m recon.metrics.report data/primary
python -m recon.metrics.dashboard data/dev data/primary data/stress --out runs/demo/index.html
```

### Windows PowerShell

```powershell
git clone https://github.com/RUSHAB3979/recon-agent.git
cd recon-agent
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m recon.metrics.report data/primary
.\.venv\Scripts\python.exe -m recon.metrics.dashboard data/dev data/primary data/stress --out runs/demo/index.html
Start-Process runs/demo/index.html
```

Open `runs/demo/index.html` in a browser. The report needs no server or API key.

With the environment above configured, these commands produce the other outputs
(on Windows, use `.\.venv\Scripts\python.exe` in place of `python`):

```bash
python -m recon.match.exceptions data/primary --out-dir runs
python -m recon.match.controller data/primary --audit-dir runs
python -m recon.match.audit runs
python -m pytest tests/ -q
```

If GNU Make is installed, `make verify` runs the tests, reports, audit checks,
exception export, demo build, and dataset statistics. `make data` regenerates
the three sample datasets. See [the demo runbook](docs/PITCH.md) for presenting
the project and [the video script](docs/VIDEO_SCRIPT.md) for recording it.

## Results

Each batch contains 500 gateway events grouped into 100 reconciliation cases.
Correct cases include reconciled payments, correctly identified exceptions,
correct abstentions, and payments requiring no action. **94/100 is case
accuracy, not the percentage of payments automatically reconciled.**

| System / metric | Development | Primary | Stress |
|---|---:|---:|---:|
| B1: exact joins and accounting checks | 58/100 | 76/100 | 60/100 |
| B2: B1 plus amount-based refund lookup | 80/100 | 89/100 | 85/100 |
| B3: B2 plus fuzzy text matching | 79/100 | 90/100 | 82/100 |
| Deterministic agent | 90/100 | 94/100 | 90/100 |
| Agent false-match rate | 0.00% | 0.00% | 0.00% |
| Agent allocation precision | 1.0000 | 1.0000 | 1.0000 |
| B2 incorrect allocations | 26 | 16 | 20 |

Run `make report` for the agent, B1, and B2 comparison; `make baseline` also
reports B3. Scoring checks the event-to-settlement allocations as well as the
case outcome, so assigning a refund to the wrong payment cannot earn credit.

A recorded live-model run on the development batch improved case accuracy from
90/100 to 96/100, with 15 accepted allocations and no incorrect allocations.
That was one model on one batch, separate from the deterministic results above.
The model, protocol, failures, and limits are in [the experiment record](docs/LIVE_MODEL.md).

## How it works

```text
Gateway ledger + settlement detail/summary + bank statement + pricing rules
    -> Normalize amounts and dates
    -> Check accounting equations
    -> Match references and recover refunds
    -> Optionally read notes for unresolved candidates
    -> Classify exceptions
    -> Export report, review queue, and decision journal
```

Money is stored as integer paise. A deterministic refund match must pass nine
checks, including exact amount, settlement window, currency, parent-payment
lineage, global allocation feasibility, and uniqueness.

The optional model receives a settlement note and a closed list of candidates.
It cannot add candidates or alter accounting checks. Its accepted allocations
are marked `SUGGESTED`, with the reader name and confidence recorded. A model
can still choose the wrong valid candidate; the benchmark measures that risk.

To enable it, set `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`, then run:

```bash
python -m recon.match.controller data/dev --adjudicate --min-confidence 0.70
```

`OPENROUTER_MODEL` selects an OpenRouter model. Anthropic takes precedence if
both keys are set. Without a key, the reader declines unresolved lines and
preserves the deterministic result. [Architecture and design decisions](SOLUTION.md).

## Data and limitations

The committed batches cover twelve scenarios, including delayed refunds,
duplicate exports, bank-credit failures, and ambiguous refund notes. The
[data specification](docs/DATA_SPEC.md) describes every file and scenario.

- All measurements use synthetic data. Real bank formats and merchant workflows
  have not been validated.
- Five additional seeds each scored 94/100. Their fixed scenario mix includes
  six cases the deterministic engine cannot resolve, so the repeated score
  does not establish real-world generalization. [Holdout record](docs/HOLDOUT.md).
- The language-model result is a small, single-run experiment. A rate-limited
  primary attempt preserved the deterministic result but did not establish
  primary-batch model accuracy.
- The audit chain detects changes only when its final hash is retained in a
  trusted location. It is not an immutable external ledger.

<details>
<summary>Generated dataset statistics and scenario counts</summary>

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

</details>

## Repository

| Path | Contents |
|---|---|
| `src/recon/datagen/` | Synthetic data generator and scenario definitions |
| `src/recon/match/` | Normalization, accounting checks, matching, model readers, and audit |
| `src/recon/metrics/` | Shared scorer, baselines, and HTML report |
| `data/` | Development, primary, and stress batches with answer keys |
| `tests/` | Data integrity, matching, reporting, and robustness tests |
| `tools/` | Demo driver, holdout runner, and statistics scripts |
| `docs/` | Data specification, experiment records, and demo instructions |

CI runs the test suite, checks byte-for-byte dataset regeneration, verifies
the audit chain, and builds the report as a downloadable artifact.
[Robustness and performance fixes](docs/ADVERSARIAL_REVIEW.md) document the
input-handling and scaling issues found during development.

## License

[MIT](LICENSE)
