.PHONY: help install data data-large test baseline agent adjudicate report ambiguity audit exceptions demo holdout stats verify verify-audit clean

# Prefer the project environment; an empty interpreter would hide recipe failures.
VENV_PYTHON := $(firstword $(wildcard .venv/bin/python .venv/Scripts/python.exe))
PYTHON ?= $(if $(strip $(VENV_PYTHON)),$(VENV_PYTHON),python3)
ifeq ($(strip $(PYTHON)),)
$(error PYTHON resolved to an empty string -- refusing to run recipes that would silently succeed)
endif
export PYTHONPATH := src

RECORDS      ?= 500

DEV_SEED     ?= 42
PRIMARY_SEED ?= 20260905
STRESS_SEED  ?= 101

help:
	@echo "make data       generate dev + primary + stress datasets (500 records each)"
	@echo "make test       run the full test suite"
	@echo "make data-large generate a 2000-record set for stable per-class metrics"
	@echo "make baseline   run B1, B2, B3 and report the benchmark difficulty floor D"
	@echo "make agent      run the reconciliation agent and print its per-pass yield"
	@echo "make adjudicate run the agent WITH the evidence-reading rung (opt-in, not a published number)"
	@echo "make report     score the agent against B1 and B2 through the shared scorer"
	@echo "make ambiguity  report candidate ambiguity before and after the gates"
	@echo "make stats      regenerate the README dataset table from data/"
	@echo "make audit      write a decision journal per family to runs/"
	@echo "make exceptions write the operator queue per family to runs/"
	@echo "make demo       write the self-contained run report to runs/demo/index.html"
	@echo "make holdout    reproduce the recorded holdout evaluation (see docs/HOLDOUT.md)"
	@echo "make verify-audit  re-verify the tamper-evident hash chain of every audit log"
	@echo "make verify     test + baseline + report + ambiguity + audit chain + exceptions + demo + stats"
	@echo "make clean      remove sample data and generated outputs"

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

baseline:
	@$(PYTHON) -m recon.metrics.baselines data/dev data/primary data/stress

agent:
	@$(PYTHON) -m recon.match.controller data/dev data/primary data/stress

adjudicate:
	@$(PYTHON) -m recon.match.controller data/dev --adjudicate

report:
	@$(PYTHON) -m recon.metrics.report data/dev data/primary data/stress

ambiguity:
	@$(PYTHON) tools/ambiguity.py data/dev
	@echo ""
	@$(PYTHON) tools/ambiguity.py data/primary
	@echo ""
	@$(PYTHON) tools/ambiguity.py data/stress

audit:
	@$(PYTHON) -m recon.match.controller data/dev data/primary data/stress --audit-dir runs

exceptions:
	@$(PYTHON) -m recon.match.exceptions data/dev data/primary data/stress --out-dir runs

demo:
	@$(PYTHON) -m recon.metrics.dashboard data/dev data/primary data/stress --out runs/demo/index.html

holdout:
	@$(PYTHON) tools/holdout.py

stats:
	@$(PYTHON) tools/refresh_stats.py

verify-audit:
	@$(PYTHON) -m recon.match.audit runs

verify: test baseline report ambiguity audit exceptions demo verify-audit stats

clean:
	@rm -rf data/dev data/primary data/stress data/large runs
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
