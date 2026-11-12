# ADR-0001: Build the backend as a modular monolith with a separate worker process

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

One developer builds and runs DTM AI Tutor on one home server. The domain
splits into modules (`auth`, `content`, `practice`, `mastery`, `planner`,
`notifications`, `stats`, `tutor`, `exam`, `analytics`) arriving milestone by
milestone. Real load is unknown until the M9 load test. LLM
generation is slow and must never block an API response.

## Considered options

1. **Modular monolith + separate worker process.**
2. **Microservices** (a service per module, Kafka, Kubernetes).
3. **Unstructured monolith** (no enforced boundaries).

## Decision

We build one FastAPI application. Each module has three layers,
`router -> service -> repository`, and calls another module only through its
`service` interface, never its tables or repository. `import-linter` enforces
this in CI. Background work, including every LLM call, runs in a separate
worker process sharing PostgreSQL and Redis. A module is split out only if the
M9 load test shows the need.

**Why not microservices:** with one developer the cost is mostly operations:
separate deploys, network failures, distributed tracing, cross-database
consistency, and Kafka and Kubernetes on one home machine. None of it solves a
measured problem, and service boundaries drawn before the domain is understood
are expensive to move.

**Why not an unstructured monolith:** cheapest to start, but modules start
reading each other's tables and a later split becomes a rewrite. Enforced
boundaries keep that exit open for little cost.

## Consequences

- **Positive:** one deploy, local transactions, simple setup; boundaries checked by a tool; slow LLM work kept off API latency.
- **Negative:** API and worker share database and Redis failures; one bad
  migration affects every module; `import-linter` contracts need upkeep.
- **Revisit when:** the M9 load test shows a module needs independent scaling
  or deployment; a module needs a different runtime or release cadence; or more
  developers join and block each other.
