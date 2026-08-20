# Convenience targets. Everything here is a thin wrapper over `python -m bench`,
# so you can always drop down to the CLI if you need a flag the Makefile does
# not expose.

PY ?= python3
PLATFORMS ?= cognodb neo4j_aura memgraph falkordb arangodb

.PHONY: help install dataset doctor bench report selftest test clean all

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## create a venv and install pinned dependencies
	$(PY) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo "Now: source .venv/bin/activate && cp .env.example .env"

dataset: ## download, sample and freeze the benchmark dataset
	$(PY) -m bench dataset

doctor: ## verify credentials and connectivity for every platform
	$(PY) -m bench doctor

bench: ## run the full benchmark on every platform
	$(PY) -m bench run $(foreach p,$(PLATFORMS),--platform $(p)) -v

report: ## regenerate RESULTS.md and the charts
	$(PY) -m bench report

selftest: ## exercise the whole pipeline offline against the mock backend
	$(PY) -m bench dataset --fixture
	$(PY) -m bench selftest

test: ## run the unit tests
	$(PY) -m pytest tests -q

all: dataset doctor bench report ## the whole thing, in order

clean: ## remove generated results (not the dataset)
	rm -rf results/raw/*.json results/charts/*.png results/shared_inputs.json RESULTS.md
