# ADR-0006: Split the project into three repositories: backend, frontend, infra

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

The three parts have different lifecycles: the backend is built from M0, the
frontend starts at M7, and infra (Compose, Caddy, monitoring, deploy, k6,
runbook) changes with environments, not features. The
self-hosted deploy runner must be attached to as little code as possible
(ADR-0004).

## Considered options

1. **Three repositories:** `dtm-tutor-backend`, `dtm-tutor-frontend`,
   `dtm-tutor-infra`.
2. **One monorepo.**
3. **Two repositories** (application code together, infra separate).

## Decision

Each repository builds, tests and versions on its own: backend and frontend
publish Docker images (the backend also an `openapi.json` per release); infra
only deploys. Four rules cover coordination:

1. A cross-repo feature takes two PRs, backend first, without breaking the old
   API.
2. The frontend pins a released `openapi.json` and generates its types from it.
3. `envs/<env>/versions.env` in infra pins which image tags run together. A
   deploy changes this file; a rollback reverts that change.
4. Shared issue and PR templates live in the `.github` repository.

**Why not a monorepo:** one PR could change API and UI together, but CI would
need path filters, the frontend would sit empty until M7, and the deploy runner
would be attached to the repository holding all application code and all PRs.
A monorepo also hides breaking API changes; separate repos force a versioned
contract.

**Why not two repositories:** keeps the runner isolated but still couples
backend and frontend CI and release cadence.

## Consequences

- **Positive:** independent CI and releases; a versioned API contract; "what runs where" is one file per environment with history.
- **Negative:** cross-repo features need two ordered PRs; API backward
  compatibility is required work; three sets of rulesets and CI; no atomic
  cross-repo change.
- **Revisit when:** most features need coordinated changes in both app repos
  and the two-PR order slows work; or keeping `versions.env` and the
  `openapi.json` pin in sync often breaks deploys.
