# ADR-0003: Use a three-branch flow (dev, staging, main) with protected branches

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

Staging deploys automatically on merge; production deploys on merge and tag
with a manual approval (plan, "Muhitlar"; M0-11). Milestones are demoed on
staging first. GitHub does not let a solo author approve their own pull
request, so CI and branch rules are the safety net.

## Considered options

1. **Three long-lived branches: `dev`, `staging`, `main`.**
2. **Trunk-based development** (one `main`, feature flags, promotion by tag).
3. **GitHub flow** (one `main`, always deployable).

## Decision

- Task branches (`feat/`, `fix/`, `docs/`, `chore/`) start from `dev` and are
  **squash**-merged into `dev`: one task, one commit.
- `dev -> staging` and `staging -> main` use **merge commits**; squashing here
  makes histories diverge and causes false conflicts.
- `hotfix/` branches start from `main`, are squash-merged into it, then `main`
  is merged back into `dev`.
- Rulesets on all three branches: PR required, no force push, no deletion,
  threads resolved, zero approvals. Required checks: `lint`, `typecheck`,
  `test`, `migrations`, `build`.
- The infra repository has only `main`, with PR and CI required.

**Why not trunk-based:** honestly the lowest ceremony, but it moves the
staging/production split into tags and feature flags we would have to build.
With `staging` as a branch, `git log` answers "what is on staging" and deploys
follow from a merge.

**Why not GitHub flow:** with one `main` there is nowhere for a release
candidate to sit on staging for a demo while work continues; every merge is a
production candidate.

## Consequences

- **Positive:** one branch per environment; promotion is a visible PR diff.
- **Negative:** two promotion PRs per milestone; the hotfix back-merge to `dev`
  is easy to forget.
- **Revisit when:** promotion PRs keep showing conflicts or drift; staging
  stops being used for demos; or deploys move to continuous delivery.
