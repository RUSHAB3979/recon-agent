# The 5-minute video — shooting script

`docs/PITCH.md` is the **live** runbook: you improvise over commands you are
running in front of people. This is the **recorded** script. Different artifact,
different rules — no wifi risk and no clock anxiety, but also no room to ad-lib,
and a hard cap you cannot talk your way past.

**Target 4:45, not 5:00.** The submission is graded partly on throughput.
Running over a five-minute limit is the first thing a grader notices and the
last thing you want them to remember.

- The spoken text is **627 words** — counted, not estimated, and the header
  timings are derived from it. With the marked pauses that runs:

  | your pace | runtime |
  |---|---|
  | 125 wpm (deliberate) | **5:14** — over; take cuts 1 and 2 |
  | 135 wpm (normal) | **4:52** |
  | 145 wpm (brisk) | **4:32** |

- **Measure your own rate before you cut anything.** Record the cold open, which
  is 62 words: divide 62 by the seconds it took, times 60. If you land under 130,
  take the cuts below — don't try to talk faster, because the emphasised lines
  stop landing.
- **The numbers are load-bearing.** Every figure was recomputed on 2026-09-02
  and matches `./tools/pitch.sh --auto`. If you re-run and one moves, change the
  script — not the number.

---

## Production

**Format:** screen capture with voiceover, with your face on camera for the
first 25 seconds and the last 20. A pure screencast reads as anonymous; a pure
talking head wastes the fact that you actually have something to show.

**Capture:** 1920×1080, 30fps. Terminal at ~18pt, dark background, tall enough
for 25 lines. Record the terminal and the voiceover **separately** — reading
while watching output land is how takes get ruined.

**Shell: Git Bash, inside Windows Terminal.** Not cmd.exe — `tools/pitch.sh` is
a shell script, and cmd answers `'.' is not recognized`. It matters on camera
too: the beats use ANSI escapes for the bold headers and the cyan command lines,
and cmd renders those inconsistently. Washed-out output is not fixable in the
edit.

**Get the terminal footage first:**

```bash
cd /c/Users/rusha/Downloads/reconagentstep1/recon-agent
./tools/pitch.sh --check     # 11/11 before you record anything
./tools/pitch.sh --auto      # capture this; it runs every beat unattended
```

From cmd or PowerShell, if you must:

```
"C:\Program Files\Git\bin\bash.exe" tools/pitch.sh --auto
```

Then narrate over it. `--auto` output arrives faster than the script talks, so
in the edit, **hold each screen** until its narration ends. Do not speed the
voice up to match the terminal; slow the terminal down to match the voice.

**One take per section, not one take for the video.** Nine clean sections cut
together beats a fifth attempt at five unbroken minutes.

---

## The script

`[SCREEN]` is what the viewer sees. `▸` is a real pause, about a second. Bold is
emphasis in delivery, not volume.

---

### 0:00 — Cold open · to camera

`[SCREEN] You. No slide, no title card.`

> When someone pays a merchant online, the money doesn't go from their bank to
> the merchant's.
>
> It goes through a payment gateway — which batches a settlement cycle, takes
> its fee, nets off refunds, and sends **one** transfer. The bank writes **one
> line**.
>
> So the merchant holds two records of the same money, and has to prove they
> agree. Every cycle. ▸

---

### 0:29 — The thesis · to camera, then cut

`[SCREEN] Hold on you for the first sentence, then cut to the terminal.`

> The obvious move is to hand that to an LLM. That's the wrong answer — and
> knowing why is the whole project.
>
> **A wrong match is worse than no match.** An unmatched row gets a human. A
> confidently wrong one doesn't — it becomes a reconciled line nobody opens
> again. ▸

*Cut on the pause. Don't announce the transition.*

---

### 0:52 — The floor

`[SCREEN] Beat 1 — baselines on data/primary. Hold on the B2 row.`

> Every submission today will tell you its accuracy. Almost none will tell you
> what a plain script already scores on the same data.
>
> So we published ours, as runnable code. B1 is exact joins. B2 adds one rule —
> look up an unmatched refund by amount.
>
> **B2 scores 89 out of 100.** That's our floor, and we quote against it,
> because B1 flatters us. ▸

---

### 1:22 — The money shot

`[SCREEN] Beat 2 — the three-row table. Let it sit ~2s in silence first.`

> One scorer for both rows — otherwise you're measuring the instruments.
>
> B2 gets 89. We get 94. ▸ But **read the false-match column, not the accuracy
> column.**
>
> B2 buys those 89 by guessing, and books **sixteen wrong attributions** to do
> it. We book **zero**.
>
> Nobody publishes that number, because in production there's no ground truth
> to compute it against. Synthetic data is the only reason it can exist. ▸

---

### 1:54 — Abstention, priced

`[SCREEN] Beat 3, then 3b. Hold on the exception row long enough to read it.`

> On ten cases the agent stops. Two refund events are arithmetically valid for
> the same bank line, and nothing separates them — so it declines.
>
> That isn't a failure. **That's the product.** Oracle's engine breaks that tie
> by lowest transaction ID — file order. We abstain by default, and measure what
> it costs.
>
> And the exception list isn't a count. ▸ Every item carries the money, the
> evidence, and the action. **Four hundred thousand rupees, on somebody's desk,
> with the reason attached.** ▸

---

### 2:31 — The holdout

`[SCREEN] Beat 4 — the five-seed table.`

> Anyone can tune until the number looks good, so this was pre-registered — the
> protocol in one commit, the run in the next. The ordering is in the git log.
>
> Five seeds nothing in the repo had ever generated. Same 94. Zero false
> matches.
>
> And the honest part: **that constancy is structural, not stability.** What
> moved is the floor — the baseline swung from 8% to 13% while we didn't move at
> all. **All the variance sat in the guesser. None in the prover.** ▸

---

### 3:10 — Where the model actually sits

`[SCREEN] The nine-gate diagram from the README, or stay on the terminal.`

> So where's the AI?
>
> By the time a line reaches the model, nine gates have verified the amount to
> the paise, the date window, the currency, and global feasibility. The model
> does the one thing the gates can't — read an operations note and say which
> product it refers to.
>
> It answers with one letter. And it **cannot** do arithmetic, because **there
> are no numbers in its input.** That's not a policy. It's the architecture. ▸

---

### 3:45 — What it did, and what we haven't measured

`[SCREEN] docs/LIVE_MODEL.md, scrolled to the result table.`

> We ran that against a live model once, on the dev set. It read 28 lines,
> claimed 15, and the answer key agreed with **15 of 15**. Dev goes from 90 to
> 96, and the false-match rate stays at zero.
>
> Fifteen out of fifteen is fifteen — a real result on a small sample, not a
> claim about models. And it's synthetic data: the same code wrote the defects
> and the labels. That's in the README before anyone asks. ▸

---

### 4:22 — Close · back to camera

`[SCREEN] Beat 5 → the demo page in a browser → cut to you for the last two lines.`

> This is what you hand somebody. One file — no server, no JavaScript, no
> network. It opens from disk and prints to PDF.
>
> A hundred thousand records reconcile in four and a half seconds. 883 tests.
> Every number you just saw was computed by the run that wrote that page.
>
> **The deterministic core does the arithmetic. The model reads.** ▸ That's the
> whole design.

---

## Cut list, if a take runs long

Cut in this order. The first two cost you nothing.

1. **"One scorer for both rows…"** in the money shot (−10 words, ≈4s).
   The right instinct, and it is stated in the README — but on video the two
   rows are visibly from one table, so the point is already made.
2. **The Oracle sentence** in the abstention section (−11 words, ≈5s).
   Excellent in a live room where you can read the reaction; merely clever on
   video, and it invites a question you have no time to answer.
3. **The "structural, not stability" caveat** (−39 words, ≈17s).
   ⚠️ This one makes the holdout section *less* honest. Take it only if 1 and 2
   were not enough, and only because the caveat stays in the README — which it
   does. If you cut it, say "same 94 across five unseen seeds" and stop; do not
   replace it with a stronger claim.

Cuts 1 and 2 together take a 125-wpm read to **5:03**, and a 130-wpm read to
**4:52**. All three take 125 wpm to **4:43**.

**Never cut:** the false-match column, the exception row with the rupee figure,
or "there are no numbers in its input." Those three are what separate this
submission from a demo.

---

## Before you upload

- [ ] Runtime **under 5:00** — check the exported file, not the timeline.
- [ ] Terminal legible at 720p; graders may not watch at full resolution.
- [ ] No API key in any frame. Scrub `env`, shell history, and the title bar.
- [ ] Audio normalised, no clipping on the emphasised words.
- [ ] Repo URL on screen at the close, and in the video description.
