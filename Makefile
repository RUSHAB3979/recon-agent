.PHONY: help install data data-large test ambiguity stats verify clean

PYTHON  ?= python3
export PYTHONPATH := src

RECORDS      ?= 500
DEV_SEED     ?= 42
HOLDOUT_SEED ?= 20260905

help:
	@echo "make data       generate dev + held-out datasets (500 records each)"
	@echo "make test       run the answer-key self-validation suite"
	@echo "make data-large generate a 2000-record set for stable per-class metrics"
	@echo "make ambiguity  report the intrinsic ambiguity floor of both datasets"
	@echo "make stats      regenerate the README dataset table from data/"
	@echo "make verify     test + ambiguity + stats (run before trusting any metric)"
	@echo "make clean      remove generated data"

install:
	$(PYTHON) -m pip install -r requirements.txt

data:
	@$(PYTHON) -m recon.datagen.cli --records $(RECORDS) --seed $(DEV_SEED)     --out data/dev
	@echo ""
	@$(PYTHON) -m recon.datagen.cli --records $(RECORDS) --seed $(HOLDOUT_SEED) --out data/holdout --quiet
	@echo "held-out set written to data/holdout/ (seed $(HOLDOUT_SEED))"

data-large:
	@$(PYTHON) -m recon.datagen.cli --records 2000 --seed $(DEV_SEED) --out data/large

test:
	@$(PYTHON) -m pytest tests/ -q

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
