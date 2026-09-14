"""One error envelope for the whole API.

Every failure, from any endpoint, serialises as:

    {"error": {"code": "<stable_string>", "message": "<human readable>"}}

Codes are stable identifiers so callers can branch on them without parsing
prose. Messages are for humans and may change.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


CODE_BY_STATUS = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden",
    404: "not_found", 405: "method_not_allowed", 409: "conflict",
    413: "payload_too_large", 415: "unsupported_media_type",
    422: "invalid_request", 429: "rate_limited", 500: "internal_error",
    503: "service_unavailable",
}


def api_error(status: int, code: str, message: str) -> HTTPException:
    """Raise-ready HTTPException already wearing the envelope."""
    return HTTPException(status_code=status,
                         detail={"error": {"code": code, "message": message}})


async def http_exception_handler(request, exc: HTTPException) -> JSONResponse:
    """Pass through handler-built envelopes; wrap FastAPI's plain strings."""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": {
            "code": CODE_BY_STATUS.get(exc.status_code, "error"),
            "message": detail if isinstance(detail, str) else str(detail)}}
    return JSONResponse(
        status_code=exc.status_code, content=body,
        headers={**(exc.headers or {}),
                 "X-Request-Id": getattr(request.state, "request_id", "")})


async def validation_handler(request, exc: RequestValidationError) -> JSONResponse:
    first = (exc.errors() or [{}])[0]
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return JSONResponse(
        status_code=422,
        content={"error": {
            "code": "invalid_request",
            "message": f"{field or 'request'}: {first.get('msg', 'invalid')}"}},
        headers={"X-Request-Id": getattr(request.state, "request_id", "")})


async def internal_error_handler(request, exc) -> JSONResponse:  # pragma: no cover
    return JSONResponse(status_code=500, content={"error": {
        "code": "internal_error", "message": "Internal server error."}})
