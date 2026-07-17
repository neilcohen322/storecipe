"""RFC 9457 problem-details responses for all error paths (contracts/errors.md)."""

import uuid
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://docs.storecipe.example/problems"


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    return uuid.uuid4().hex


def problem_response(
    request: Request,
    status_code: int,
    detail: str | None = None,
    problem_type: str | None = None,
    extra: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    try:
        title = HTTPStatus(status_code).phrase
    except ValueError:
        title = "Error"
    body: dict[str, object] = {
        "type": problem_type or f"{PROBLEM_TYPE_BASE}/{status_code}",
        "title": title,
        "status": status_code,
        "instance": request.url.path,
        "request_id": _request_id(request),
    }
    if detail is not None:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def install_problem_details(app: FastAPI) -> None:
    """Attach the request-id middleware and problem+json exception handlers."""

    @app.middleware("http")
    async def assign_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(request, exc.status_code, detail=detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "message": str(error.get("msg", "")),
            }
            for error in exc.errors()
        ]
        return problem_response(
            request,
            422,
            detail="One or more fields are invalid.",
            problem_type=f"{PROBLEM_TYPE_BASE}/validation-error",
            extra={"errors": errors},
        )
