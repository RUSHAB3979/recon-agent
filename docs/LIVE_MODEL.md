# Live-model experiment

Recorded on 2 September 2026 using `liquid/lfm-2.5-2.6b:free` through
OpenRouter. This is one development-batch experiment, separate from the
deterministic benchmark reproduced in CI.

## Method

The existing deterministic ladder ran first, followed by the optional evidence
reader. Both outputs were evaluated with the same scorer and answer key.
The measurement used `min_confidence=0.0`; the usual 0.70 threshold was then
checked against the recorded verdicts. No accepted verdict fell below 0.70.
No matching rule or threshold was tuned in response to this run.

## Development result

| Metric | Deterministic | With evidence reader |
|---|---:|---:|
| Correct cases | 90/100 | 96/100 |
| False-match rate | 0.00% | 0.00% |
| Allocation precision | 1.0000 | 1.0000 |
| Allocation recall | 0.9512 | 0.9878 |
| Incorrect allocations | 0 | 0 |

The reader examined 28 lines and accepted 15 allocations, all of which agreed
with the answer key. The other 13 lines remained unresolved, including two
transport failures. Several notes could not distinguish candidates from the
same product category.

The recorded run used 12,479 input tokens and 19,132 output tokens over 28
calls, taking 182 seconds. Its recorded cost was $0 on the free-tier model.
That is a historical experiment cost, not a guarantee of current availability
or pricing.

## Rate-limited primary attempt

Fifteen of sixteen primary requests were rate-limited after the development
run. The pipeline returned the deterministic result: 94/100 correct cases,
0.00% false matches, and 1.0000 allocation precision. This tests fallback
behavior; it does not establish model accuracy on the primary batch.

## Fixes prompted by the experiment

- Made the output-token budget configurable, with a default of 2048. The earlier
  512-token limit could leave reasoning models without room to emit a verdict.
- Included HTTP status and provider details in failed-request reasons so that
  quota, provider-capacity, and configuration failures can be distinguished.
- Recognized string `"null"` as a decline instead of counting it as an unknown
  candidate label.

Regression tests cover these cases in `tests/test_adjudicator.py`.

## Run another experiment

After completing the README setup, configure an API key in your environment:

```bash
export OPENROUTER_API_KEY="your-key"
export OPENROUTER_MODEL="your-model-id"
python -m recon.match.controller data/dev --adjudicate --min-confidence 0.70
```

In PowerShell, use `$env:OPENROUTER_API_KEY` and `$env:OPENROUTER_MODEL`, and
run Python through `.\.venv\Scripts\python.exe`. Anthropic is also supported
through `ANTHROPIC_API_KEY`; it takes precedence if both provider keys are set.

The controller prints decisions and usage. The ordinary benchmark report
continues to measure the deterministic ladder. Without a configured key, the
reader declines all remaining lines.

The sample is small and synthetic. Model variance, primary-batch model
accuracy, and performance on real bank data remain unmeasured. A scripted
answer-key reader reaches 100/100 in tests, which is a test of the integration,
not a model result.
