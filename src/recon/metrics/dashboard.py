"""The demo surface: one self-contained page, generated from a real run.

WHY A GENERATED FILE AND NOT AN APP

    A reconciliation result is a thing you hand to somebody, not a service they
    log into. The deliverable here is therefore one HTML file with no server,
    no build step, no network access and no JavaScript: it opens from disk,
    prints to PDF, and attaches to an email. Nothing about it can be stale at
    the moment it is read, because it did not exist a moment earlier -- every
    figure on the page is computed by the same call that produced it.

ONE INSTRUMENT, THREE SURFACES

    ``make report`` prints these numbers, ``make exceptions`` writes the queue,
    and this page shows both. All three go through :func:`recon.metrics.report.compare`
    and :func:`recon.match.exceptions.build_exception_list`. That is deliberate
    and it is tested: a demo that computed its own figures could flatter the
    run in ways the terminal never showed, and the first question a sceptical
    reviewer asks about a dashboard is whether it agrees with the command line.

WHAT IT REFUSES TO DO

    No number on this page is typed into it. There is no template with a
    percentage in it, no "approximately", no rounded headline written by hand
    beside a computed one. Where a figure would be misleading on its own -- the
    outcome accuracy, most of all -- the page shows the number that qualifies it
    in the same row rather than in a footnote, because a reader looking at a
    dashboard reads rows, not footnotes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Sequence

from recon.match.controller import ABSTAIN, EXCEPTION, RunResult
from recon.match.exceptions import ExceptionItem, build_exception_list, summarise
from recon.match.journal import build_journal
from recon.match.passes import DEFAULT_LADDER, Pass
from recon.metrics import baselines
from recon.metrics.report import CURVE_THRESHOLDS, compare
from recon.metrics.score import ScoreReport, precision_coverage_curve

__all__ = ["Panel", "build_page", "panel_for", "render", "write_page"]

QUEUE_LIMIT = 12


# --------------------------------------------------------------------------
# formatting: the presentation boundary, and the only place a decimal appears
# --------------------------------------------------------------------------


def _rupees(paise: int) -> str:
    """Integer paise to a grouped rupee string, without touching a float.

    The terminal renderer divides by 100 for display and that is fine there.
    Here the same conversion is done with ``divmod`` and manual grouping, for
    the same reason the rest of the pipeline does: the one habit that keeps
    money off floats is not making an exception for the easy cases.
    """
    sign = "-" if paise < 0 else ""
    whole, part = divmod(abs(paise), 100)
    return f"{sign}{whole:,}.{part:02d}"


def _pct(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f}%"


def _ratio(value: float) -> str:
    return f"{value:.4f}"


# --------------------------------------------------------------------------
# the data behind one family, gathered once
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Panel:
    """Everything the page shows about one dataset family.

    Gathered in one place so that rendering is a pure function of it. A test
    can then hold the panel and the page side by side and assert the page did
    not invent, round away or contradict anything in it.
    """

    directory: Path
    agent: ScoreReport
    b1: ScoreReport
    b2: ScoreReport
    run: RunResult
    exceptions: tuple[ExceptionItem, ...]
    curve: tuple[tuple[float, float, float, float], ...]
    lexical: dict[str, float]
    audit_records: int
    audit_head: str

    @property
    def name(self) -> str:
        return self.directory.name

    @property
    def difficulty_floor(self) -> float:
        """D, quoted against B2 -- the honest adversary, not the flattering one."""
        return 1 - self.b2.outcome_accuracy

    @property
    def headroom(self) -> int:
        return self.agent.correct_cases - self.b2.correct_cases


def panel_for(directory: str | Path, ladder: Sequence[Pass] = DEFAULT_LADDER) -> Panel:
    """Run one family and collect every figure the page will show."""
    directory = Path(directory)
    agent, b1, b2, run, key = compare(directory, ladder)
    journal = build_journal(run)
    return Panel(
        directory=directory,
        agent=agent,
        b1=b1,
        b2=b2,
        run=run,
        exceptions=build_exception_list(run),
        curve=tuple(
            precision_coverage_curve(run.to_agent_output(), key, CURVE_THRESHOLDS)
        ),
        lexical=baselines.lexical_hit_rate(baselines.Batch.load(directory)),
        audit_records=len(journal.decisions),
        audit_head=journal.head_hash,
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8; --panel: #ffffff; --ink: #16150f; --muted: #6b6558;
  --rule: #e2ddd2; --accent: #7a3e12; --good: #2f6b3a; --warn: #8a5a12;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14130f; --panel: #1b1a15; --ink: #eceadf; --muted: #9c9483;
    --rule: #2f2c24; --accent: #d99a63; --good: #7fbe8b; --warn: #d9b063;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 2.5rem 1.5rem 4rem;
}
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.7rem; letter-spacing: -0.01em; margin: 0 0 .35rem; }
h2 {
  font-size: 1.05rem; text-transform: uppercase; letter-spacing: .09em;
  color: var(--muted); margin: 3rem 0 .9rem; font-weight: 600;
}
h3 { font-size: .95rem; margin: 1.8rem 0 .6rem; }
p { margin: .6rem 0; }
a { color: var(--accent); }
.sub { color: var(--muted); margin: 0 0 1.4rem; }
.note {
  border-left: 3px solid var(--accent); background: var(--panel);
  padding: .85rem 1.1rem; margin: 1.2rem 0; border-radius: 0 6px 6px 0;
}
.note strong { color: var(--accent); }
.card {
  background: var(--panel); border: 1px solid var(--rule);
  border-radius: 8px; padding: 1.1rem 1.25rem; margin: 1rem 0;
}
.card > h3:first-child { margin-top: 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .87rem; }
th, td { text-align: right; padding: .38rem .55rem; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
thead th {
  border-bottom: 1px solid var(--rule); color: var(--muted);
  font-weight: 600; font-size: .74rem; text-transform: uppercase;
  letter-spacing: .06em;
}
tbody tr + tr td { border-top: 1px solid var(--rule); }
tbody tr.hero td { font-weight: 700; }
td.num, th.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.good { color: var(--good); }
.warn { color: var(--warn); }
.muted { color: var(--muted); }
.kv { display: flex; flex-wrap: wrap; gap: 1.6rem; margin: .8rem 0 0; }
.kv > div { min-width: 8rem; }
.kv dt { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
         color: var(--muted); margin: 0 0 .2rem; }
.kv dd { margin: 0; font-family: var(--mono); font-size: 1.25rem; }
details { margin: .3rem 0 0; }
summary { cursor: pointer; color: var(--muted); font-size: .82rem; }
details p { font-size: .84rem; margin: .45rem 0 0; }
code, .mono { font-family: var(--mono); font-size: .85em; }
footer {
  margin-top: 3.5rem; padding-top: 1.2rem; border-top: 1px solid var(--rule);
  color: var(--muted); font-size: .85rem;
}
nav { margin: 1.2rem 0 0; font-size: .88rem; }
nav a { margin-right: 1rem; }
@media print {
  body { padding: 0; } .card { break-inside: avoid; } nav { display: none; }
}
"""


NUMERIC = ' class="num"'


def _table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], hero: int = -1
) -> str:
    """A table whose first column is text and whose rest are tabular numerals.

    Cells arrive pre-escaped or pre-marked-up by the caller; headers are escaped
    here. Splitting it that way keeps the one place that emits raw HTML -- the
    queue's evidence disclosure -- visible at its call site instead of behind a
    flag on this function.
    """
    head = "".join(
        f"<th{NUMERIC if i else ''}>{escape(h)}</th>" for i, h in enumerate(headers)
    )
    body = []
    for index, row in enumerate(rows):
        cells = "".join(
            f"<td{NUMERIC if i else ''}>{cell}</td>" for i, cell in enumerate(row)
        )
        klass = ' class="hero"' if index == hero else ""
        body.append(f"<tr{klass}>{cells}</tr>")
    return (
        '<div class="scroll"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _scoreboard_row(label: str, report: ScoreReport) -> list[str]:
    return [
        escape(label),
        f"{report.correct_cases}/{report.total_cases}",
        _pct(report.outcome_accuracy),
        f'<span class="{"good" if report.false_match_rate == 0 else "warn"}">'
        f"{_pct(report.false_match_rate, 2)}</span>",
        _ratio(report.allocations.precision),
        _ratio(report.allocations.recall),
        str(report.allocations.false_positives),
    ]


def _scoreboard(panel: Panel) -> str:
    rows = [
        _scoreboard_row("B1 — exact joins", panel.b1),
        _scoreboard_row("B2 — + amount lookup", panel.b2),
        _scoreboard_row("agent", panel.agent),
    ]
    table = _table(
        ["system", "cases", "outcome", "false match", "alloc P", "alloc R", "alloc FP"],
        rows,
        hero=2,
    )
    return (
        table
        + '<p class="muted">Difficulty floor <strong>D = '
        + _pct(panel.difficulty_floor)
        + f"</strong>, quoted against B2 rather than B1 — a floor built on a "
        f"restriction is the easy number to beat, not the honest one. Headroom "
        f"over B2: <strong>{panel.headroom:+d} cases</strong>.</p>"
    )


def _passes(panel: Panel) -> str:
    rows = [
        [
            escape(result.pass_name),
            str(result.examined),
            str(len(result.claims)),
            str(len(result.abstentions)),
        ]
        for result in panel.run.ladder.per_pass
    ]
    out = [
        "<h3>Per-pass yield</h3>",
        _table(["rung", "examined", "claimed", "abstained"], rows),
    ]

    for result in panel.run.ladder.per_pass:
        gates = {k: v for k, v in result.counters.items() if k.startswith("gate_")}
        if not gates:
            continue
        gate_rows = []
        for name, count in gates.items():
            if name.startswith("gate_2"):
                gate_rows.append([escape(name), "—", "applied as the amount index"])
                continue
            gate_rows.append(
                [escape(name), str(count), "" if count else "no effect on this data"]
            )
        shortlisted = result.counters.get("candidates_by_amount", 0)
        out.append(
            f"<h3>Gate eliminations — {escape(result.pass_name)}</h3>"
            f'<p class="muted">{shortlisted} amount-matched candidate(s) across '
            f"{result.examined} line(s). Zeroes are printed rather than hidden: a "
            f"gate that does no work on this data is a fact about the data, and "
            f"publishing it is what lets a reader judge whether the gate earns "
            f"its place.</p>"
            + _table(["gate", "eliminated", ""], gate_rows)
        )
    return "".join(out)


def _curve(panel: Panel) -> str:
    rows = [
        [
            f"{threshold:.2f}",
            _ratio(coverage),
            _ratio(precision),
            _ratio(false_match),
        ]
        for threshold, coverage, precision, false_match in panel.curve
    ]
    retained = sorted(
        {
            decision.confidence
            for decision in panel.run.to_agent_output().decisions
            if decision.outcome != ABSTAIN
        }
    )
    note = ""
    if len(retained) <= 1:
        held = ", ".join(f"{value:.2f}" for value in retained) or "none"
        note = (
            f'<p class="muted"><strong>Flat by construction.</strong> Every '
            f"retained decision carries confidence {held}, so no threshold moves "
            f"one. The engine proves a line or abstains on it; there is no ranked "
            f"middle to trade coverage against. The dial is real only on the "
            f"evidence-reading rung. This note is computed from the run, not "
            f"written into the page.</p>"
        )
    return (
        "<h3>Precision and coverage across abstention thresholds</h3>"
        + _table(["threshold", "coverage", "precision", "false match"], rows)
        + note
    )


def _queue(panel: Panel) -> str:
    items = panel.exceptions
    abstained = [item for item in items if item.outcome == ABSTAIN]
    breaks = [item for item in items if item.outcome == EXCEPTION]
    summary_rows = [
        [escape(category), str(count), _rupees(exposure)]
        for category, (count, exposure) in summarise(items).items()
    ]

    rows = []
    for item in items[:QUEUE_LIMIT]:
        rows.append(
            [
                f'<span class="mono">{escape(item.case_id)}</span>'
                f"<details><summary>evidence &amp; action</summary>"
                f"<p>{escape(item.evidence)}</p>"
                f"<p><em>{escape(item.recommended_action)}</em></p></details>",
                escape(item.category),
                _rupees(item.exposure_paise),
            ]
        )

    more = (
        f'<p class="muted">{len(items) - QUEUE_LIMIT} further item(s) in '
        f"<code>runs/{escape(panel.name)}/exceptions.csv</code>.</p>"
        if len(items) > QUEUE_LIMIT
        else ""
    )

    break_exposure = sum(item.exposure_paise for item in breaks)
    unattributed = sum(item.exposure_paise for item in abstained)
    # Which of the two is larger is a fact about this family, so it is measured
    # rather than asserted. The prose on a generated page has the same standing
    # as its numbers: if it says something checkable, it has to check it.
    larger = (
        "the abstentions are the larger figure"
        if unattributed > break_exposure
        else "the control breaks are the larger figure"
    )

    return (
        "<h3>The operator queue</h3>"
        + '<p class="muted">The two totals are never added together. A control '
        "break is money that does not tie out; an abstention is money that "
        f"arrived and is not yet attributed. On this family {larger}, so a "
        "combined total would be dominated by one kind of item wearing the "
        "name of the other.</p>"
        + '<dl class="kv">'
        + f"<div><dt>open items</dt><dd>{len(items)}</dd></div>"
        + f"<div><dt>control breaks</dt><dd>{len(breaks)}</dd></div>"
        + "<div><dt>break exposure</dt><dd>"
        + _rupees(break_exposure)
        + "</dd></div>"
        + f"<div><dt>unattributed</dt><dd>{len(abstained)}</dd></div>"
        + "<div><dt>unattributed value</dt><dd>"
        + _rupees(unattributed)
        + "</dd></div></dl>"
        + _table(["category", "count", "exposure"], summary_rows)
        + _table(["case", "category", "exposure"], rows)
        + more
    )


def _classification(panel: Panel) -> str:
    agent = panel.agent
    rows = [
        [
            escape(category),
            _ratio(metrics.precision),
            _ratio(metrics.recall),
            str(metrics.support),
        ]
        for category, metrics in sorted(agent.exception_categories.items())
    ]
    return (
        "<h3>Exception classification</h3>"
        + _table(["category", "precision", "recall", "support"], rows)
        + '<dl class="kv">'
        + f"<div><dt>abstentions</dt><dd>{agent.abstention_count}</dd></div>"
        + f"<div><dt>correct refusals</dt><dd>{agent.correct_refusals}</dd></div>"
        + f"<div><dt>missed resolutions</dt><dd>{agent.missed_resolutions}</dd></div>"
        + "<div><dt>NO_ACTION false positives</dt><dd>"
        + f"{agent.no_action_false_positives}/{agent.no_action_support}</dd></div>"
        + "</dl>"
    )


def _lexical(panel: Panel) -> str:
    data = panel.lexical
    return (
        "<h3>The string-matching objection, measured</h3>"
        + '<p class="muted">"You only needed fuzzy matching" is answered per '
        "decision rather than per case: over anonymous refund lines with more "
        "than one exact-amount candidate, does the highest-scoring candidate "
        "happen to be the one the answer key names? A line with k candidates is "
        "a k-sided coin, so chance is the sum of 1/k.</p>"
        + '<dl class="kv">'
        + f"<div><dt>decidable lines</dt><dd>{int(data['decidable_lines'])}</dd></div>"
        + f"<div><dt>lexical hits</dt><dd>{int(data['hits'])}</dd></div>"
        + "<div><dt>expected by chance</dt><dd>"
        + f"{data['expected_by_chance']:.1f}</dd></div>"
        + f"<div><dt>lift</dt><dd>{data['lift']:+.3f}</dd></div>"
        + "</dl>"
    )


def _family(panel: Panel) -> str:
    run = panel.run
    return (
        f'<h2 id="{escape(panel.name)}">{escape(str(panel.directory))}</h2>'
        + '<div class="card">'
        + _scoreboard(panel)
        + '<dl class="kv">'
        + f"<div><dt>throughput</dt><dd>{run.throughput:,.0f}/s</dd></div>"
        + f"<div><dt>records</dt><dd>{run.record_count}</dd></div>"
        + f"<div><dt>elapsed</dt><dd>{run.elapsed_seconds:.3f}s</dd></div>"
        + "<div><dt>audit records</dt><dd>"
        + f"{panel.audit_records}</dd></div>"
        + "</dl>"
        + f'<p class="muted">Chain head <code>{escape(panel.audit_head)}</code> — '
        "this run's seal, recomputed on read by <code>make verify-audit</code>.</p>"
        + "</div>"
        + '<div class="card">' + _passes(panel) + "</div>"
        + '<div class="card">' + _curve(panel) + "</div>"
        + '<div class="card">' + _classification(panel) + "</div>"
        + '<div class="card">' + _queue(panel) + "</div>"
        + '<div class="card">' + _lexical(panel) + "</div>"
    )


def render(panels: Sequence[Panel], generated_at: datetime) -> str:
    """The whole page, as a string. Pure in its inputs."""
    if not panels:
        raise ValueError("render needs at least one family panel")

    nav = " ".join(
        f'<a href="#{escape(panel.name)}">{escape(panel.name)}</a>' for panel in panels
    )
    stamp = generated_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reconciliation agent — run report</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<h1>Multi-source reconciliation agent</h1>
<p class="sub">Generated {escape(stamp)} by <code>make demo</code>. Every figure
on this page was computed by the run that produced the page, through the same
scorer <code>make report</code> prints from. Nothing here is typed in by hand.</p>

<div class="note">
<p><strong>Read the false-match rate before the outcome accuracy.</strong>
Outcome accuracy alone rewards guessing: a contested refund charged to the wrong
event still produces the expected <code>RECONCILED</code>. B2 demonstrates that
in public — it posts a higher outcome accuracy than a join-only agent while
booking false attributions. The columns below are ordered so that trade is
visible rather than buried.</p>
<p><strong>A wrong match is worse than no match.</strong> An unmatched row gets a
human to look at it. A confidently wrong one does not.</p>
</div>

<nav>{nav}</nav>

{"".join(_family(panel) for panel in panels)}

<footer>
<p><strong>The honest limitation.</strong> The held-out families are different
seeds from the same generator. They measure whether the tolerances were
overfitted. They do <em>not</em> measure robustness to real bank data, because
the same code wrote both the defects and their labels.</p>
<p>The evidence-reading rung is off by default and contributes to no figure on
this page. With the declining reader the pipeline reproduces these numbers
exactly, and that equality is a test.</p>
</footer>
</main>
</body>
</html>
"""


def build_page(
    directories: Sequence[str | Path],
    *,
    ladder: Sequence[Pass] = DEFAULT_LADDER,
    generated_at: datetime | None = None,
) -> str:
    panels = [panel_for(directory, ladder) for directory in directories]
    return render(panels, generated_at or datetime.now(timezone.utc))


def write_page(html: str, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the demo page for one or more released datasets."
    )
    parser.add_argument(
        "data_dir",
        type=Path,
        nargs="*",
        default=[Path("data/dev"), Path("data/primary"), Path("data/stress")],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("runs/demo/index.html"),
        help=(
            "where to write the page. Under runs/ rather than data/: a dataset "
            "directory is an input and stays read-only."
        ),
    )
    args = parser.parse_args(argv)
    path = write_page(build_page(args.data_dir), args.out)
    print(
        f"demo page written to {path} "
        f"({path.stat().st_size:,} bytes, self-contained)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
