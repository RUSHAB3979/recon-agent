#!/usr/bin/env bash
#
# The demo driver.
#
# WHY A SCRIPT AND NOT A LIST OF COMMANDS TO TYPE
#
#   Live typing on stage fails in exactly two ways: a typo that costs ten
#   seconds of silence, and a command that scrolls the one number the whole
#   pitch rests on off the top of the screen. This runs the real commands --
#   nothing here is pre-recorded or replayed from a file -- but it runs them
#   scoped to one family so each beat is one readable screen, and it prints
#   each command before running it so the room can see it is genuine.
#
#   Advance with Enter. That means a question mid-demo costs nothing: stop,
#   answer, press Enter. --auto is for screen recording, where nobody is
#   there to press anything.
#
# USAGE
#   ./tools/pitch.sh --check     preflight; run this before you walk on stage
#   ./tools/pitch.sh             interactive, Enter to advance
#   ./tools/pitch.sh --auto      fixed pauses, for recording
#
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=src
# Prefer the project venv, for exactly the reason the Makefile does: the bare
# `python3` on PATH is not the interpreter this project's dependencies are
# installed into on every machine. A preflight that opens with a red FAIL
# because it asked the wrong interpreter reports a broken toolchain as a broken
# project -- on stage, thirty seconds before you start talking. $PYTHON still
# wins when it is set, so a deliberate choice is never overridden.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
else
  PY=python3
fi

AUTO=0
PAUSE=6
case "${1:-}" in
  --auto)  AUTO=1 ;;
  --check) MODE=check ;;
  "")      ;;
  *)       echo "usage: $0 [--check|--auto]" >&2; exit 2 ;;
esac

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }
cyan()  { printf '\033[36m%s\033[0m\n' "$*"; }

beat() {                       # beat "<title>" "<command>"
  printf '\n'
  bold "── $1"
  printf '\n'
  cyan "\$ $2"
  printf '\n'
  eval "$2"
  hold
}

hold() {
  if [ "$AUTO" = "1" ]; then sleep "$PAUSE"; else
    printf '\n'; dim "[Enter]"; read -r _ || true
  fi
}

# ---------------------------------------------------------------- preflight

if [ "${MODE:-}" = "check" ]; then
  fail=0
  bold "Preflight"
  dim  "interpreter: $PY"
  printf '\n'

  check() {  # check "<label>" "<command>"
    if eval "$2" >/dev/null 2>&1; then
      printf '  \033[32mok\033[0m    %s\n' "$1"
    else
      printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1
    fi
  }

  check "python runs"                 "$PY --version"
  check "pytest installed"            "$PY -m pytest --version"
  check "datasets present"            "test -s data/primary/settlement_detail.csv"
  check "agent reconciles"            "$PY -m recon.match.controller data/primary"
  check "baselines run"               "$PY -m recon.metrics.baselines data/primary"
  check "report runs"                 "$PY -m recon.metrics.report data/primary"
  check "holdout runs"                "$PY tools/holdout.py"
  check "demo page builds"            "$PY -m recon.metrics.dashboard data/primary --out runs/demo/index.html"
  check "demo page is on disk"        "test -s runs/demo/index.html"
  check "exception queue writes"      "$PY -m recon.match.exceptions data/primary --out-dir runs"
  check "queue has rows to show"      "test \$($PY -c \"import csv;print(sum(1 for _ in csv.DictReader(open('runs/primary/exceptions.csv'))))\") -gt 0"

  printf '\n'
  if [ "$fail" = "0" ]; then
    bold "Ready. Nothing on the critical path needs a network or an API key."
  else
    bold "NOT ready -- fix the FAILs above before presenting."; exit 1
  fi
  exit 0
fi

# ------------------------------------------------------------------- the demo

# Build the two run artifacts quietly before the first beat. They are outputs,
# not inputs -- `runs/` is gitignored, so a fresh clone has neither -- and a
# demo that dies on a missing file it was always going to generate itself is a
# self-inflicted wound. Regenerated every time so nothing on screen is stale.
$PY -m recon.match.exceptions data/primary --out-dir runs >/dev/null 2>&1
$PY -m recon.metrics.dashboard data/primary --out runs/demo/index.html >/dev/null 2>&1

clear 2>/dev/null || true
bold "Multi-source Reconciliation Agent"
dim  "Every number on the next four screens is computed live, now."
hold

# 1. The floor. Establish what a plain script already does, before claiming
#    anything -- this is the number almost nobody publishes about themselves.
# `sed -n '1,Np'` rather than `head -N` throughout: head closes the pipe as soon
# as it has its lines, the writer takes SIGPIPE, and Python prints a
# BrokenPipeError traceback -- on the projector, in the middle of the pitch. sed
# reads the stream to the end and stays quiet.
beat "1. What a plain script already solves" \
     "$PY -m recon.metrics.baselines data/primary | sed -n '1,18p'"

# 2. The money shot. B2 buys cases by guessing and books false attributions
#    doing it; the columns are ordered so that trade is visible.
beat "2. The agent, scored against that floor by the same scorer" \
     "$PY -m recon.metrics.report data/primary | sed -n '1,9p'"

# 3. Abstention, priced. The queue is the deliverable, not the failure -- so
#    show the categories and the money behind them, not just a count.
beat "3. What it refuses to guess, and what that refusal is worth" \
     "$PY -m recon.metrics.report data/primary | sed -n '/abstentions/,/no_action/p;/operator queue/,/throughput/p'"

# 3b. One row of the queue, with the evidence a human would work from. This is
#     the "here is why I could not resolve it" the brief asks for, and it is the
#     screen that separates an exception list from a number.
beat "3b. One item, with the reason it is on the desk" \
     "$PY -c \"import csv,textwrap; r=next(csv.DictReader(open('runs/primary/exceptions.csv'))); print('case      ', r['case_id']); print('category  ', r['category']); print('exposure  ', r['exposure_rupees']); print('action    ', textwrap.fill(r['recommended_action'], 72, subsequent_indent='           ')); print('evidence  ', textwrap.fill(r['evidence'][:300], 72, subsequent_indent='           '))\""

# 4. The holdout: seeds nothing in the repo had ever generated or scored.
beat "4. Five seeds nobody had ever run" \
     "$PY tools/holdout.py | tail -12"

# 5. The artifact a human works.
beat "5. The operator's queue, and the page you hand somebody" \
     "$PY -m recon.metrics.dashboard data/primary --out runs/demo/index.html && ls -lh runs/demo/index.html | awk '{print \$5, \$9}'"

printf '\n'
bold "Open runs/demo/index.html -- no server, no JavaScript, no network."
printf '\n'
