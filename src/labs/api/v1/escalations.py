"""Appeals: the human feedback loop.

WHY THIS EXISTS
---------------
Level 1 is allowed to end a request when it is confident a track is AI. That is
the tier's whole economic case, and it is also the one place where the service
makes an expensive claim on cheap evidence: two small models, 1.2 MB of
weights, deciding that somebody's track is machine-generated and then declining
to look further.

A person who believes that is wrong has to be able to say so, and saying so has
to actually DO something. Here, it runs the 1.29 GB model that Level 1 skipped.

WHAT IT IS WORTH BESIDES FAIRNESS
---------------------------------
An appeal produces the only labelled data this system ever generates about its
own mistakes. When Level 2 disagrees with Level 1 on a track a human disputed,
that is a confirmed false positive with the audio attached - which is exactly
the material you would want to fine-tune or re-threshold on, and it is
impossible to collect any other way.

So the outcome is recorded on both sides: `upheld` when the deep model agrees
with the screen, `overturned` when it does not. `GET /v1/feedback/summary`
reports the overturn rate, which is a measurement of the detector rather than
of the UI.

TWO WAYS IN
-----------
  screen_id only   the audio was retained at exit; nothing to re-upload
  screen_id + file the retention window expired, or retention is off

Both link the appeal to the original screen, so the feedback record is complete
either way.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...core.config import get_settings
from ...core.security import DEEP_SCOPE
from ...services.feedback import PENDING
from ..deps import bucket_for, require_scope
from ..errors import ErrorResponse, api_error
from ..serialize import jsonable
from ..validate import clean_text
from .analyses import get_detector

log = logging.getLogger(__name__)
router = APIRouter(tags=["escalations"])
settings = get_settings()


class EscalationAccepted(BaseModel):
    id: str
    status: str
    screen_id: str
    poll_url: str
    level_1_verdict: Optional[str] = None
    note: str


@router.post("/escalations", status_code=202, response_model=EscalationAccepted,
             responses={400: {"model": ErrorResponse},
                        404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse},
                        410: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}},
             summary="Appeal a Level-1 verdict to the deep model")
async def create_escalation(
    request: Request,
    screen_id: str = Form(..., description="The id from a /v1/screen response"),
    reason: Optional[str] = Form(None, description="Why you disagree. Recorded verbatim."),
    file: Optional[UploadFile] = File(
        None, description="Re-upload, only needed if the retention window expired"),
    webhook_url: Optional[str] = Form(None),
    who=Depends(require_scope(DEEP_SCOPE)),
):
    """Run Level 2 on a track Level 1 decided on its own.

    Requires the `deep` scope, because it runs the deep model and costs what
    the deep model costs. That is a deliberate product choice and not an
    obstacle to fairness: the platform's own upload loop holds the scope, so
    it can escalate on a user's behalf without charging them for disputing a
    verdict the platform produced.

    Returns `202` with a job id. Poll `GET /v1/analyses/{id}` as usual.
    """
    from ...services.storage import get_mongo

    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    screen_id = clean_text(screen_id, "screen_id", max_length=64)
    reason = clean_text(reason, "reason", max_length=2000)

    mongo = get_mongo()
    if not mongo.enabled:
        raise api_error(
            503, "appeals_unavailable",
            "Appeals need persistence, which is not configured on this "
            "deployment. Submit the file to POST /v1/analyses instead.")

    doc = mongo.get_screen(screen_id)
    if not doc:
        # Expired and never-existed are deliberately the same answer. The ids
        # are unguessable, but confirming which of the two a stranger hit is
        # still free information about other people's traffic.
        raise api_error(
            404, "screen_not_found",
            "No retained screen with that id. Retained results expire after "
            f"{settings.screen.retain_seconds // 60} minutes; re-submit the "
            "file to POST /v1/analyses.")

    if doc.get("owner") and who.id and doc["owner"] != who.id:
        raise api_error(404, "screen_not_found", "No retained screen with that id.")

    if doc.get("escalated") and doc.get("analysis_id"):
        # Idempotent rather than an error: a double-click on an "I disagree"
        # button must not start a second 60 s analysis of the same track.
        return JSONResponse(status_code=200, content=jsonable({
            "id": doc["analysis_id"],
            "status": "duplicate",
            "screen_id": screen_id,
            "poll_url": f"/v1/analyses/{doc['analysis_id']}",
            "level_1_verdict": (doc.get("result") or {}).get("verdict"),
            "note": "This screen was already escalated; returning the "
                    "existing analysis.",
        }))

    # Readiness, before accepting the work rather than after. /v1/analyses has
    # always done this; without it here an appeal returns 202 and then fails
    # asynchronously with "Detector is not loaded" - which is the worst
    # possible answer to someone disputing a verdict, because it looks like
    # their appeal was accepted and then quietly lost.
    #
    # Only checked when THIS process would run it. A distributed API container
    # never loads the backbone; its workers do.
    from ...services.queue import distributed

    if not distributed():
        detector = get_detector(settings)
        if detector is None:
            raise api_error(
                501, "deep_tier_unavailable",
                "This deployment serves the Level-1 screen only, so an appeal "
                "cannot be run here.")
        if not detector.is_ready:
            raise api_error(
                503, "model_loading",
                "The deep model is still loading, so the appeal cannot be run "
                "yet. Retry once GET /v1/health reports ready.")

    path = _materialise(doc, file, request_id)
    if path is None:
        raise api_error(
            410, "audio_unavailable",
            "The audio for this screen is no longer retained. Attach the file "
            "to this request, or submit it to POST /v1/analyses.")

    if file is not None:
        await file.close()

    level_1 = doc.get("result") or {}
    display_name = doc.get("filename") or "audio"

    try:
        job_id = _dispatch(path, display_name, level_1, screen_id, reason,
                           webhook_url, who, request_id)
    finally:
        # The dispatcher copies what it needs (to S3 when distributed, or into
        # the job's own staged path when local), so the appeal's temp file is
        # ours to clean up either way.
        _unlink(path)

    mongo.mark_screen_escalated(screen_id, job_id)
    mongo.save_feedback({
        "kind": "escalation",
        "screen_id": screen_id,
        "analysis_id": job_id,
        "api_key_id": who.id,
        "request_id": request_id,
        "reason": reason,
        "level_1": {
            "verdict": level_1.get("verdict"),
            "probability": level_1.get("fake_probability"),
            "confidence": level_1.get("confidence"),
        },
        # Resolved when the analysis finishes; see services/feedback.py.
        "outcome": PENDING,
    })

    return EscalationAccepted(
        id=job_id, status="queued", screen_id=screen_id,
        poll_url=f"/v1/analyses/{job_id}",
        level_1_verdict=level_1.get("verdict"),
        note="The deep model is running. Its verdict is independent of "
             "Level 1's and may overturn it.")


@router.get("/feedback/summary", summary="Appeal and overturn rates")
def feedback_summary(days: int = 30, who=Depends(require_scope("admin"))):
    """How often humans disagree, and how often they turn out to be right.

    Admin scope: an overturn rate is a measurement of the detector's error
    rate, which is commercially sensitive and not an ordinary caller's to read.

    `overturn_rate` is null rather than 0.0 until something has actually been
    decided. A rate computed from zero samples is not a rate, and rendering it
    as 0% reads as "the detector is never wrong".
    """
    from ...services.storage import get_mongo

    return JSONResponse(status_code=200,
                        content=jsonable(get_mongo().feedback_summary(days)))


# ---------------------------------------------------------------- helpers --
def _materialise(doc: dict, file: Optional[UploadFile],
                 request_id: str) -> Optional[str]:
    """Get the audio onto local disk: from the upload, or back from S3."""
    from ...core.uploads import stage_prefix
    from ...core.uploads import stage_upload as _stage

    if file is not None and file.filename:
        return _stage(file, prefix=stage_prefix(request_id), settings=settings)

    audio = doc.get("audio") or {}
    if not (audio.get("bucket") and audio.get("key")):
        return None

    import tempfile

    import boto3

    suffix = os.path.splitext(audio["key"])[1] or ".audio"
    fh = tempfile.NamedTemporaryFile(prefix=f"labs-appeal-{request_id[:8]}-",
                                     suffix=suffix, delete=False)
    fh.close()
    try:
        boto3.client("s3", region_name=settings.storage.s3_region) \
            .download_file(audio["bucket"], audio["key"], fh.name)
        return fh.name
    except Exception:
        log.warning("Could not fetch retained audio %s/%s",
                    audio.get("bucket"), audio.get("key"), exc_info=True)
        _unlink(fh.name)
        return None


def _dispatch(path: str, display_name: str, level_1: dict, screen_id: str,
              reason: Optional[str], webhook_url: Optional[str], who,
              request_id: str) -> str:
    """Queue the deep analysis, through whichever backend is configured.

    Deliberately `mode=full` rather than `mode=ai`. Somebody disputing a
    verdict is owed the evidence, not a second bare label - and `full` is also
    the mode that cannot short-circuit back to Level 1, which would otherwise
    return the very answer being appealed.
    """
    from ...core.security import get_limiter
    from ...services.jobs import get_runner, get_store
    from ...services.queue import distributed
    from .analyses import _dispatch_remote, run_analysis

    meta = {"mode": "full", "filename": display_name, "reference": None,
            "verify_policy": "never", "target_genre": None,
            "request_id": request_id, "api_key": who.fingerprint,
            "fields": None, "escalated_from": screen_id,
            "appeal_reason": reason}

    store = get_store()
    job = store.create(meta=meta, owner=who.id)

    if distributed():
        _dispatch_remote(path, job.id, "full", "never", display_name, None,
                         None, None, webhook_url, who, request_id,
                         escalated_from=screen_id)
        return job.id

    # Local path: the runner needs a file that outlives this request, so the
    # appeal's temp copy is duplicated into a staged upload the job owns.
    import shutil

    from ...core.uploads import stage_prefix

    staged = f"{stage_prefix(request_id)}-appeal{os.path.splitext(path)[1]}"
    shutil.copyfile(path, staged)

    limiter, bucket = get_limiter(), bucket_for(who)
    limiter.acquire(bucket, settings.server.max_inflight_per_key)

    def cleanup() -> None:
        limiter.release(bucket)
        _unlink(staged)

    get_runner().submit(
        job,
        lambda progress: run_analysis(
            staged, "full", "never", display_name, progress,
            persist={"id": job.id, "filename": display_name,
                     "reference": None, "api_key_id": who.id,
                     "request_id": request_id}),
        webhook_url=webhook_url, cleanup=cleanup)
    return job.id


def _unlink(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            log.warning("Could not remove %s", path)
