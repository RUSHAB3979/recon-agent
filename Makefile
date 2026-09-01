.PHONY: help install data data-large test baseline agent adjudicate report ambiguity audit exceptions demo holdout stats verify verify-audit clean

# Prefer the project venv when one exists. The bare `python3` on PATH is not
# the interpreter this project's dependencies are installed into on every
# machine, and a `make verify` that cannot run is worse than no target at all --
# it reports a broken toolchain as a broken project.
#
# $(wildcard) over BOTH candidates in ONE call, then $(firstword): the earlier
# form was two separate $(wildcard) calls joined by a literal space, so on a
# machine with no .venv -- every CI runner -- it expanded to a string that was
# whitespace rather than empty. $(if) strips its literal argument text, not the
# result of expanding it, so that whitespace tested TRUE, $(firstword) of it was
# empty, and PYTHON became empty. `@$(PYTHON) -m pytest` then expanded to
# `@ -m pytest`, in which make reads the leading `-` as the ignore-errors recipe
# prefix: the step ran `m`, failed, and was reported as a SUCCESS. A test target
# that passes without running the tests is the dangerous half of that bug, so
# the guard below refuses to build rather than let it happen again.
VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/Scripts/python.exe))
PYTHON ?= $(if $(strip $(VENV_PYTHON)),$(VENV_PYTHON),python3)
ifeq ($(strip $(PYTHON)),)
$(error PYTHON resolved to an empty string -- refusing to run recipes that would silently succeed)
endif
export PYTHONPATH := src

RECORDS      ?= 500

# Three families, three jobs.  DEV is the only surface any threshold may be
# tuned against; it carries every scenario class, because a tuning set that
# lacks the phenomenon being tuned for freezes the abstention threshold at zero
# and the agent then never abstains.  PRIMARY produces the headline number and
# is never inspected while tuning.  STRESS enriches the rare classes so a
# per-class rate is measured on more than three cases.
DEV_SEED     ?= 42
PRIMARY_SEED ?= 20260905
STRESS_SEED  ?= 101

help:
	@echo "make data       generate dev + primary + stress datasets (500 records each)"
	@echo "make test       run the answer-key self-validation suite"
	@echo "make data-large generate a 2000-record set for stable per-class metrics"
	@echo "make baseline   run B1 and report the benchmark difficulty floor D"
	@echo "make agent      run the reconciliation agent and print its per-pass yield"
	@echo "make adjudicate run the agent WITH the evidence-reading rung (opt-in, not a published number)"
	@echo "make report     score the agent against B1 and B2 through the shared scorer"
	@echo "make ambiguity  report candidate ambiguity before and after the gates"
	@echo "make stats      regenerate the README dataset table from data/"
	@echo "make audit      write a decision journal per family to runs/"
	@echo "make exceptions write the operator queue per family to runs/"
	@echo "make demo       write the self-contained run report to runs/demo/index.html"
	@echo "make holdout    run the frozen agent on never-seen seeds (once; see tools/holdout.py)"
	@echo "make verify-audit  re-verify the tamper-evident hash chain of every audit log"
	@echo "make verify     test + baseline + report + ambiguity + audit chain + exceptions + demo + stats"
	@echo "make clean      remove generated data"

install:
	$(PYTHON) -m pip install -r requirements.txt

data:
	@$(PYTHON) -m recon.datagen.cli --records $(RECORDS) --seed $(DEV_SEED)  --family development --out data/dev
	@echo ""
	@$(PYTHON) -m recon.datagen.cli --records $(RECORDS) --seed $(PRIMARY_SEED)  --family primary --out data/primary --quiet
	@echo "primary batch written to data/primary/ (seed $(PRIMARY_SEED)) -- never tune here"
	@$(PYTHON) -m recon.datagen.cli --records $(RECORDS) --seed $(STRESS_SEED)  --family stress --out data/stress --quiet
	@echo "stress batch written to data/stress/  (seed $(STRESS_SEED)) -- never tune here"

data-large:
	@$(PYTHON) -m recon.datagen.cli --records 2000 --seed $(PRIMARY_SEED)  --family primary --out data/large

test:
	@$(PYTHON) -m pytest tests/ -q

# B1 is the floor every headline number is quoted against. It ships as runnable
# code so a sceptic can reproduce D without trusting the generator.
baseline:
	@$(PYTHON) -m recon.metrics.baselines data/dev data/primary data/stress

# The agent itself. Prints what each rung of the ladder actually contributed,
# so a rung that resolves nothing on real data is visible as a rung to delete.
agent:
	@$(PYTHON) -m recon.match.controller data/dev data/primary data/stress

# The evidence-reading rung, on the development family only. Deliberately not
# part of `make report` or `make verify`: the published figures must not depend
# on a network call, an API key, or a model's mood. With no ANTHROPIC_API_KEY in
# the environment this selects the declining reader and reproduces the
# deterministic result, which is what CI measures.
adjudicate:
	@$(PYTHON) -m recon.match.controller data/dev --adjudicate

# One scorer, two consumers: the agent and the published floor are measured by
# the same instrument, so the gap between them is attributable to capability.
report:
	@$(PYTHON) -m recon.metrics.report data/dev data/primary data/stress

ambiguity:
	@$(PYTHON) tools/ambiguity.py data/dev
	@echo ""
	@$(PYTHON) tools/ambiguity.py data/primary
	@echo ""
	@$(PYTHON) tools/ambiguity.py data/stress

# Every decision the agent took, sealed into a hash chain. Rule 5 of this
# project is that every decision is logged and human-overridable; a module
# that CAN log while the pipeline never DOES is not compliance, it is a
# library. This target is what makes `make verify-audit` verify something.
audit:
	@$(PYTHON) -m recon.match.controller data/dev data/primary data/stress --audit-dir runs

# The exceptions the agent could not resolve, ranked by the money behind
# them. Rule 4 of this project is that unmatched rows are never hidden, and
# this is the artifact that discharges it: a file an operator works through,
# not a count in a report.
exceptions:
	@$(PYTHON) -m recon.match.exceptions data/dev data/primary data/stress --out-dir runs

# The demo surface. One HTML file with no server, no build step, no network
# access and no JavaScript: it opens from disk, prints to PDF and attaches to
# an email. Every figure on it is computed by the run that writes it, through
# the same scorer `make report` prints from -- a dashboard that computed its
# own numbers could flatter the run in ways the terminal never showed, and
# that agreement is a test rather than a convention.
demo:
	@$(PYTHON) -m recon.metrics.dashboard data/dev data/primary data/stress --out runs/demo/index.html

# The holdout. Seeds nothing in this repository has ever generated, scored or
# looked at, run once against the frozen ladder. Deliberately NOT part of
# `make verify`: a holdout that runs on every commit is a validation set with
# a longer name, and the whole value of this one is that its result was seen
# exactly once, after the protocol in tools/holdout.py was committed.
holdout:
	@$(PYTHON) tools/holdout.py

stats:
	@$(PYTHON) tools/refresh_stats.py

# An audit trail nothing re-checks is a log file. This recomputes every
# record_hash from the record it claims to seal and fails on the first break.
# Journals live under runs/ rather than data/. A dataset directory is an
# input and stays read-only; writing a run's output into it would make the
# byte-for-byte regeneration check in CI compare a run against itself.
#
# The directory is passed through and expanded by the module, not by
# $(wildcard): make expands wildcards when it parses the file, so `make
# verify` would otherwise check the logs that existed BEFORE its own
# `audit` prerequisite ran -- on a clean tree, none.
verify-audit:
	@$(PYTHON) -m recon.match.audit runs

verify: test baseline report ambiguity audit exceptions demo verify-audit stats

clean:
	@rm -rf data/dev data/primary data/stress data/large runs
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
