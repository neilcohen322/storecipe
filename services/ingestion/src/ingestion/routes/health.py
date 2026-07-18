from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from ingestion.config import get_settings
from ingestion.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().service_name}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, Any]:
    dependencies, failed = await health_service.check_dependencies(
        request.app.state.engine, request.app.state.redis
    )
    if failed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependency unavailable: " + ", ".join(failed),
        )
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "dependencies": dependencies,
    }
