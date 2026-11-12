# Architecture Decision Records

One decision per file. Accepted ADRs are not edited: if a decision changes, a
new ADR is written and the old one is marked `Superseded by NNNN`.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith.md) | Modular monolith with a separate worker process | Accepted |
| [0002](0002-fastapi-sqlalchemy.md) | FastAPI + async SQLAlchemy 2 + Alembic, not Django | Accepted |
| [0003](0003-three-branch-flow.md) | Three-branch flow: `dev`, `staging`, `main` | Accepted |
| [0004](0004-home-server-cloudflare-tunnel.md) | Home server for staging and production, behind Cloudflare Tunnel | Accepted |
| 0005 | *Reserved* for the M1 LLM provider benchmark | — |
| [0006](0006-three-repositories.md) | Three repositories: backend, frontend, infra | Accepted |

Note: the plan's M2-10 token-strategy ADR will take the next free number (0007); owner to confirm.
