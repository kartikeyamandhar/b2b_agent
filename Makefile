# RecallOps backend tasks. Run from the repository root.
SHELL := /bin/bash
.DEFAULT_GOAL := help

API := apps/api
# Only the values make itself needs are read from .env, and an environment variable always wins (?=), so
# `ADJUDICATOR=rules_only make worker` reaches the process. Python reads .env itself (pydantic-settings), where
# environment variables also win. Including the whole .env would turn every line into a make assignment that
# overrides the caller's environment (final review, decisions log 2026-10-19).
env_value = $(shell test -f .env && sed -n 's/^$(1)=//p' .env | tail -n 1)

POSTGRES_PORT ?= $(or $(call env_value,POSTGRES_PORT),55432)
RECALLOPS_OWNER_PASSWORD ?= $(or $(call env_value,RECALLOPS_OWNER_PASSWORD),owner-dev-only)
RECALLOPS_APP_PASSWORD ?= $(or $(call env_value,RECALLOPS_APP_PASSWORD),app-dev-only)
RECALLOPS_INTERNAL_PASSWORD ?= $(or $(call env_value,RECALLOPS_INTERNAL_PASSWORD),internal-dev-only)

# psycopg[binary] bundles libpq, so no system libpq is needed. LIBPQ_DIR is only a fallback for the pure-Python
# implementation; it is passed per command because the macOS system shell strips DYLD_* variables.
LIBPQ_DIR ?= $(firstword $(wildcard /opt/homebrew/opt/libpq/lib /opt/homebrew/opt/postgresql@16/lib /opt/homebrew/opt/postgresql@15/lib /usr/local/opt/libpq/lib))
ENV := DYLD_FALLBACK_LIBRARY_PATH=$(LIBPQ_DIR) PYTHONPATH=$(CURDIR)/$(API)/src
RUN := $(ENV) uv run --project $(API) --frozen
PY := $(RUN) python
PSQL := docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -q
DATABASES := recallops recallops_test

.PHONY: help setup db-up db-bootstrap migrate downgrade api dev worker export-payouts graph-sync seed test lint format typecheck check eval ingest label profile openapi readme-assets

help: ## List targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install pinned deps, start Postgres, create roles and databases, run migrations
	uv sync --project $(API) --frozen
	docker compose up -d --wait --pull never
	$(MAKE) db-bootstrap migrate

db-up: ## Start Postgres and wait until healthy
	docker compose up -d --wait --pull never

db-bootstrap: ## Create or re-assert roles and databases (idempotent)
	@for db in $(DATABASES); do \
	  $(PSQL) -v owner_password='$(RECALLOPS_OWNER_PASSWORD)' -v app_password='$(RECALLOPS_APP_PASSWORD)' \
	    -v internal_password='$(RECALLOPS_INTERNAL_PASSWORD)' -v dbname=$$db -f - < $(API)/sql/bootstrap_roles.sql || exit 1; \
	  echo "bootstrapped $$db"; \
	done

migrate: ## Upgrade both databases to head as recallops_owner
	@for db in $(DATABASES); do \
	  (cd $(API) && $(ENV) uv run --frozen alembic -x dburl=postgresql://recallops_owner:$(RECALLOPS_OWNER_PASSWORD)@localhost:$(POSTGRES_PORT)/$$db upgrade head) || exit 1; \
	done

downgrade: ## Downgrade the test database to base (reversibility check)
	cd $(API) && $(ENV) uv run --frozen alembic -x dburl=postgresql://recallops_owner:$(RECALLOPS_OWNER_PASSWORD)@localhost:$(POSTGRES_PORT)/recallops_test downgrade base

api: ## Run the API on http://127.0.0.1:8000
	$(RUN) uvicorn recallops.api.app:app --host 127.0.0.1 --port 8000 --no-access-log

dev: ## Run the API with auto-reload
	$(RUN) uvicorn recallops.api.app:app --host 127.0.0.1 --port 8000 --no-access-log --reload --reload-dir $(API)/src

worker: ## Run the adjudication worker (ADJUDICATOR=agent needs LLM_MODE=live; rules_only runs offline)
	$(PY) -m recallops.claims.worker $(ARGS)

export-payouts: ## Write the check/ACH export file for payouts in pending_export and mark them exported
	$(PY) -m recallops.payouts.export $(ARGS)

graph-sync: ## Rebuild the Neo4j fraud graph and fraud_clusters from Postgres (every program; ARGS="--program <id>")
	$(PY) -m recallops.fraud sync $(if $(ARGS),$(ARGS),--all)

seed: ## Load one real recall, open a program, generate and adjudicate 500 synthetic claims
	$(PY) -m recallops.seed

ingest: ## Load the saved CPSC archive windows into recalls (offline)
	$(PY) -m recallops.ingest.runner --source cpsc --from-dir data/raw/cpsc

test: ## Run pytest with coverage (needs make setup)
	cd $(API) && $(ENV) uv run --frozen pytest --cov --cov-report=term

lint: ## Ruff lint and format check
	cd $(API) && uv run --frozen ruff check src tests ../../docs/assets/src && uv run --frozen ruff format --check src tests ../../docs/assets/src

format: ## Apply ruff formatting
	cd $(API) && uv run --frozen ruff format src tests && uv run --frozen ruff check --fix src tests

typecheck: ## mypy strict
	cd $(API) && uv run --frozen mypy

check: lint typecheck test ## Everything CI runs for the backend

eval: ## Run every eval in replay mode and rewrite evals/results/*.md
	$(PY) -m recallops.evals.run_all

label: ## Owner audit or blind labeling of golden notices: make label ARGS="--audit --source-id 24082"
	$(PY) evals/extraction/label.py $(ARGS)

profile: ## Recompute the data profile numbers into evals/results/data_profile.json (needs data/raw)
	$(PY) -m recallops.data_profile

readme-assets: ## Rebuild the README charts in docs/assets from evals/results and the seeded database (needs make seed)
	$(PY) docs/assets/src/build_assets.py $(ARGS)

openapi: ## Regenerate docs/contracts/openapi.json from the app
	$(PY) -m recallops.api.export_openapi docs/contracts/openapi.json
