# ADR-0002: Use FastAPI with async SQLAlchemy 2 and Alembic, not Django

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

The backend is mostly I/O waiting: LLM calls, SSE explanations (M6), a live
exam over WebSocket (M8). The frontend will generate its types from
`openapi.json` (ADR-0006), so the contract must be explicit. Code runs under
`mypy --strict`, and one developer maintains what the framework lacks.

## Considered options

1. **FastAPI + SQLAlchemy 2 (async) + Alembic.**
2. **Django + Django REST Framework.**
3. **Django + Django Ninja.**

## Decision

We use FastAPI, async SQLAlchemy 2 and Alembic. Request and response bodies are
Pydantic models; the OpenAPI schema is generated from them and checked in CI.

**What Django would have given us, honestly:** a free admin panel (we build
one in M3), mature auth and built-in migrations: strong for a CRUD-heavy app.

**Why not Django + DRF:** DRF is built around synchronous views. Our main
workloads need async end to end, and Django's docs state that async-mode
transactions are not yet supported and must go through `sync_to_async()`;
mixing styles adds overhead and `SynchronousOnlyOperation` bugs. DRF
serializers are also a weaker typed contract than Pydantic under
`mypy --strict`.

**Why not Django + Ninja:** it fixes the schema side but keeps Django's ORM
with the same async limits, plus a large framework we only partly use.

## Consequences

- **Positive:** async LLM, SSE and WebSocket without thread adapters; one typed
  source for validation, docs and frontend types; explicit queries make N+1
  control (`lazy="raise"`, query-count fixture) practical.
- **Negative:** we build the admin (M3) and auth (M2) ourselves; async
  SQLAlchemy needs discipline around lazy loading and session lifetime.
- **Revisit when:** our own admin and auth work outgrows the async workload;
  the async paths move out of this app; or Django's async ORM gains
  transactions and Django-experienced developers join.

## References

- Django, "Asynchronous support":
  <https://docs.djangoproject.com/en/stable/topics/async/> (checked 2026-11-12).
