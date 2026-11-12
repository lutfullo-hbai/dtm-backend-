"""Wire models for the health endpoints."""

from typing import Literal

from pydantic import BaseModel, Field

from app.core.errors import ProblemDetails

CheckStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    """Liveness result: the process is up and serving requests."""

    status: Literal["ok"] = Field(description="Always `ok` when the process responds.")


class ReadinessChecks(BaseModel):
    """Per-dependency readiness results."""

    database: CheckStatus = Field(description="PostgreSQL `SELECT 1` result.")
    redis: CheckStatus = Field(description="Redis `PING` result.")


class ReadinessResponse(BaseModel):
    """Readiness result when every dependency is reachable."""

    status: Literal["ok"] = Field(description="`ok` when all dependencies are reachable.")
    checks: ReadinessChecks


class NotReadyProblem(ProblemDetails):
    """Problem body returned when at least one dependency is unavailable."""

    checks: dict[str, CheckStatus] = Field(
        description="Per-dependency results, keyed `database` and `redis`."
    )
