.PHONY: check-specs
check-specs:  ## Verify the repo conforms to /specs
	uv run python scripts/check_specs.py

.PHONY: data
data:  ## Run the Phase 1 data pipeline (raw->clean->synthetic + quality/calibration reports)
	uv run python -m ingestion.pipeline

.PHONY: golden
golden:  ## Rebuild the committed ingestion golden set (real-AACT clean-transform oracle)
	uv run python -m tests.golden.build_golden

.PHONY: eda-report
eda-report:  ## Generate EDA figures + summary into data/eda/ from the cleaned ref_* tables
	uv run python -m ingestion.eda

.PHONY: eda
eda:  ## Open the marimo EDA notebook (interactive view of data/eda outputs + ref_*)
	uv run marimo edit ingestion/eda/notebook.py

.PHONY: db-up
db-up:  ## Start local Postgres + Redis for the backend spine
	docker compose -f docker-compose.dev.yml up -d postgres redis

.PHONY: migrate
migrate:  ## Apply Alembic migrations (RLS schema)
	uv run alembic upgrade head

.PHONY: seed
seed:  ## Seed the two-sponsor isolation fixture
	uv run python -m vigil.seed

.PHONY: api
api:  ## Run the FastAPI app locally
	uv run uvicorn vigil.api.app:app --reload --port 8000

.PHONY: worker
worker:  ## Run the Arq worker
	uv run arq vigil.workers.settings.WorkerSettings

.PHONY: baseline
baseline:  ## Train the Phase-3 baselines on REAL data/clean (metrics + SHAP + card -> data/models)
	uv run python -m models.baselines

.PHONY: leakage
leakage:  ## Run the sacred cross-tenant leakage test
	uv run pytest tests/spine/test_leakage.py -q

.PHONY: test-slow
test-slow:  ## Run the slow full-cohort synthetic regeneration tests (excluded by default)
	uv run pytest -m slow -q

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'
