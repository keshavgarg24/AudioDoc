"""Level 1: the free screen endpoint.

SYNCHRONOUS, AND THAT IS THE POINT
----------------------------------
Every other detection route in this service is asynchronous, because a 40 s to
3 min request dies behind most load balancers. Level 1 runs in ~1.5 s, which is
inside every default proxy timeout, so it answers in the response rather than
handing back a job id. A frontend that wants a verdict gets one in one round
trip with no polling, which is most of the reason the tier exists.

BOUNDED ON PURPOSE
------------------
FastAPI runs a sync handler on anyio's worker pool, which defaults to 40
threads. Level 1 is CPU-bound, so 40 concurrent screens on a 4-core box do not
run 40x faster - they run at the same aggregate throughput with 40x the
latency, and the p99 on a request that promises ~1.5 s becomes a minute. The
semaphore keeps the queue in front of the work, where a caller can be told to
retry, rather than inside it where they can only wait.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ... import verdicts
from ...core.config import get_settings
from ...core.security import SCREEN_SCOPE
from ..deps import require_scope
from ..errors import ErrorResponse, api_error
from ..serialize import jsonable
from ..validate import safe_filename

log = logging.getLogger(__name__)
router = APIRouter(tags=["screen"])
settings = get_settings()


def _permits() -> int:
    """Concurrent screens allowed in this process.

    Defaults to the CPU count, capped at 8. Level 1 is a short GEMM-bound
    burst, so one in flight per core saturates the box; past that, ONNX
    Runtime's own intra-op threads and ours start contending for the same
    vector units.
    """
    from ...core.config import _env_int

    configured = _env_int("LABS_SCREEN_CONCURRENCY", 0)
    if configured > 0:
        return configured
    return max(1, min(os.cpu_count() or 2, 8))


_SLOTS = threading.BoundedSemaphore(_permits())


class ScreenResponse(BaseModel):
    model_config = {"extra": "allow"}
    tier: str
    verdict: str
    filename: str


@router.post("/screen", response_model=ScreenResponse,
             responses={400: {"model": ErrorResponse},
                        413: {"model": ErrorResponse},
                        415: {"model": ErrorResponse},
                        429: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}},
             summary="Screen a track for AI origin (Level 1, fast)")
def create_screen(
    request: Request,
    file: UploadFile = File(..., description="Audio file to screen"),
    reference: Optional[str] = Form(None, description="Your own id, echoed back"),
    who=Depends(require_scope(SCREEN_SCOPE)),
):
    """Return a Level-1 verdict in one round trip.

    Two small models over two representations of the audio, fused so that
    agreement raises confidence and disagreement lowers it, plus content
    credentials and container forensics.

    `next_step` is the field to act on:

      return    the verdict is decisive and the deep model would not change it
      escalate  Level 1 could not settle this track; POST /v1/analyses

    A `human-made` result from this endpoint is NOT an exoneration. Both
    Level-1 models recognise only the generators they were trained on, so
    their silence is not evidence. Use mode=ai or mode=full for that claim.
    """
    from ...core.uploads import stage_prefix
    from ...core.uploads import stage_upload as _stage

    if not settings.screen.enabled:
        raise api_error(503, "screen_disabled",
                        "The Level-1 screen is not enabled on this deployment.")

    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in settings.audio.allowed_suffixes:
        raise api_error(
            415, "unsupported_media_type",
            f"Unsupported file type '{suffix or 'unknown'}'. Allowed: "
            f"{', '.join(settings.audio.allowed_suffixes)}.")

    # Admission control before the upload is staged, so a saturated instance
    # sheds load without first spending disk and bandwidth on a file it is
    # about to refuse.
    if not _SLOTS.acquire(blocking=False):
        raise api_error(429, "screen_busy",
                        "This instance is at its screening concurrency limit. "
                        "Retry shortly.")

    path = None
    try:
        display_name = safe_filename(file.filename)
        path = _stage(file, prefix=stage_prefix(request_id), settings=settings)

        from ... import tiers

        started = time.time()
        report = tiers.analyse(path, mode="screen", settings=settings,
                               display_name=display_name)
        report["reference"] = reference
        report["request_id"] = request_id
        _attach_appeal(report, path, display_name, who, request_id)
        log.info("Screened %s in %.2fs -> %s", display_name,
                 time.time() - started, report.get("verdict"))
        return JSONResponse(status_code=200, content=jsonable(report))
    except Exception as exc:
        from ...analysis.audio import AudioError

        if isinstance(exc, AudioError):
            raise api_error(400, "invalid_audio", str(exc)) from exc
        if hasattr(exc, "status_code"):      # already an api_error
            raise
        log.exception("Screen failed for request %s", request_id)
        # The message is deliberately generic: this endpoint is ungated, so its
        # error text reaches anyone, and a decoder traceback is a description of
        # the parsing stack in front of it.
        raise api_error(503, "screen_failed",
                        "The screen could not be completed. Retry shortly.") from None
    finally:
        _SLOTS.release()
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                log.warning("Could not remove staged upload %s", path)


def _attach_appeal(report: dict, path: str, display_name: str, who,
                   request_id: str) -> None:
    """Retain a decisive result so the caller can appeal it, and say so.

    ONLY when Level 1 ended the request on its own. That is the case where the
    caller has a verdict and no deep analysis to check it against, and it is a
    small fraction of traffic. Retaining every screen on an ungated endpoint
    would be an unbounded storage bill and a standing pile of other people's
    audio, for no benefit - every other path already escalates.

    Entirely best effort. Retention failing must never fail a screen: the
    caller asked for a verdict, and they have one.
    """
    cfg = settings.screen
    if (not cfg.retain_for_appeal
            or report.get("next_step") != verdicts.NEXT_RETURN):
        return

    try:
        from ...services.storage import get_audio_store, get_mongo

        mongo = get_mongo()
        if not mongo.enabled:
            return

        screen_id = f"scr_{uuid.uuid4().hex}"
        # Without the audio there is nothing to re-analyse, so an appeal would
        # have to re-upload. That is still a supported path, and the id is
        # still worth issuing so the appeal can be linked to this verdict.
        audio = None
        try:
            audio = get_audio_store().put(path, screen_id, display_name)
        except Exception:
            log.debug("Could not retain appeal audio for %s", screen_id,
                      exc_info=True)

        stored = mongo.save_screen(
            screen_id, result=report, audio=audio,
            meta={"filename": display_name, "owner": who.id,
                  "api_key": who.fingerprint, "request_id": request_id},
            ttl_seconds=cfg.retain_seconds)
        if not stored:
            return

        report["screen_id"] = screen_id
        report["appeal"] = {
            "available": True,
            "endpoint": "/v1/escalations",
            "screen_id": screen_id,
            # null, not 0, when retention is unlimited. A client reading 0
            # would reasonably conclude the window had already closed and hide
            # the appeal button - which is the opposite of what 0 configures.
            "expires_in_seconds": cfg.retain_seconds or None,
            "requires_reupload": audio is None,
            "note": "Level 1 decided this on its own and did not run the deep "
                    "model. If you believe that is wrong, POST this screen_id "
                    "to /v1/escalations and the deep model will re-examine the "
                    "track. Its verdict is independent and may overturn this "
                    "one.",
        }
    except Exception:
        log.warning("Could not prepare the appeal path", exc_info=True)
