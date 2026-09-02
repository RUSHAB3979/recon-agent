# The rung against a live model

Until 2 September 2026 every end-to-end figure in this repository came from one
of two readers that never touch a network: `DecliningReader`, which refuses
every line and produces the deterministic floor, and `ScriptedReader`, which
answers from the answer key and bounds the plumbing at 100/100. Both are
useful — the first is what CI runs and therefore what the published numbers
are, the second proves the wiring — and neither is a measurement of a model.

`SOLUTION.md` said so plainly, and the honest gap was named before anyone asked
for it. This document closes half of it.

---

## Protocol, stated before the run

Declared first for the same reason the holdout protocol was: a number is only
worth quoting if the thing that produced it was fixed before it was seen.

1. **The ladder is not touched.** The published deterministic result is the
   control row. The rung is appended to `DEFAULT_LADDER` and nothing else
   changes, so the difference between the two rows is attributable to the rung
   and to nothing else.
2. **One scorer, both rows.** The same `recon.metrics.score.score` against the
   same `AnswerKey`. Measuring the agent with one instrument and the control
   with another measures the instruments.
3. **`min_confidence = 0.0` for the measurement.** The run measures the reader,
   not the policy threshold. The published 0.70 floor is then applied to the
   same recorded verdicts, so what the threshold costs is shown rather than
   assumed.
4. **`data/dev` first, and `data/dev` is the only surface anything may be tuned
   against.** Nothing was tuned: no threshold, tolerance or gate changed in
   response to any number below.
5. **The claims are scored pair-wise** against `answer_key_allocations.csv`,
   which gives honest partial credit, so a rung that gets seven legs of an
   eight-leg batch right scores seven and not zero.

## The result on `data/dev`

Run on 2 September 2026. Model named in full, because an accuracy or cost
figure without a model attached is not checkable.

| | cases | outcome | false match | alloc P | alloc R | FP |
|---|---|---|---|---|---|---|
| deterministic only | 90/100 | 90.0% | 0.00% | 1.0000 | 0.9512 | 0 |
| **+ adjudication** | **96/100** | **96.0%** | **0.00%** | **1.0000** | **0.9878** | **0** |

The rung's own behaviour, which is the part that matters:

| | |
|---|---|
| model | `liquid/lfm-2.5-2.6b:free` (OpenRouter) |
| lines examined | 28 |
| claims made | 15 |
| **claims the answer key agrees with** | **15** |
| **claims wrong** | **0** |
| **rung precision** | **1.0000** |
| verdicts below the 0.70 floor | 0 |
| tokens | 12,479 in / 19,132 out over 28 calls |
| cost | $0 — a free-tier model, which is a real price and not a lookup miss |
| wall clock | 182s for the batch |

**Six cases, and zero false matches.** The deterministic ladder is a prover: it
does not guess, and the six it could not close were six it could not prove. The
rung closed them by reading one sentence, and the false-match column did not
move off zero.

## What it declined, and why that reads well

Thirteen of the twenty-eight lines came back as abstentions, and the reasons are
substantive rather than evasive:

- *"Both candidates represent footwear products"*
- *"Both candidates A and B reference the same product"*
- *"Both candidate products are garments"*

Those are correct refusals. Where the residual is two Puma sneakers against a
note that says "return — footwear size mismatch", there is nothing to read, and
a model that picked one would have been guessing with a plausible sentence
attached. Two further declines were transport failures, not judgements, and are
recorded as such.

## What is still NOT measured

- **`data/primary` has no live number.** The attempt on 2 September hit the
  OpenRouter free tier's 50-request daily cap after the dev run, and fifteen of
  its sixteen calls came back rate-limited. That run is reported below as a
  degradation result, not as an accuracy result.
- **One model, one run.** 15-for-15 on fifteen claims is a real result and a
  small sample. It is not a claim about models in general, about this model on
  other data, or about run-to-run stability — the free tier throttles too hard
  to measure variance honestly.
- **Nothing is measured about real bank data.** Unchanged, and still the
  biggest limitation in the repository: the same code wrote the defects and
  their labels.

## The accidental degradation proof

The throttled `data/primary` attempt is worth keeping. With the adjudicator
unreachable on fifteen of sixteen lines, the pipeline returned:

| | cases | outcome | false match | alloc P | alloc R | FP |
|---|---|---|---|---|---|---|
| deterministic only | 94/100 | 94.0% | 0.00% | 1.0000 | 0.9722 | 0 |
| + adjudication (throttled) | 94/100 | 94.0% | 0.00% | 1.0000 | 0.9722 | 0 |

Identical. The rung is strictly additive: when the model is unavailable, every
line it would have read becomes an exception with a named reason, and no
published figure moves. That property was asserted in a docstring before it was
ever observed under a real outage. It has now been observed.

## Three defects the live run exposed

None could move money — the closed-list guard and the confidence floor sit
downstream of all three — and all three made the report say something untrue
about **why** a line went to a human, which in a tool whose central claim is an
honest exception list is the failure that counts.

1. **The output budget was a constant.** At `max_tokens: 512`, three free
   reasoning models spent the whole budget thinking and emitted no verdict,
   which the reader recorded as *"model returned no parseable verdict"*. That
   reason was false: the model was cut off by a number in our own file. The
   budget is now a parameter, defaulting to 2048.
2. **`HTTPError` was the whole reason.** An operator reading that row has to
   choose between waiting, topping up, and fixing a model slug, and the status
   code is what separates them. Declines now name the code and the upstream
   provider — which is how *"somebody else saturated the shared free pool"* was
   distinguished from *"this account is out of quota"*, having initially been
   misdiagnosed as the latter.
3. **A decline spelled as a word was counted as a fabrication.** Two models
   declined correctly and wrote the string `"null"` rather than JSON `null`.
   The closed-list guard already made a match impossible either way, so no money
   was at risk — but a correct refusal was being filed under hallucination, and
   that bucket is the one a reader takes for this rung's error rate. Abstention
   and fabrication no longer share a counter.

Each has a test. The suite is 883 tests.

## Reproducing it

```bash
export OPENROUTER_API_KEY=...              # or ANTHROPIC_API_KEY
export OPENROUTER_MODEL=liquid/lfm-2.5-2.6b:free
make adjudicate                            # data/dev, with the rung
```

`OPENROUTER_MODEL` exists so that reproducing a published number never requires
editing source: the default slug is a paid model, and a key with no credit can
only reach `:free` ones. With no key set at all, `default_reader()` returns the
declining reader and the deterministic numbers come back unchanged — which is
what CI runs, and therefore what every other figure in this repository is.
