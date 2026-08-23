.PHONY: help install data data-large test baseline ambiguity stats verify clean

PYTHON  ?= python3
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
	@echo "make ambiguity  report candidate ambiguity before and after the gates"
	@echo "make stats      regenerate the README dataset table from data/"
	@echo "make verify     test + ambiguity + stats (run before trusting any metric)"
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

ambiguity:
	@$(PYTHON) tools/ambiguity.py data/dev
	@echo ""
	@$(PYTHON) tools/ambiguity.py data/holdout

stats:
	@$(PYTHON) tools/refresh_stats.py

verify: test ambiguity stats

clean:
	@rm -rf data/dev data/holdout
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
