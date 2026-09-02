# Pitch runbook

Four minutes, six screens, every number computed live on stage. Nothing here
is recorded, replayed or pre-baked — and saying so while the commands run is
part of the pitch.

- **Driver:** `./tools/pitch.sh` (Enter to advance), `--auto` to record,
  `--check` before you walk on.
- **Total runtime of the commands:** about four seconds for all seven, of
  which the holdout is one and a half. The talking is the slow part, which is
  the way round you want it.
- **Nothing on the critical path needs a network or an API key.** If the venue
  wifi dies, the demo does not.

---

## Before you go on

```bash
git pull
make data          # only if data/ is missing; the CSVs are committed
./tools/pitch.sh --check
```

Eleven checks, all must pass. Then leave a terminal open in the repo root, font
size up, window tall enough for 25 lines.

**Have `runs/demo/index.html` already open in a browser tab.** Screen 5 points
at it; you do not want to be opening a file manager on stage.

---

## The script

Stage directions in *italics*. Say the bold lines close to verbatim — they are
the ones that land.

### 0:00 — The problem (no screen, look at the room)

> When a customer pays a merchant online, the money doesn't go from their bank
> to the merchant's bank. It goes through a gateway, which batches a whole
> settlement cycle, takes its fee, nets off refunds, and sends **one** transfer.
> The bank writes **one line**.
>
> So the merchant has two independent records of the same money and has to prove
> they agree. Every cycle. By hand it's slow and it's error-prone.
>
> The obvious move is to hand it to an LLM. That's the wrong answer, and knowing
> why is the whole project. **A wrong match is worse than no match. An unmatched
> row gets a human to look at it. A confidently wrong one does not** — it becomes
> a RECONCILED line nobody opens again.

### 0:35 — Screen 1: the floor

*Run beat 1.*

> Every team today will tell you their accuracy. Almost nobody will tell you
> what a plain SQL script already scores on the same data.
>
> So we published ours, as runnable code. B1 is exact joins. B2 adds one rule —
> look up an unmatched refund by amount. B3 adds fuzzy string matching, because
> "you only needed string matching" is the obvious objection.
>
> **B2 scores 89 out of 100.** That's our floor, and we quote against it rather
> than against B1, because B1 flatters us.
>
> And look at the bottom line: string matching picks the right candidate 8 times
> out of 17, against 8.5 expected from a coin. **Negative lift.** It isn't doing
> badly — it has nothing to rank on.

### 1:15 — Screen 2: the money shot

*Run beat 2. Let it sit for a second before talking.*

> Same scorer for the baselines and for us — otherwise you're measuring the
> instruments.
>
> B2 gets 89. We get 94. But **read the false-match column, not the accuracy
> column.** B2 buys those 89 cases by guessing, and it books **16 wrong
> attributions** to do it. We book **zero**.
>
> That's the number nobody publishes, because in production you have no ground
> truth to compute it against. Synthetic data is the only reason this column can
> exist — which turns our biggest limitation into the reason the metric is here.

### 2:00 — Screen 3 + 3b: abstention, priced

*Run beat 3, then 3b.*

> On ten cases the agent stops. Two refund events are arithmetically valid for
> the same line, and nothing separates them — so it declines.
>
> That's not a failure, **that's the product.** Oracle's reconciliation engine
> breaks that tie by lowest transaction ID — which is file order. We make
> abstention the default and then measure what it costs.
>
> And the exception list isn't a count. *[beat 3b]* Every item has the money
> behind it, the evidence, and what to do: two candidates survived gates 1
> through 8, gate 9 needs exactly one, so a human decides. **Four hundred
> thousand rupees, on somebody's desk, with the reason attached.**

### 2:50 — Screen 4: the holdout

*Run beat 4.*

> Anyone can tune until the number looks good. So we pre-registered this:
> the protocol went in **one commit**, the run in the **next**. You can check
> the ordering in the log, and `git diff` between them touches no source file.
>
> Five seeds nothing in the repo had ever generated. Same 94. Zero false
> matches.
>
> And the honest part: **that constancy is structural, not stability.** The
> scenario mix is a fixed partition, so 94 is 100 minus 6 by construction on any
> seed. What actually moved is the floor — D swung from 8% to 13% while we
> didn't move at all. **All the variance sat in the guesser and none in the
> prover.**

### 3:25 — Screen 5: the deliverable

*Run beat 5, then switch to the browser tab.*

> One file. No server, no JavaScript, no network. It opens from disk, prints to
> PDF, and attaches to an email — because a reconciliation result is something
> you hand to somebody, and a demo that needs a running process is broken the
> day after the deadline.
>
> Scoreboard, per-gate eliminations, the abstention curve, the queue, and the
> audit chain head. Every figure computed by the run that wrote the page.

### 3:50 — Close

> The deterministic core does all the arithmetic. The model reads one sentence
> and answers with one letter — and it **cannot** compute a number, because
> there are no numbers in its input. Every candidate matched the delta exactly,
> so amounts and dates are left out of the prompt entirely.
>
> That's not a policy we promise. It's the architecture.

---

## Questions you will get, and the honest answer

**"Where's the AI? This looks deterministic."**
> Deliberately. By the time a line reaches the model, nine gates have verified
> the amount to the paise, the window, the currency, the lineage, the controls
> and global feasibility. A tool call could only return what the gates already
> proved — that's theatre with latency. The model does the one thing the gates
> can't: read an ops note and say which product it's about. It's off by default
> and contributes to no number on that screen.

**"So you've never run it against a real model?"**
> We have now, once, on dev — and I'd rather give you the sample size than the
> headline. It read 28 lines, claimed 15, and the answer key agreed with 15 of
> 15. Dev goes 90 to 96, and the false-match column stays on zero. That's a
> 2.6-billion-parameter free model, so the cost per batch is nil.
>
> What I still don't have is a number on the primary set — the free tier's daily
> cap cut it off. And fifteen-for-fifteen is fifteen. It's a real result on a
> small sample, not a claim about models.
>
> The throttled run was worth more than the number would have been, though: with
> the model unreachable on fifteen of sixteen lines, primary came back at exactly
> the deterministic 94 and 0.00% false match. Every line the model didn't reach
> became an exception with a reason. **We'd asserted that in a docstring; now
> we've watched it happen.**

**"It's all synthetic."**
> Yes, and the same code wrote the defects and the labels — it's in the README
> before anyone asks. It measures whether tolerances were overfitted. It does
> not measure robustness to real bank data. What synthetic data buys is the
> false-match rate, which you cannot compute without ground truth.

**"Does it scale?"**
> 100,000 records in four and a half seconds, near-linear. It didn't start
> there — profiling found gate 8 re-solving the whole batch's matching once per
> candidate. The fix decomposes the graph into components; a fingerprint of
> every verdict across twelve datasets hashes identically before and after.

**"What breaks it?"**
> We attacked it ourselves and wrote it up — six findings, all fixed. The one
> worth telling you: our exception CSV could carry an Excel formula. In
> production that column holds bank narration and payment descriptions, which is
> text a paying customer chooses. That's CSV injection with a finance team on
> the receiving end.

---

## If something goes wrong

| symptom | do this |
|---|---|
| A command errors | `./tools/pitch.sh --check` names the broken one. Skip to the browser tab; screen 5 stands alone. |
| `data/` missing | `make data` — 3 seconds, and CI proves it regenerates byte-for-byte. |
| Terminal too small | Beats 1 and 3 are the long ones. `--auto` won't help; shrink the font before starting. |
| Badly over time | Cut screen 1 and open on screen 2 — say "B2, a slightly longer SQL script, scores 89 with 16 false matches" and carry on. |
| Asked to prove it's live | Re-run any beat — each is under two seconds. `make verify` recomputes every published number across 876 tests, but it takes about a minute warm and longer cold, so offer it for after, not on the clock. |
