# Backend conventions (M0)

| | |
|---|---|
| Status | Binding for all backend code, as of milestone M0 |
| Owner | Project owner (backend maintainer) |
| Update when | A convention below changes — in the same PR as the code, so this file and the code never disagree |

The rules every change to this repository follows: the stack, configuration, the HTTP and error contract, logging,
the database layer, the resource budget and the quality gates. Each rule names where it is enforced; a rule without
an automated check says so. [Architecture](architecture.md) explains the shape of the system and
[the ADRs](adr/README.md) the decisions behind it; this file is the checklist for writing code.

## 1. Stack and structure

- Python 3.13.15 (`.python-version`, the Docker image), dependencies locked with uv (`uv.lock`).
- FastAPI, SQLAlchemy 2 async with asyncpg, Alembic, pydantic-settings, structlog, uuid-utils (UUIDv7), uvicorn.
- PostgreSQL 18.6 and Redis 8.10.3: the same image tags in `compose.yaml`, the test containers
  (`tests/conftest.py`) and CI; `tests/unit/test_compose.py` and `tests/unit/test_ci_workflow.py` fail if they
  differ.
- Tooling: ruff (lint and format), mypy `--strict`, import-linter, vulture, pre-commit, commitizen.
- Modular monolith: shared infrastructure in `app/core`, features in `app/modules/<name>/` with
  `router.py`, `service.py`, `repository.py`, `schemas.py`, `models.py` (only `service.py` is mandatory).
- Dependency direction, enforced by import-linter (`[tool.importlinter]` in `pyproject.toml`):
  - `app.core` never imports `app.modules` or `app.main`;
  - inside a module: router → service → repository → schemas → models, never upwards;
  - routers never import repositories, SQLAlchemy or Redis directly;
  - services and repositories never import FastAPI or Starlette;
  - services never import SQLAlchemy, asyncpg or Redis; the session and engine types they need are re-exported
    by `app.core.db`;
  - a module reaches another module only through that module's service.
- A module's `__init__.py` contains no imports (`make module-init-check`), so re-exports cannot bypass these rules.

## 2. Configuration (Twelve-Factor III)

Settings come only from environment variables (`app/core/config.py`; no `.env` file is read by the app). They are
validated when the application is built, and the process refuses to start on an invalid value. Error messages name
the variable and the problem, never the value.

| Variable | Default | Rule |
|---|---|---|
| `APP_ENV` | required | `local`, `test`, `staging` or `production` |
| `DATABASE_URL` | required | `postgresql+asyncpg://`, fully parsed: valid host, port 1–65535, database name; the only query parameter allowed is `ssl` (`disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`) |
| `REDIS_URL` | required | `redis://` or `rediss://`, valid host and port, database `/N`, no query parameters, no fragment |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` or `console` (console is for humans, locally) |
| `DB_POOL_SIZE` | `5` | 1–50 |
| `DB_MAX_OVERFLOW` | `5` | 0–50 |
| `DB_POOL_TIMEOUT_SECONDS` | `5` | > 0, finite |
| `DB_STATEMENT_TIMEOUT_MS` | `5000` | integer > 0 |
| `SLOW_QUERY_MS` | `100` | integer > 0 |
| `READINESS_TIMEOUT_SECONDS` | `2` | > 0, finite |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `2` | > 0, finite |
| `REDIS_MAX_CONNECTIONS` | `20` | 1–200 |

`APP_ENV` is reserved for environment-specific behaviour. In M0 no code reads it yet, and it is not written to the
log. `.env.example` lists every variable with local-only placeholder values; `.env` is never committed.

## 3. HTTP contract

**Operational endpoints** (unversioned; business endpoints go under `/api/v1` from M2):

- `GET /health` → `200 {"status": "ok"}`. Never touches PostgreSQL or Redis.
- `GET /ready` → `200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`, or `503` problem (below)
  with `"checks": {"database": "ok|unavailable", "redis": "ok|unavailable"}` and `code` `not_ready`. The database
  check (`SELECT 1`) and the Redis check (`PING`) run concurrently and are bounded together by
  `READINESS_TIMEOUT_SECONDS`. Single-flight per process: concurrent callers share one in-flight check.

**Errors** are RFC 9457 `application/problem+json` (`app/core/errors`):

- Members: `type` (`/problems/<code with dashes>`), `title`, `status`, `detail` (always a string), `code` (stable,
  snake_case: the client's key), `request_id`, plus extension members where documented.
- `422 validation_error` with `errors: [{loc, message, type}]`; the rejected input is never echoed.
- `404 not_found`, `405 method_not_allowed` (the `Allow` header is kept), any other HTTP error
  `http_<status>` with its headers kept. `204` and `304` stay bodiless.
- An unhandled exception is `500 internal_error` with a generic detail; the traceback goes to the log only.
- Expected failures are `AppError` subclasses that set `status`, `code` and `title`.

**Request id.** `X-Request-ID` is reused when it matches `^[A-Za-z0-9._-]{1,128}$`, otherwise a UUIDv7 is
generated. It is set on every response, errors included, equals the body's `request_id`, and is bound to every log
line of the request. The request-context middleware owns it (`request.state.request_id`).

## 4. Logging

- structlog, one JSON object per line on stdout (`LOG_FORMAT=console` for humans). Every line has `timestamp`
  (RFC 3339, UTC), `level`, `event` and `logger`; lines written while serving a request also have `request_id`.
  Stdlib loggers (uvicorn, SQLAlchemy, Alembic) go through the same pipeline.
- One `http_request` event per request: `method`, `path` (never the query string), `status`, `duration_ms`, and
  `aborted: true` when a started response was cut short. Level: a successful `/health` at DEBUG, `503` at WARNING,
  any other 5xx (or an aborted or never-started response) at ERROR, everything else at INFO. Uvicorn's own access
  log is off.
- Never logged: secrets, `Authorization`, cookies, query strings, bound SQL parameters, personal data, local
  variables of a traceback.
- Database errors: the driver's and PostgreSQL's messages quote values (a rejected parameter, a violating row in a
  DETAIL line), so they are kept out of the log; a server error's value-free identifiers are logged instead as
  `db_error` (`sqlstate`, `constraint_name`, `table_name`, `column_name`, ...). The mechanism is described in the
  docstring of `app/core/logging.py`.
- **Rule:** never put `str(error)` or `repr(error)` of a database error into a log field. Log the exception itself
  (`logger.exception(...)` or `exc_info=True`) so the redaction sees it.
- `slow_query` at WARNING for a statement slower than `SLOW_QUERY_MS`: `statement` (truncated to 500 characters,
  never parameters) and `duration_ms`. A failed statement adds `failed`, `timed_out`, `error_type` and `sqlstate`,
  never the error message; a statement cancelled by `statement_timeout` (SQLSTATE 57014) is logged whatever
  `SLOW_QUERY_MS` is.

## 5. Database

- The engine is built in the application lifespan: pool size, overflow and timeout from configuration,
  `pool_pre_ping`, `hide_parameters=True`, and `statement_timeout` set on every connection
  (`DB_STATEMENT_TIMEOUT_MS`). The app never opens connections at import time.
- Sessions: `async_sessionmaker(expire_on_commit=False)`. `get_session` yields one session per request and rolls it
  back if the request fails; nothing is committed implicitly: the service that owns the unit of work commits.
- Models subclass `app.core.db.Base`, whose naming convention names every constraint:
  `ix_%(column_0_label)s`, `uq_%(table_name)s_%(column_0_N_name)s`, `ck_%(table_name)s_%(constraint_name)s`,
  `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `pk_%(table_name)s`.
- Relationships use `app.core.db.relationship`, which defaults to `lazy="raise"`; `sqlalchemy.orm.relationship` and
  `backref` are banned by ruff (`TID251`).
- Mixins: `UUIDPrimaryKeyMixin` (UUIDv7 `id`, generated in Python), `TimestampMixin` (`created_at` and
  `updated_at`, `timestamptz NOT NULL`, default `now()` in the database; `updated_at` is refreshed by SQLAlchemy on
  ORM updates, not by a trigger, so a raw SQL `UPDATE` must set it itself).
- Alembic runs async, reads `DATABASE_URL` through `Settings`, and names revision files
  `YYYY_MM_DD_HHMM_<slug>.py` in UTC. Migrations are a separate one-off step (`make migrate`, the compose `migrate`
  service), never run at application start. Every revision must upgrade and downgrade, and the models must match
  the migrated schema (`tests/migrations/`).
- Tests never substitute the database: they run against PostgreSQL and Redis containers, with the schema from
  `alembic upgrade head`.

## 6. Resource budget

| Item | Budget | Where it is set or checked |
|---|---|---|
| API process | 1 uvicorn worker per container | Dockerfile `CMD` (no `--workers`) |
| API memory | limit 256 MiB, idle RSS under 120 MiB | `compose.yaml` limit; RSS: target, not checked automatically |
| PostgreSQL | limit 512 MiB | `compose.yaml` |
| Redis | limit 128 MiB, `maxmemory 64mb`, `noeviction` | `compose.yaml` |
| Image | under 250 MB uncompressed | target, not checked automatically |
| Startup | under 5 s to a healthy `/health` | target, not checked automatically |
| `/ready` | at most 2 database round-trips and 1 Redis command | `tests/integration/test_readiness.py` |
| Redis pool | bounded (`REDIS_MAX_CONNECTIONS`) and blocking | `app/core/redis.py` |
| CI | under 10 minutes | job timeouts in `.github/workflows/ci.yml` (10–15 min per job) |

## 7. Quality rules

Enforced by `make lint` (the pre-commit hook) and CI:

- Files at most 300 lines (tests 500): `scripts/check_file_length.py`.
- Functions at most 50 statements, complexity at most 10, at most 5 arguments; lines at most 100 characters: ruff.
- No dead code (vulture; framework-only names go in `vulture_whitelist.py` under its stated rule), no commented-out
  code, no `print` or debugger calls: ruff.
- `TODO(#<issue>): text` only: ruff `TD` rules.
- mypy `--strict` over `app`, `tests`, `scripts` and `migrations`.
- `# noqa` only with a code and a reason (`# noqa: CODE  # why`): `make noqa-check`.
- Commits follow Conventional Commits with types `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `ci`, `chore`
  and a first line of at most 72 characters (commit-msg hook, and CI for pull requests).
