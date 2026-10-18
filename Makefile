# Developer entry points. Backend tooling runs through uv (pinned Python 3.12 + uv.lock).
SHELL := /bin/bash
UV ?= uv
COMPOSE ?= docker compose
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help dev up down logs infra migrate seed test test-backend test-frontend test-e2e \
        lint format typecheck gen-client eval install hooks clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-15s %s\n",$$1,$$2}'

install: ## Install backend + frontend deps locally
	$(BACKEND) $(UV) sync
	$(FRONTEND) npm ci

hooks: ## Install pre-commit hooks
	$(BACKEND) $(UV) run pre-commit install --config ../.pre-commit-config.yaml

dev: ## Start the whole stack (foreground)
	$(COMPOSE) up --build

up: ## Start the whole stack (background, waits for health)
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Tail logs
	$(COMPOSE) logs -f --tail=100

infra: ## Start only Postgres, Redis, MinIO (for local tests)
	$(COMPOSE) up -d --wait postgres redis minio

migrate: ## Apply DB migrations (inside the api container)
	$(COMPOSE) exec api alembic upgrade head

seed: ## Load synthetic seed data (idempotent: wipes and reloads)
	$(COMPOSE) exec api python -m seed.seed

test: test-backend test-frontend ## Run all tests

test-backend: infra ## Backend tests against real Postgres+pgvector and Redis
	$(BACKEND) $(UV) run pytest -q

test-frontend: ## Frontend unit tests (Vitest)
	$(FRONTEND) npm run test

test-e2e: ## Playwright flows (needs `make up` + `make seed`)
	$(FRONTEND) npx playwright test

lint: ## ruff + eslint + prettier
	$(BACKEND) $(UV) run ruff check . ../eval && $(UV) run ruff format --check . ../eval
	$(FRONTEND) npm run lint

format: ## Auto-format everything
	$(BACKEND) $(UV) run ruff check --fix . && $(UV) run ruff format .
	$(FRONTEND) npm run format

typecheck: ## mypy (strict on domain/services) + tsc --noEmit
	$(BACKEND) $(UV) run mypy && $(UV) run mypy ../eval
	$(FRONTEND) npm run typecheck

gen-client: ## Export OpenAPI and regenerate the typed frontend client
	$(BACKEND) $(UV) run python -m app.export_openapi openapi.json
	$(FRONTEND) npm run gen:api

eval: infra ## Offline evaluation suite (own fresh DB) -> eval/reports/REPORT.md
	$(BACKEND) PYTHONPATH=.. $(UV) run python -m eval.run_all

clean:
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/dist
