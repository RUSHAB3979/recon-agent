# Robustness and performance review

The 1 September 2026 review tested malformed inputs, spreadsheet exports,
and larger generated batches. The resulting fixes are covered by
`tests/test_adversarial.py`.

## Input handling

| Issue | Fix |
|---|---|
| Text in an exported exception CSV could be interpreted as a spreadsheet formula | Prefix affected cells with an apostrophe; preserve ordinary values |
| Timestamp offsets were accepted but dropped when converting to dates | Reject offset-bearing timestamps until a business timezone is explicitly supported |
| Money parsing accepted underscores, leading plus signs, and non-ASCII digits | Require the declared integer-paise format, `-?[0-9]+` |
| A UTF-8 BOM changed the first header's name | Read CSVs with `utf-8-sig` |

The money-parser finding concerned an overly permissive input contract, not a
demonstrated unit-conversion error. Interior separators, currency symbols,
and exponents were already rejected.

The suite also checks half-up rounding, integer amounts throughout the run,
CRLF files, invalid payment amounts, empty batches, and net-negative cycles.

## Scaling

Profiling found three repeated operations:

1. Case decisions rebuilt whole-run indexes once per case. A `LadderIndex` is
   now built once and passed to the case evaluator.
2. Candidate checks repeatedly rebuilt the set of referenced events. That set
   is now cached for the batch.
3. The feasibility gate re-solved the whole candidate graph for each pairing.
   It now re-solves only the pairing's connected component.

A maximum matching on a disconnected graph is the union of matchings on its
components. Forcing one edge changes only its own component, so this optimization
preserves the feasibility test. Regression tests compare component and whole-graph
results, including a connected chain that must not be split.

Recorded timings from development:

| Generated gateway events | Before | After |
|---:|---:|---:|
| 500 | 0.022 s | 0.015 s |
| 2,000 | 0.230 s | 0.060 s |
| 8,000 | 4.466 s | 0.265 s |
| 20,000 | 31.939 s | 0.739 s |
| 50,000 | — | 2.106 s |
| 100,000 | — | 4.452 s |

The 20,000-event batch ran about 43 times faster after all three fixes. These
are historical local timings on generated data, not production throughput
guarantees. The controller's records-per-second metric also counts settlement
details and summaries, so it has a different denominator from this table.

During development, verdicts, claim reasons, abstentions, gate counters, and
audit output were compared across twelve generated batches before and after
the graph optimization. Their recorded fingerprints agreed. Structural tests
guard the index reuse and graph equivalence without requiring identical timings
on every machine.
