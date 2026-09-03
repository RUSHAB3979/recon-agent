# Holdout evaluation

The protocol was committed in `7b7aa41` before the initial run on 1 September
2026. It used five additional seeds (70001–70005), the primary scenario mix,
and the deterministic ladder with the evidence reader disabled.

The initial evaluation was a single run. The seeds and runner are retained
for reproduction; later executions are reruns of known data.

## Recorded result

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

## Interpretation

All five batches scored 94/100 with no false matches. The fixed primary mix
contains six `DESCRIBED_REFUND` cases in every 100-case batch, and the
deterministic agent abstains on all six. The repeated 94/100 therefore follows
from the scenario mix; it should not be treated as five independent estimates
of production accuracy.

B2's difficulty floor varied from 8% to 13%, leaving the agent two to seven
correct cases ahead. Allocation recall ranged from 0.9716 to 0.9719.

These seeds come from the same generator as the committed data. They test
behavior on additional generated batches, not compatibility with real bank
data or unseen types of reconciliation failures.

## Reproduce

Run `make holdout` or, with `PYTHONPATH=src`, `python tools/holdout.py`.
The runner rejects the development and previously reserved benchmark seeds.
Rerunning this command does not create a new held-out evaluation.
