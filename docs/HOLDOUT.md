# The holdout run

Pre-registered in `tools/holdout.py`, committed before the run
(`7f23a24 Declare the holdout protocol, before running it`). Executed once, on
2026-09-01. Nothing in the engine changed afterwards, and the commit order is
the evidence rather than the assurance.

## Protocol, as declared

| | |
|---|---|
| seeds | 70001–70005, a contiguous block, never generated or scored before |
| family | `PRIMARY` — the realistic-prevalence mix that produces the headline |
| ladder | frozen `DEFAULT_LADDER`; evidence-reading rung off |
| runs | **one** |

A guard in the runner refuses any seed appearing elsewhere in the repository
(7, 8, 42, 99, 101, 2026, 20260905). A holdout seed already seen is a re-run
wearing the name of a holdout, and that is the one way this measurement could
have been wrong without looking wrong.

## Result, verbatim

```
holdout run  2026-09-01 04:22:23 UTC
  family primary, 500 records, seeds [70001, 70002, 70003, 70004, 70005]
  ladder frozen, evidence-reading rung off

     seed    cases   outcome  false match   alloc P   alloc R   FP   D vs B2  headroom
    70001   94/100    94.0%       0.00%    1.0000    0.9718    0    12.0%        +6
    70002   94/100    94.0%       0.00%    1.0000    0.9719    0    10.0%        +4
    70003   94/100    94.0%       0.00%    1.0000    0.9717    0     8.0%        +2
    70004   94/100    94.0%       0.00%    1.0000    0.9716    0    13.0%        +7
    70005   94/100    94.0%       0.00%    1.0000    0.9716    0     9.0%        +3

  outcome accuracy   mean 94.0%   min 94.0%   max 94.0%
  false-match rate   max 0.00%   allocation precision min 1.0000
```

The held-out figure is **identical to the published primary figure**: 94/100,
0.00% false matches, 1.0000 allocation precision. Nothing was fitted to the
particular batches in `data/`.

## What it does not show, and this matters more than the number

**The headline was never free to move much, and a holdout that returns the same
number five times is measuring less than it looks like it is measuring.**

`PRIMARY_CASE_SHARES` is a fixed partition of 100 cases, so every seed draws the
same *count* per scenario — 6 `DESCRIBED_REFUND` cases, every time. The agent's
per-class behaviour is deterministic: it closes every other class completely and
abstains on all six `DESCRIBED_REFUND` cases, which is the designed residual the
gates provably cannot separate. Checked on two of the five seeds after the run:

```
70001 missed classes: {'DESCRIBED_REFUND': (0, 6)}
70003 missed classes: {'DESCRIBED_REFUND': (0, 6)}
```

94 is therefore 100 − 6, by construction, on any seed. Reporting the constancy
as though five independent draws had agreed would be reading a structural
identity as evidence of stability.

## What genuinely moved

**D moved five points and the agent did not.** The difficulty floor against B2
ranged from 8.0% to 13.0% across the five seeds — B2's guessing luck varies with
the draw — while the agent's score, false-match rate and allocation precision
did not vary at all. Allocation recall moved only in the fourth decimal
(0.9716–0.9719).

That is the cleanest statement of the difference the whole project is about:
across five unseen batches, **all of the seed-to-seed variance sat in the
guesser and none of it in the prover.** The honest headroom figure is therefore
a range, +2 to +7 cases over B2, not the single number either family happens to
show.

## The limitation that does not go away here

The holdout is a different seed from the same generator. It measures whether the
tolerances were fitted to the batches in `data/`. It does **not** measure
robustness to real bank data, because the same code wrote both the defects and
their labels. This run must not be described as if it did.
