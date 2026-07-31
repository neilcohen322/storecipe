import asyncio
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
    redis = request.app.state.redis
    postgres_ok, redis_ok = await asyncio.gather(
        health_service.check_postgres(engine),
        health_service.check_redis(
            redis,
            timeout_seconds=request.app.state.redis_timeout_seconds,
        ),
    )
    if not postgres_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependency unavailable: postgres",
        )
    return {
        "status": "ok",
        "service": get_settings().service_name,
        "dependencies": {
            "postgres": "ok",
            "redis_cache": "ok" if redis_ok else "degraded",
        },
    }
