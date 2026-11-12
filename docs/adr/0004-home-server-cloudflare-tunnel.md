# ADR-0004: Host staging and production on the home server behind Cloudflare Tunnel

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

The Compose stack includes Ollama, which needs real CPU/GPU and memory. There
is no hosting budget and no static IP. The repositories are public, so
untrusted pull requests must never run code on the owner's machine.

## Considered options

1. **Own machine for staging and production, exposed through Cloudflare Tunnel.**
2. **A rented VPS.**
3. **PaaS or managed cloud.**

## Decision

Staging and production run on the owner's machine as separate Compose projects
with separate networks, volumes and databases. The only way in is Cloudflare
Tunnel, which connects outbound only. A self-hosted GitHub runner is attached only to the infra repository
and runs only deploy workflows, triggered by `repository_dispatch` from backend
`staging`/`main` merges and by infra `main` merges, never by pull requests. All
PR CI runs on GitHub-hosted runners. The database is dumped daily with
`pg_dump` and the copy is moved off the machine.

**Why not a VPS:** one that can run Ollama costs money every month and is still
one machine run by one person; the single point of failure moves, it does not
go away.

**Why not PaaS / managed cloud:** least operations work, but the local LLM is
the hardest part to host, and managed services hide the deploy, backup and
rollback work this portfolio is meant to show.

## Consequences

- **Positive:** no hosting bill; Ollama on owned hardware; no inbound ports.
- **Negative:** **one machine is a single point of failure**: power, internet,
  disk or a reboot takes both environments down, with no SLA. Staging load
  competes with production. The runner is a path into the machine. Planned
  mitigations: UPS, an external uptime monitor, and telling beta users openly.
- **Revisit when:** real users arrive (M11); the M9 load test shows the machine
  cannot carry both environments; a restore drill fails; or home use of the
  machine causes outages.

## References

- Cloudflare Tunnel, outbound-only connections:
  <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>
  (checked 2026-11-12).
- GitHub Actions secure use, self-hosted runners on public repositories:
  <https://docs.github.com/en/actions/reference/security/secure-use>
  (checked 2026-11-12).
