"""Operational endpoints: liveness and readiness (unversioned, outside /api/v1)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.errors import problem_openapi_response
from app.modules.health.schemas import (
    HealthResponse,
    NotReadyProblem,
    ReadinessChecks,
    ReadinessResponse,
)
from app.modules.health.service import NOT_READY_DETAIL, ReadinessService

router = APIRouter(tags=["health"])

_NOT_READY_EXAMPLE = {
    "type": "/problems/not-ready",
    "title": "Service not ready",
    "status": 503,
    "detail": NOT_READY_DETAIL,
    "code": "not_ready",
    "request_id": "0190f3c2-7b1e-7c4a-9d2f-3a1b2c3d4e5f",
    "checks": {"database": "ok", "redis": "unavailable"},
}


def get_readiness_service(request: Request) -> ReadinessService:
    """Return the readiness service built in the application lifespan."""
    service: ReadinessService = request.app.state.readiness_service
    return service


@router.get(
    "/health",
    summary="Liveness probe",
    description="Reports that the process is running. Never touches PostgreSQL or Redis.",
    response_model=HealthResponse,
    responses={200: {"content": {"application/json": {"example": {"status": "ok"}}}}},
)
async def get_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Checks PostgreSQL (`SELECT 1`) and Redis (`PING`) concurrently, each bounded by "
        "READINESS_TIMEOUT_SECONDS. Returns 503 if either is unavailable."
    ),
    response_model=ReadinessResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"status": "ok", "checks": {"database": "ok", "redis": "ok"}},
                },
            },
        },
        503: problem_openapi_response(
            NotReadyProblem, "A dependency is unavailable.", _NOT_READY_EXAMPLE
        ),
    },
)
async def get_ready(
    service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> ReadinessResponse:
    report = await service.ensure_ready()  # NotReadyError renders as the 503 problem
    return ReadinessResponse(
        status="ok", checks=ReadinessChecks(database=report.database, redis=report.redis)
    )
