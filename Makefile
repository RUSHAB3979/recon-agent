.PHONY: help install data data-large test baseline agent report ambiguity stats verify clean

# Prefer the project venv when one exists. The bare `python3` on PATH is not
# the interpreter this project's dependencies are installed into on every
# machine, and a `make verify` that cannot run is worse than no target at all --
# it reports a broken toolchain as a broken project.
VENV_PYTHON := $(wildcard .venv/Scripts/python.exe) $(wildcard .venv/bin/python)
PYTHON  ?= $(if $(VENV_PYTHON),$(firstword $(VENV_PYTHON)),python3)
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
	@echo "make report     score the agent against B1 and B2 through the shared scorer"
	@echo "make ambiguity  report candidate ambiguity before and after the gates"
	@echo "make stats      regenerate the README dataset table from data/"
	@echo "make verify     test + baseline + report + ambiguity + stats (run before trusting any metric)"
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

stats:
	@$(PYTHON) tools/refresh_stats.py

verify: test baseline report ambiguity stats

clean:
	@rm -rf data/dev data/primary data/stress data/large
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
