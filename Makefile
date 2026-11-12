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

.PHONY: help install lint noqa-check module-init-check format typecheck test test-unit \
	test-integration test-clean openapi openapi-check migrate migration downgrade

help: ## List available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install locked dependencies (incl. dev group)
	uv sync --frozen

# The pre-commit hook runs exactly this target, so the hook and CI cannot drift apart.
lint: ## All static checks (no file changes)
	$(UV_RUN) ruff check $(PY_PATHS)
	$(UV_RUN) ruff format --check $(PY_PATHS)
	$(UV_RUN) mypy
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

# Normally nothing is left: fixtures remove their containers, also on Ctrl-C/SIGTERM;
# only a killed run (SIGKILL) leaks them. This removes every testcontainers container
# on this Docker daemon, including those of another test run still in progress.
test-clean: ## Remove leftover test containers (label org.testcontainers=true)
	docker ps -aq --filter label=org.testcontainers=true | xargs -r docker rm -f

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
