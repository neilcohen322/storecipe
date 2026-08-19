"""HTTP admission for Catalog mutation rate limits."""

from __future__ import annotations

import math
import time

from fastapi import Request, Response

from catalog.rate_limits import BurstLimiter, RateLimitDecision
from catalog.services.errors import MutationRateLimited, MutationRateLimitUnavailable

MUTATION_OPERATION = "catalog_mutation"


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(decision.limit),
        "RateLimit-Remaining": str(decision.remaining),
        "RateLimit-Reset": str(decision.reset_at),
        "Retry-After": str(max(1, decision.reset_at - math.floor(time.time()))),
    }


async def enforce_mutation_quota(request: Request, response: Response, subject: str) -> None:
    limiter: BurstLimiter = request.app.state.mutation_burst_limiter
    decision = await limiter.hit(subject, MUTATION_OPERATION)
    headers = rate_limit_headers(decision)
    if decision.degraded:
        raise MutationRateLimitUnavailable(headers)
    if not decision.allowed:
        raise MutationRateLimited(headers)
    response.headers.update(
        {
            "RateLimit-Limit": headers["RateLimit-Limit"],
            "RateLimit-Remaining": headers["RateLimit-Remaining"],
            "RateLimit-Reset": headers["RateLimit-Reset"],
        }
    )
