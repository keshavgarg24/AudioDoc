"""Tool endpoints.

Every tool is submitted the same way and polled the same way, so a client
integrates once and gains each new tool for free:

    GET  /v1/tools                  what exists, what it costs, what it claims
    POST /v1/tools/{slug}           202 with a job id
    GET  /v1/tools/results/{id}     status, then the result

`wait` is offered for convenience: a caller that would rather block than poll
can ask the server to hold the response for up to 25 seconds and return the
result inline if the tool finishes in time. It falls back to the job id when it
does not, so a slow tool can never turn into a timed-out request.

Uniform async is deliberate. A hosted proxy in front of this service will close
an idle connection long before a two-file tool finishes, and having one tool
behave differently from the rest is how clients end up with two code paths and
one of them untested.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ... import tools as registry
from ...core.config import get_settings
from ...core.security import get_limiter
from ...services import tools as runner
from ...services.jobs import get_runner, get_store
from ..deps import bucket_for, require_scope
from ..errors import ErrorResponse, api_error
from ..serialize import jsonable
from ..validate import clean_text, safe_filename

log = logging.getLogger(__name__)
router = APIRouter(tags=["tools"])
settings = get_settings()

MAX_WAIT_SECONDS = 25.0
POLL_STEP = 0.35


class ToolAccepted(BaseModel):
    id: str
    status: str
    tool: str
    poll_url: str
    created_at: Optional[str] = None


# ---------------------------------------------------------------- catalogue --
@router.get("/tools", summary="List available tools")
def list_tools():
    """Every tool, with its inputs, expected runtime and stated accuracy.

    The accuracy and limitations strings are part of the contract: they are
    what the product claims, and they are returned here so an integrator can
    surface them rather than inventing their own.
    """
    return JSONResponse(status_code=200, content={
        "tools": registry.catalogue(),
        "count": len(registry.slugs()),
        "api_version": settings.server.api_version,
    })


@router.get("/tools/{slug}", summary="Describe one tool",
            responses={404: {"model": ErrorResponse}})
def describe_tool(slug: str):
    spec = registry.spec(slug)
    if spec is None:
        raise api_error(404, "unknown_tool",
                        f"No tool '{slug}'. See GET /v1/tools.")
    return JSONResponse(status_code=200, content=next(
        t for t in registry.catalogue() if t["slug"] == slug))


# ------------------------------------------------------------------ submit --
@router.post("/tools/{slug}", status_code=202, response_model=ToolAccepted,
             responses={400: {"model": ErrorResponse},
                        404: {"model": ErrorResponse},
                        413: {"model": ErrorResponse},
                        415: {"model": ErrorResponse},
                        422: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}},
             summary="Run a tool")
async def run_tool(
    slug: str,
    request: Request,
    file: Optional[UploadFile] = File(None, description="Primary audio file"),
    reference: Optional[UploadFile] = File(None, description="Reference track (Reference Match)"),
    beat: Optional[UploadFile] = File(None, description="Instrumental (Beat and Vocal Fit)"),
    vocal: Optional[UploadFile] = File(None, description="Vocal (Beat and Vocal Fit)"),
    genre: Optional[str] = Form(None, description="Target genre, where the tool uses one"),
    wait: float = Form(0, description="Seconds to block before falling back to a job id (max 25)"),
    no_cache: bool = Form(False, description="Skip the content-hash cache"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    who=Depends(require_scope("analyze")),
):
    """Submit audio to a tool.

    Repeat submissions of byte-identical audio with identical options return a
    cached result immediately, flagged with `cached: true`. Pass `no_cache` to
    force a fresh run.
    """
    spec = registry.spec(slug)
    if spec is None:
        raise api_error(404, "unknown_tool",
                        f"No tool '{slug}'. See GET /v1/tools.")

    supplied = {"file": file, "reference": reference, "beat": beat,
                "vocal": vocal}
    missing = [name for name in spec.inputs if supplied.get(name) is None]
    if missing:
        raise api_error(
            422, "missing_input",
            f"{spec.name} needs {len(spec.inputs)} file(s): "
            f"{', '.join(spec.inputs)}. Missing: {', '.join(missing)}.")

    if genre:
        from ...artist import genres as g
        clean_text(genre, "genre", max_length=64)
        if not g.resolve(genre):
            raise api_error(422, "invalid_genre",
                            f"Unknown genre '{genre}'. See GET /v1/genres.")

    # Reuse the middleware's id so a caller quoting X-Request-Id can be traced
    # into the worker's log lines rather than into a second, unrelated id.
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    paths: Dict[str, str] = {}
    try:
        for name in spec.inputs:
            paths[name] = _stage(supplied[name], f"{request_id}-{name}")
            await supplied[name].close()
    except Exception:
        _cleanup(paths)
        raise

    store, jobs = get_store(), get_runner()

    # Refusals happen before the job record exists, so a rejected submission
    # cannot leave a `queued` job behind that eviction will never reclaim.
    limiter, bucket = get_limiter(), bucket_for(who)
    if not limiter.acquire(bucket, settings.server.max_inflight_per_key):
        _cleanup(paths)
        raise api_error(429, "too_many_inflight",
                        f"This key already has "
                        f"{settings.server.max_inflight_per_key} jobs in "
                        f"flight. Wait for one to finish.")

    job = None
    try:
        job = store.create(
            meta={"tool": slug, "kind": "tool",
                  "request_id": request_id,
                  "api_key": who.fingerprint,
                  "filenames": {n: safe_filename(supplied[n].filename, n)
                                for n in spec.inputs}},
            owner=who.id)

        if idempotency_key:
            from ...services.storage import get_mongo
            existing = get_mongo().claim_idempotency(idempotency_key, job.id)
            if existing:
                store.discard(job.id)
                limiter.release(bucket)
                _cleanup(paths)
                return ToolAccepted(id=existing, status="duplicate", tool=slug,
                                    poll_url=f"/v1/tools/results/{existing}")

        params = {"genre": genre} if genre else {}

        # Mirror the job into MongoDB so a poll can be answered by a container
        # that did not run it.
        #
        # A tool runs IN THIS PROCESS, so `get_store()` only ever knows about
        # jobs this replica accepted. Behind a load balancer that makes
        # /v1/tools/results/{id} a coin flip: the replica that ran the tool
        # answers 200, every other replica answers 404, and a client that
        # treats its first 404 as "no such job" - which is what 404 means -
        # gives up on a run that is progressing normally. The tool_results
        # collection does not help here; it is keyed by a content hash for
        # caching, not by job id.
        _mirror_job(job.id, who.id, slug, request_id)

        def work(progress):
            _mirror_update(job.id, status="running", progress="running")
            try:
                result = runner.run(slug, paths, params, progress=progress,
                                    use_cache=not no_cache)
            except Exception as exc:
                _mirror_update(job.id, status="failed", progress="failed",
                               error=str(exc)[:500])
                raise
            _mirror_update(job.id, status="succeeded", progress="complete",
                           result=result)
            return result

        def cleanup():
            limiter.release(bucket)
            _cleanup(paths)

        jobs.submit(job, work, cleanup=cleanup)
    except Exception:
        if job is not None:
            store.discard(job.id)
        limiter.release(bucket)
        _cleanup(paths)
        log.warning("Could not queue tool run", exc_info=True)
        # `from None`: already logged with its traceback just above.
        raise api_error(503, "service_unavailable",
                        "The service is not accepting new work right now. "
                        "Retry shortly.") from None

    # Convenience path: hold briefly so a simple client can stay synchronous.
    blocked = await _block(job, min(max(wait, 0.0), MAX_WAIT_SECONDS))
    if blocked is not None:
        return JSONResponse(status_code=200, content=jsonable(blocked))

    return ToolAccepted(id=job.id, status=job.status, tool=slug,
                        poll_url=f"/v1/tools/results/{job.id}",
                        created_at=job.public().get("created_at"))


# ------------------------------------------------------------------ result --
@router.get("/tools/results/{job_id}", summary="Fetch a tool result",
            responses={404: {"model": ErrorResponse}})
def get_result(job_id: str, who=Depends(require_scope("read"))):
    job = get_store().get(job_id)
    if job and job.owned_by(who.id):
        # Coerced, not returned raw: a tool result assembled from stored
        # data can carry datetimes, and JSONResponse raises on those.
        return JSONResponse(status_code=200, content=jsonable(job.public()))

    # Not in this process. With more than one replica that is the ordinary
    # case rather than an error, so the shared record is consulted before
    # giving the caller a 404 they would reasonably act on.
    if job is None:
        mirrored = _mirrored_body(job_id, who)
        if mirrored is not None:
            return JSONResponse(status_code=200, content=jsonable(mirrored))

    # Same 404 either way: distinguishing "does not exist" from "not
    # yours" tells an attacker which ids are real.
    raise api_error(404, "not_found",
                    "No tool run with that id. Results are held for a "
                    "limited window after completion.")


# ----------------------------------------------------------------- helpers --
# Every mirror call is best effort. The local store remains the source of
# truth for the replica that is running the tool, so a MongoDB outage costs
# cross-replica visibility - the thing that was broken before this existed -
# and never the run itself.
def _mirror_job(job_id: str, api_key_id: Optional[str], slug: str,
                request_id: str) -> None:
    try:
        from ...services.storage import get_mongo
        get_mongo().create_job(job_id,
                               {"kind": "tool", "tool": slug,
                                "request_id": request_id},
                               api_key_id=api_key_id)
    except Exception:
        log.debug("Could not mirror tool job %s", job_id, exc_info=True)


def _mirror_update(job_id: str, **fields) -> None:
    try:
        from ...services.storage import get_mongo
        get_mongo().update_job(job_id, **fields)
    except Exception:
        log.debug("Could not update mirrored tool job %s", job_id, exc_info=True)


def _mirrored_body(job_id: str, who) -> Optional[Dict]:
    """Shared state for a tool run this container never executed."""
    try:
        from ...services.storage import get_mongo
        doc = get_mongo().get_job(job_id)
    except Exception:
        return None
    if not doc or doc.get("kind") != "tool":
        return None
    # Ownership is checked here too, not only on the local path: a fallback
    # that skips it would hand any caller any tool result by id.
    if who.id and doc.get("api_key_id") and doc["api_key_id"] != who.id:
        return None
    return {"id": job_id, "status": doc.get("status", "running"),
            "progress": doc.get("progress", doc.get("status", "running")),
            "tool": doc.get("tool"), "created_at": doc.get("created_at"),
            "started_at": doc.get("started_at"),
            "finished_at": doc.get("finished_at"),
            "error": doc.get("error"), "source": "shared",
            "result": doc.get("result")}


def _stage(upload: UploadFile, tag: str) -> str:
    """Stream an upload to disk, enforcing type and size as it goes."""
    from ...core.uploads import stage_prefix, stage_upload
    return stage_upload(upload, prefix=stage_prefix("tool", tag), settings=settings)


def _cleanup(paths: Dict[str, str]) -> None:
    for path in paths.values():
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                log.warning("Could not remove staged upload %s", path)


async def _block(job, seconds: float) -> Optional[Dict]:
    """Wait briefly for a job, returning its body if it finishes in time.

    Must be async and must await: a time.sleep here would block the event loop
    for the whole wait, stalling every other request on the process rather than
    just this one.
    """
    if seconds <= 0:
        return None
    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(POLL_STEP)
        body = job.public()
        if body.get("status") in ("succeeded", "failed"):
            return body
    return None
