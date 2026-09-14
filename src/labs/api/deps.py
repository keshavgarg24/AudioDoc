"""Shared FastAPI dependencies: who is calling, and may they.

`principal` resolves the API key and applies the per-key rate limit.
`require_scope("analyze")` additionally checks the key holds that scope.
Both are safe to use on any route.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from ..core.config import get_settings
from ..core.security import Principal, authenticate, get_limiter
from .errors import api_error

_AUTH_ERRORS = {
    "missing_api_key": (401, "An X-API-Key header is required."),
    "invalid_api_key": (401, "That API key is not recognised."),
    "revoked_api_key": (401, "That API key has been revoked."),
    "quota_exceeded": (429, "Daily quota exceeded for this key."),
    "auth_unavailable": (503, "Authentication is temporarily unavailable."),
}


def principal(request: Request,
              x_api_key: Optional[str] = Header(default=None)) -> Principal:
    """Authenticate, rate limit, and attach the caller to request.state."""
    settings = get_settings()
    who, err = authenticate(x_api_key, settings=settings)
    if err:
        status, message = _AUTH_ERRORS.get(err, (401, "Unauthorized."))
        if err == "quota_exceeded":
            # A quota resets at midnight UTC. Telling the caller when to come
            # back is the difference between a client that backs off and one
            # that retries in a tight loop for the rest of the day.
            raise HTTPException(
                status_code=status,
                detail={"error": {"code": err, "message": message}},
                headers={"Retry-After": str(_seconds_to_utc_midnight())})
        raise api_error(status, err, message)

    allowed, retry_after = get_limiter().check(bucket_for(who))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {
                "code": "rate_limited",
                "message": f"Too many requests. Retry in {retry_after}s."}},
            headers={"Retry-After": str(retry_after)})

    request.state.principal = who
    return who


def require_scope(scope: str):
    """Dependency factory: the caller must hold `scope`."""
    def _check(who: Principal = Depends(principal)) -> Principal:
        if not who.can(scope):
            raise api_error(403, "forbidden",
                            f"This key does not have the '{scope}' scope.")
        return who
    return _check


def bucket_for(who: Principal) -> str:
    """Stable per-caller key for rate limiting and concurrency accounting."""
    return who.id or who.fingerprint or "anonymous"


def _seconds_to_utc_midnight() -> int:
    """Seconds until the daily quota window rolls over."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(tz=timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))
