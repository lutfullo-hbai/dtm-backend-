.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

UV_RUN := uv run --frozen
PY_PATHS := app tests scripts migrations
# The rule is "noqa only with a reason". Matched case-insensitively, this finds a line
# or file-level (`# ruff: noqa`, `# flake8: noqa`) suppression whose codes are followed
# by nothing, or by an empty `#` comment.
NOQA_CODES := [A-Z]+[0-9]+([[:space:]]*,[[:space:]]*[A-Z]+[0-9]+)*
NOQA_WITHOUT_REASON := \#[[:space:]]*((ruff|flake8)[[:space:]]*:[[:space:]]*)?noqa([[:space:]]*:[[:space:]]*$(NOQA_CODES))?[[:space:]]*(\#[[:space:]]*)?$$
# A feature module's __init__.py must stay empty of imports: a re-export there would
# let other modules reach its repository or models without import-linter noticing.
MODULE_INITS_DIR := app/modules
# Local compose stack (compose.yaml); the image is labelled with the current commit.
COMPOSE := VCS_REF=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown) docker compose
READY_TIMEOUT_SECONDS := 60

# CI (.github/workflows/ci.yml) runs these targets, so CI and a local run execute the
# same commands: ci-lint, typecheck, test, test-migrations, migrations-roundtrip,
# openapi-check and image.
IMAGE := dtm-backend:ci
VCS_REF ?= $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
APP_UID := 10001

.PHONY: help install lint ci-lint noqa-check module-init-check format typecheck test \
	test-unit test-integration test-migrations test-clean migrations-roundtrip openapi \
	openapi-check migrate migration downgrade image up down logs ps migrate-docker

# The name column is as wide as the longest target name, so every description aligns.
help: ## List available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' \
		'{ name[NR] = $$1; text[NR] = $$2; if (length($$1) > width) width = length($$1) } \
		END { for (i = 1; i <= NR; i++) printf "  %-" width "s  %s\n", name[i], text[i] }'

install: ## Install locked dependencies (incl. dev group)
	uv sync --frozen

# The pre-commit hook runs exactly this target; CI runs its two halves as the `lint`
# and `typecheck` jobs, so the hook and CI cannot drift apart.
lint: ## All static checks (no file changes): ci-lint, then typecheck
	$(MAKE) --no-print-directory ci-lint
	$(MAKE) --no-print-directory typecheck

ci-lint: ## Style, import contracts and code-quality checks (lint without mypy)
	$(UV_RUN) ruff check $(PY_PATHS)
	$(UV_RUN) ruff format --check $(PY_PATHS)
	$(UV_RUN) lint-imports --no-logo
	$(UV_RUN) vulture
	$(UV_RUN) python -m scripts.check_file_length
	$(MAKE) --no-print-directory noqa-check
	$(MAKE) --no-print-directory module-init-check

noqa-check: ## Fail on `# noqa` without a reason comment
	@if grep -rniE --include='*.py' '$(NOQA_WITHOUT_REASON)' $(PY_PATHS); then \
		echo 'noqa without a reason: write `# noqa: CODE  # why`' >&2; exit 1; \
	fi

module-init-check: ## Fail if a module's __init__.py imports (re-exports) anything
	@if find $(MODULE_INITS_DIR) -name '__init__.py' -print0 \
		| xargs -0 -r grep -nHE '^[[:space:]]*(import|from)[[:space:]]'; then \
		echo 'import in a module __init__.py: import from the module file itself' >&2; exit 1; \
	fi

format: ## Apply ruff fixes and formatting
	$(UV_RUN) ruff check --fix $(PY_PATHS)
	$(UV_RUN) ruff format $(PY_PATHS)

typecheck: ## mypy --strict over app, tests, scripts and migrations
	$(UV_RUN) mypy

# Integration tests start PostgreSQL and Redis containers (testcontainers) and fail,
# never skip, when Docker is unavailable.
test: ## All tests with branch coverage (fails under 80%); needs Docker
	$(UV_RUN) pytest --cov --cov-report=term

test-unit: ## Tests that need no Docker
	$(UV_RUN) pytest -m unit

test-integration: ## Tests against real PostgreSQL and Redis containers; needs Docker
	$(UV_RUN) pytest -m integration

test-migrations: ## Migration history tests (pytest-alembic); needs Docker
	$(UV_RUN) pytest tests/migrations

# Normally nothing is left: fixtures remove their containers, also on Ctrl-C/SIGTERM;
# only a killed run (SIGKILL) leaks them. This removes every testcontainers container
# on this Docker daemon, including those of another test run still in progress.
test-clean: ## Remove leftover test containers (label org.testcontainers=true)
	docker ps -aq --filter label=org.testcontainers=true | xargs -r docker rm -f

# Through the alembic CLI and env.py, as the one-off migrate process runs in production.
# Needs DATABASE_URL (and the other required settings) for a disposable database.
# `downgrade base` drops every table: the target refuses to run unless APP_ENV=test
# (as in CI), so a shell pointed at a real database cannot wipe it by accident.
migrations-roundtrip: ## upgrade head, downgrade base, upgrade head, then alembic check
	@if [ "$${APP_ENV:-}" != "test" ]; then \
		echo 'migrations-roundtrip drops every table (downgrade base): it runs only' >&2; \
		echo 'with APP_ENV=test against a disposable database.' >&2; \
		exit 2; \
	fi
	$(UV_RUN) alembic upgrade head
	$(UV_RUN) alembic downgrade base
	$(UV_RUN) alembic upgrade head
	$(UV_RUN) alembic check

openapi: ## Regenerate docs/api/openapi.json
	$(UV_RUN) python -m scripts.export_openapi

openapi-check: ## Fail if docs/api/openapi.json is stale
	$(UV_RUN) python -m scripts.export_openapi --check

# Migrations run as a one-off process against DATABASE_URL (the app never migrates).
migrate: ## Apply all migrations (alembic upgrade head)
	$(UV_RUN) alembic upgrade head

migration: ## Autogenerate a migration: make migration name="add users"; then review it
	@if [ -z "$(name)" ]; then echo 'usage: make migration name="short description"' >&2; exit 2; fi
	$(UV_RUN) alembic revision --autogenerate -m "$(name)"

downgrade: ## Revert the latest migration (alembic downgrade -1)
	$(UV_RUN) alembic downgrade -1

image: ## Build the API image (labelled with VCS_REF) and check it runs as non-root
	docker build --build-arg VCS_REF=$(VCS_REF) -t $(IMAGE) .
	@uid=$$(docker run --rm --entrypoint id $(IMAGE) -u); \
	if [ "$$uid" != "$(APP_UID)" ]; then echo "image runs as uid $$uid, expected $(APP_UID)" >&2; exit 1; fi
	docker run --rm --entrypoint python $(IMAGE) -c 'import app.main'

.env:
	cp .env.example .env
	@echo 'Created .env from .env.example (local-only placeholder values).'

# In order: dependencies healthy -> migrations applied -> API started. compose runs the
# one-off `migrate` before `api` (depends_on: service_completed_successfully) and fails
# if it fails; its exited container stays, so `docker compose restart api` still works.
up: .env ## Build and start the local stack; wait until /ready answers 200
	$(COMPOSE) build
	$(COMPOSE) up -d --wait postgres redis
	$(COMPOSE) up -d api || { $(COMPOSE) logs --no-log-prefix migrate >&2; \
		echo 'migrations failed (see above)' >&2; exit 1; }
	@port=$$(sed -n 's/^API_HOST_PORT=//p' .env); url="http://127.0.0.1:$${port:-8000}"; \
	deadline=$$(( $$(date +%s) + $(READY_TIMEOUT_SECONDS) )); \
	until curl -fs -o /dev/null "$$url/ready"; do \
		if [ "$$(date +%s)" -ge "$$deadline" ]; then \
			echo "API not ready after $(READY_TIMEOUT_SECONDS)s; see: make logs" >&2; exit 1; \
		fi; \
		sleep 1; \
	done; \
	echo "API ready: $$url (docs: $$url/docs)"

down: ## Stop and remove the local stack's containers (the database volume is kept)
	$(COMPOSE) down

logs: ## Follow the local stack's logs
	$(COMPOSE) logs -f

ps: ## Show the local stack's containers
	$(COMPOSE) ps -a

migrate-docker: .env ## Apply migrations in the local stack (one-off container)
	$(COMPOSE) build
	$(COMPOSE) run --rm migrate
