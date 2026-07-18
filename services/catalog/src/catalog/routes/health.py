from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from catalog.config import get_settings
from catalog.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().service_name}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, Any]:
    engine = request.app.state.engine
    if not await health_service.check_postgres(engine):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependency unavailable: postgres",
        )
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "dependencies": {"postgres": "ok"},
    }
