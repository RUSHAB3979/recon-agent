# Five-minute pitch script

Aim for roughly 4:30–4:50 after a practice recording. Leave time to show the
exception row and dashboard. Use [the demo runbook](PITCH.md) to capture the
terminal output, then record the narration below at a comfortable pace.

## 0:00 — Problem

> When a merchant receives a payment, the gateway ledger, settlement report,
> and bank statement each tell part of the story. They do not always agree.
> Refunds can arrive in a later cycle, fees can differ, and a settlement can
> be missing from the bank statement.
>
> Our project is the Multi-source Reconciliation Agent, built for the
> AI Finance Controller track. It checks these records together and produces
> a review queue for the cases that need attention.

## 0:40 — Approach

Show the pipeline in the README.

> The accounting runs in Python, with all money stored as integer paise.
> We check amounts, fees, taxes, settlement totals, and bank credits before
> trying to explain a mismatch.
>
> Refunds are the harder part. Several refund events can have the same amount.
> We check their dates, parent payments, and whether each allocation fits the
> rest of the batch. If the evidence still leaves multiple candidates, the
> deterministic engine leaves the case open.

## 1:25 — Results

Show the primary comparison from the demo driver.

> On the primary synthetic batch, the agent gets 94 out of 100 cases correct,
> with no false matches. Case accuracy includes identifying exceptions and
> knowing when no action is needed; it does not mean 94 percent of payments
> were automatically reconciled.
>
> The amount-lookup baseline gets 89 cases correct and makes 16 incorrect
> allocations. That difference matters because the wrong refund can still
> make a total add up. Our scorer checks which event was assigned, as well
> as whether the case was marked reconciled.

## 2:10 — Exception queue

Show one exception row, then the dashboard.

> Here is what an operator gets: the category, the records involved, the
> evidence, and a recommended next step. Missing credits, duplicate credits,
> fee differences, and ambiguous refunds are visible separately.
>
> We also keep accounting discrepancies separate from unattributed refund
> value. Not knowing which refund explains a line does not mean that money
> is missing.
>
> The report opens as a standalone HTML file. Decisions are recorded in a
> hash-chained journal, including the rule or model that produced them.

## 2:55 — AI contribution

Show the development experiment table in `docs/LIVE_MODEL.md`.

> The optional language model reads settlement notes only after the accounting
> checks leave multiple valid candidates. It receives a closed list, can
> decline, and cannot change the accounting rules.
>
> In one live development run, it improved case accuracy from 90 to 96 out
> of 100. It accepted 15 allocations, all correct against the answer key.
> We record those as model suggestions with confidence, because restricting
> the choices does not guarantee the model reads the note correctly.

## 3:40 — Challenges and limits

> During development, amount lookup solved an early benchmark completely.
> That pushed us to add cases where equal amounts point to different possible
> refunds and to score the actual allocations.
>
> Larger batches also exposed repeated full-batch work. Building indexes once
> and solving independent graph components reduced the recorded runtime of
> a 20,000-event batch from about 32 seconds to under one second.
>
> All of these results use synthetic data. The model result is one run, and
> real bank formats and merchant workflows still need validation. Five
> additional seeds also scored 94, but their fixed scenario mix explains
> that consistency.

## 4:30 — Close

> The prototype gives a finance operator checked allocations, a reason for
> each unresolved case, and a record of how the decision was made. The code,
> sample data, baselines, and tests are available in the repository.

## Before submitting

- Check the final video is under five minutes and the terminal text is legible.
- Keep API keys and unrelated windows out of the recording.
- Match spoken figures to the recorded output.
- Open the shared video link in a signed-out window to verify access.
