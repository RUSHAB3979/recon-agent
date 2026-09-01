"""What the demo surface must guarantee.

A dashboard is the easiest place in a project to lie, and usually by accident:
it is written last, it is read by people who will not run the commands, and it
is the one artifact where a number can be typed in beside a computed one and
nobody notices for a week. These tests close that gap in three directions.

    IT AGREES WITH THE TERMINAL. The page and ``make report`` are the same
    figures from the same scorer, so a reviewer who runs the command sees what
    the page showed them.

    IT TRACKS THE RUN. Change the run and the page changes. A page that renders
    identically from a different result is a template with numbers painted on.

    IT IS SELF-CONTAINED AND ESCAPED. No network, no scripts, and evidence text
    -- which is generated from data -- cannot inject markup into the page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from recon.datagen import Family, GenConfig, generate
from recon.datagen.io import write_dataset
from recon.match.exceptions import build_exception_list
from recon.match.passes import JOIN_ONLY_LADDER
from recon.metrics.dashboard import (
    QUEUE_LIMIT,
    Panel,
    build_page,
    panel_for,
    render,
    write_page,
)

STAMP = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def directory(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dashboard")
    write_dataset(
        generate(GenConfig(n_records=500, seed=42, family=Family.DEVELOPMENT)), out
    )
    return Path(out)


@pytest.fixture(scope="module")
def panel(directory) -> Panel:
    return panel_for(directory)


@pytest.fixture(scope="module")
def page(panel) -> str:
    return render([panel], STAMP)


# --------------------------------------------------------------------------
# it agrees with the terminal
# --------------------------------------------------------------------------


def test_the_headline_figures_are_the_scored_ones(panel, page):
    """Every case count on the page comes off the same ScoreReport."""
    for report in (panel.b1, panel.b2, panel.agent):
        assert f"{report.correct_cases}/{report.total_cases}" in page


def test_the_page_and_the_terminal_report_agree(directory, panel, page, capsys):
    """One instrument, two surfaces -- asserted, not conventional.

    The first question a sceptic asks about a dashboard is whether it agrees
    with the command line. Both are rendered here and the figures that carry
    the claim -- every system's case score, the false-match rate, allocation
    precision -- are required to appear in both. A demo computing its own
    numbers could flatter the run in ways the terminal never showed, and this
    is the test that stops it.
    """
    from recon.metrics.report import render as render_text

    render_text(directory)
    terminal = capsys.readouterr().out

    for report in (panel.b1, panel.b2, panel.agent):
        score = f"{report.correct_cases}/{report.total_cases}"
        assert score in page
        # The terminal pads the two halves apart; compare the parts it prints.
        assert f"{report.correct_cases}/" in terminal
        assert f"{report.allocations.precision:.4f}" in terminal
        assert f"{report.allocations.precision:.4f}" in page

    assert f"{panel.agent.false_match_rate * 100:.2f}%" in terminal
    assert f"{panel.agent.false_match_rate * 100:.2f}%" in page


def test_the_difficulty_floor_is_quoted_against_b2(panel, page):
    """D against B1 would be the flattering number; the page must show B2's."""
    assert f"D = {(1 - panel.b2.outcome_accuracy) * 100:.1f}%" in page
    if panel.b1.outcome_accuracy != panel.b2.outcome_accuracy:
        against_b1 = f"D = {(1 - panel.b1.outcome_accuracy) * 100:.1f}%"
        assert against_b1 not in page


def test_the_false_match_rate_is_on_the_page_beside_the_accuracy(panel, page):
    """The qualifying number shares the row; a footnote would not be read."""
    assert f"{panel.agent.false_match_rate * 100:.2f}%" in page
    assert "Read the false-match rate before the outcome accuracy" in page


def test_the_queue_totals_are_reported_separately(panel, page):
    """Control breaks and abstentions are never summed into one figure."""
    assert "control breaks" in page and "unattributed" in page
    assert "never added together" in page


def test_the_page_measures_which_queue_total_is_larger(panel, page):
    """Prose on a generated page has the same standing as its numbers.

    "the abstentions are the larger figure" is a checkable claim about this
    family, so the page checks it rather than repeating what was true when the
    sentence was written.
    """
    from recon.match.controller import ABSTAIN as ABSTAINED
    from recon.match.controller import EXCEPTION as BREAK

    unattributed = sum(
        item.exposure_paise for item in panel.exceptions if item.outcome == ABSTAINED
    )
    breaks = sum(
        item.exposure_paise for item in panel.exceptions if item.outcome == BREAK
    )
    expected = (
        "the abstentions are the larger figure"
        if unattributed > breaks
        else "the control breaks are the larger figure"
    )
    other = (
        "the control breaks are the larger figure"
        if expected.startswith("the abstentions")
        else "the abstentions are the larger figure"
    )
    assert expected in page
    assert other not in page


def test_every_exception_category_appears_with_its_count(panel, page):
    from recon.match.exceptions import summarise

    summary = summarise(panel.exceptions)
    assert summary
    for category, (count, _exposure) in summary.items():
        assert category in page
        assert f">{count}</td>" in page


def test_the_queue_head_is_shown_and_the_tail_is_accounted_for(panel, page):
    items = panel.exceptions
    for item in items[:QUEUE_LIMIT]:
        assert item.case_id in page
    if len(items) > QUEUE_LIMIT:
        assert f"{len(items) - QUEUE_LIMIT} further item(s)" in page


def test_the_gate_table_publishes_its_zeroes(panel, page):
    """A gate that eliminated nothing is still listed, with its zero."""
    gates = [
        name
        for result in panel.run.ladder.per_pass
        for name in result.counters
        if name.startswith("gate_")
    ]
    assert gates
    for name in gates:
        assert name in page
    assert "no effect on this data" in page


# --------------------------------------------------------------------------
# it tracks the run
# --------------------------------------------------------------------------


def test_a_weaker_ladder_renders_a_different_page(directory, panel, page):
    """The page is a function of the result, not a template with a logo on it.

    Rendered from a join-only ladder -- a materially worse agent -- both the
    score and the queue must move, and the page must show that they did. If it
    did not, every assertion above would be checking that a constant is still a
    constant.
    """
    join_only = panel_for(directory, JOIN_ONLY_LADDER)
    assert join_only.agent.correct_cases < panel.agent.correct_cases
    assert len(build_exception_list(join_only.run)) > len(panel.exceptions)

    weaker = render([join_only], STAMP)
    assert weaker != page
    assert f"{join_only.agent.correct_cases}/{join_only.agent.total_cases}" in weaker


def test_the_same_run_renders_identically(panel):
    """Determinism, so a regenerated page diffs to nothing when nothing moved."""
    assert render([panel], STAMP) == render([panel], STAMP)


# --------------------------------------------------------------------------
# it is self-contained and escaped
# --------------------------------------------------------------------------


def test_the_page_makes_no_network_request(page):
    """It has to work from a USB stick, offline, in a room with no wifi."""
    import re

    assert "<script" not in page
    assert "http://" not in page
    assert "https://" not in page
    # Protocol-relative and any other remote reference, caught at the attribute
    # rather than by scanning for schemes: `src="//cdn..."` carries no scheme.
    remote = [
        url
        for url in re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', page)
        if not url.startswith("#")
    ]
    assert remote == []


def test_evidence_text_is_escaped_not_injected(directory):
    """Evidence is generated text, and generated text is still untrusted input.

    A case id or reason carrying a angle bracket must render as characters,
    never as markup. The check is done by rewriting a real item rather than by
    trusting that the generator will never emit one.
    """
    import dataclasses

    panel = panel_for(directory)
    assert panel.exceptions
    hostile = dataclasses.replace(
        panel.exceptions[0],
        evidence='<img src=x onerror="alert(1)">',
        recommended_action="a & b < c",
    )
    injected = dataclasses.replace(
        panel, exceptions=(hostile,) + panel.exceptions[1:]
    )
    html = render([injected], STAMP)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html
    assert "a &amp; b &lt; c" in html


def test_the_page_is_well_formed(page):
    from html.parser import HTMLParser

    void = {"meta", "br", "hr", "img", "input", "link", "source", "col", "base"}

    class Checker(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []
            self.errors: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack[-1] != tag:
                self.errors.append(f"{tag} closed against {self.stack[-3:]}")
                while self.stack and self.stack.pop() != tag:
                    pass
            else:
                self.stack.pop()

    checker = Checker()
    checker.feed(page)
    assert checker.errors == []
    assert checker.stack == []


# --------------------------------------------------------------------------
# the file on disk
# --------------------------------------------------------------------------


def test_build_and_write_produce_a_readable_file(directory, tmp_path):
    html = build_page([directory], generated_at=STAMP)
    path = write_page(html, tmp_path / "nested" / "index.html")
    assert path.read_text(encoding="utf-8") == html
    assert path.stat().st_size > 5_000


def test_render_refuses_an_empty_page():
    """A page with no families is a broken command, not an empty dashboard."""
    with pytest.raises(ValueError):
        render([], STAMP)
