"""Analysis submission and retrieval.

Analyses are asynchronous. A 40s to 3min synchronous request is cut by most
load balancers and proxies, so the production path is:

    POST /v1/analyses      -> 202 Accepted with a job id
    GET  /v1/analyses/{id} -> status, then the result
    webhook                -> optional signed callback on completion

Secondary verification never runs unless the caller asks for it. `verify`
defaults to "never", meaning the primary model alone decides. The provider
behind verification is an internal detail and is never named in a response.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ... import tiers
from ...core.config import get_settings
from ...core.security import DEEP_SCOPE, get_limiter
from ...services.jobs import get_runner, get_store
from ..deps import bucket_for, require_scope
from ..errors import ErrorResponse, api_error
from ..serialize import jsonable
from ..validate import clean_text, require_choice, safe_filename

log = logging.getLogger(__name__)
router = APIRouter(tags=["analyses"])
settings = get_settings()

# "screen" is accepted here for symmetry, but POST /v1/screen is the better
# route for it: Level 1 finishes inside a normal HTTP timeout, so queueing it
# and making the caller poll adds a round trip and buys nothing.
def get_detector(settings=None):
    """The deep detector, or None when this image does not carry it.

    Deferred so the screen-only image (Dockerfile --target screen), which has
    no torch by design, can serve this module's routes at all. Returns None
    rather than raising, so the caller turns absence into a 503 the client can
    understand instead of a 500.
    """
    try:
        from ...ml.detector import get_detector as _get
    except ImportError:
        return None

    return _get(settings)


# Imported, not restated. The orchestrator owns which modes exist; a second
# copy here is one that can drift, and /v1/health reads this one.
#
# "screen" is accepted for symmetry, but POST /v1/screen is the better route
# for it: Level 1 finishes inside a normal HTTP timeout, so queueing it and
# making the caller poll adds a round trip and buys nothing.
MODES = tiers.MODES
VERIFY_POLICIES = ("never", "auto", "always")


class AnalysisAccepted(BaseModel):
    id: str
    status: str
    mode: str
    filename: str
    poll_url: str
    created_at: Optional[str] = None


class AnalysisStatus(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    status: str
    progress: str


# ------------------------------------------------------------- ingestion --
def stage_upload(file: UploadFile, request_id: str) -> str:
    """Stream an upload to a temp file, enforcing type and size as it goes."""
    from ...core.uploads import stage_prefix
    from ...core.uploads import stage_upload as _stage
    return _stage(file, prefix=stage_prefix(request_id), settings=settings)


# ------------------------------------------------------------------ work --
def attach_detection(report: Dict, mode: str, verify_policy: str,
                     path: str, display_name: str, progress=None) -> Dict:
    """Apply the escalation policy and, only when it fires, verify.

    Nothing here, in its return value, or in any exception it raises may name
    the verification provider.
    """
    from ...services.acrcloud import ACRClient, ACRError
    from ...services.consensus import build_consensus, should_escalate

    def tick(stage: str) -> None:
        if progress:
            progress(stage)

    if mode not in ("ai", "full"):
        return report

    decision = should_escalate(report, verify_policy)
    verification, verification_error = None, None

    if decision["escalate"]:
        client = ACRClient()
        if not client.cfg.configured:
            # Nothing is fabricated in its place; the primary verdict stands.
            log.info("Secondary verification requested but not configured")
            verification_error = {
                "code": "verification_not_configured",
                "message": "Secondary verification is not enabled on this deployment.",
            }
        else:
            tick("verification")
            try:
                verification = client.scan(path, name=display_name)
            except ACRError as exc:
                log.warning("Secondary verification unavailable: %s", exc)
                verification_error = {
                    "code": "verification_unavailable",
                    "message": "Secondary verification could not be completed.",
                }

    consensus = build_consensus(report, verification, decision)
    report["detection"] = {
        "primary": {
            "provider": "primary",
            "prediction": report.get("prediction"),
            "ai_probability": report.get("fake_probability"),
            "ai_probability_unit": "ratio",
            "confidence": report.get("confidence"),
            "raw_logit": report.get("raw_logit"),
            "reliability": report.get("reliability"),
        },
        "verification": verification,
        "verification_error": verification_error,
        "escalation": decision,
        "consensus": consensus,
    }
    _promote(report, consensus, verification)
    return report


def _promote(report: Dict, consensus: Dict, verification: Optional[Dict]) -> None:
    """Let the consensus verdict become the headline one.

    WHY THIS IS NEEDED. `build_consensus` already treats verification as
    authoritative - a positive catalogue match is stronger evidence than any
    statistical inference, so it takes priority over the local model. But
    until now that conclusion lived only inside `detection.consensus`, while
    the top-level `prediction` and `verdict` still carried what the local
    model thought. Every caller reads the top-level fields, so the override
    was real in the report and invisible in practice.

    The primary result is not destroyed - it stays verbatim under
    `detection.primary`, and `verdict_source` says which one is on top - so
    nothing is hidden, it is just no longer the thing a caller trips over.
    """
    if not settings.server.promote_consensus or not verification:
        return
    verdict = consensus.get("verdict")
    if verdict not in ("ai_generated", "human"):
        return

    from ...verdicts import VERDICT_AI, VERDICT_HUMAN

    is_ai = verdict == "ai_generated"
    before = report.get("verdict")

    report["prediction"] = "Fake" if is_ai else "Real"
    report["verdict"] = VERDICT_AI if is_ai else VERDICT_HUMAN
    # Verification is a catalogue lookup rather than a score, so there is no
    # calibrated probability to report for it. Leaving the local model's
    # probability beside an overridden verdict would be actively misleading -
    # it would read as the confidence behind a verdict it did not produce.
    report["verdict_source"] = "verification"
    report["decisive"] = True

    if before and before != report["verdict"]:
        report["verdict_changed"] = {
            "from": before,
            "to": report["verdict"],
            "by": "verification",
            "note": consensus.get("detail", ""),
        }
        log.info("Verification overrode the local verdict: %s -> %s",
                 before, report["verdict"])


def run_analysis(path: str, mode: str, verify_policy: str, display_name: str,
                 progress, target_genre: Optional[str] = None,
                 persist: Optional[Dict] = None) -> Dict:
    """The unit of work a job runs. Persistence never fails the analysis."""
    detector = get_detector(settings)
    started = time.time()

    # Through the tier orchestrator rather than straight to the detector, so
    # `ai` requests get the Level-1 screen first and can skip the backbone
    # entirely when it is decisive. See labs.tiers for which modes short-circuit.
    from ... import tiers

    report = tiers.analyse(path, mode=mode, settings=settings,
                           detector=detector, display_name=display_name,
                           target_genre=target_genre, progress=progress)

    # An early exit never reached Level 2, so there is no primary model verdict
    # for the escalation policy to weigh and nothing to verify against.
    if not report.get("early_exit"):
        report = attach_detection(report, mode, verify_policy, path,
                                  display_name, progress=progress)

    info = detector.info
    report["model"] = {
        "revision": getattr(info, "revision", None),
        "device": getattr(info, "device", None),
        "api_version": settings.server.api_version,
    }

    if persist:
        progress("storing")
        try:
            _persist(path, report, persist, time.time() - started, mode)
        except Exception:
            log.warning("Persistence failed for %s", persist.get("id"),
                        exc_info=True)

    progress("finalising")
    return report


def _persist(path: str, report: Dict, meta: Dict, seconds: float,
             mode: str) -> None:
    """Same recorder the synchronous path uses, so storage cannot drift."""
    from ...services.persistence import record

    record(analysis_id=meta["id"], report=report, path=path,
           filename=meta.get("filename") or "audio", seconds=seconds,
           mode=mode, api_key_id=meta.get("api_key_id"),
           request_id=meta.get("request_id"),
           reference=meta.get("reference"), source="api")


# ---------------------------------------------------------------- routes --
@router.post("/analyses", status_code=202, response_model=AnalysisAccepted,
             responses={400: {"model": ErrorResponse},
                        413: {"model": ErrorResponse},
                        415: {"model": ErrorResponse},
                        422: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}},
             summary="Submit a track for analysis")
async def create_analysis(
    request: Request,
    file: UploadFile = File(..., description="Audio file to analyse"),
    mode: str = Form("ai", description="ai | audio | full"),
    verify: str = Form(None, description="never | auto | always. "
                                         "Defaults to LABS_VERIFY_DEFAULT."),
    genre: Optional[str] = Form(None, description="Target genre for the transformation guide"),
    webhook_url: Optional[str] = Form(None, description="POSTed on completion"),
    reference: Optional[str] = Form(None, description="Your own id, echoed back"),
    fields: Optional[str] = Form(None, description="Comma-separated sections to return"),
    no_cache: bool = Form(False, description="Skip the content-hash dedup cache"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    who=Depends(require_scope("analyze")),
):
    """Queue an analysis and return immediately with a job id.

    `verify` controls secondary verification. It is opt-in:
      never   (default) the primary model alone decides
      auto    verify only when the primary result is weak
      always  verify on every request

    `genre` opts into the transformation guide: what to change to move this
    track toward the named genre. See GET /v1/genres.

    Send an `Idempotency-Key` header to make retries safe.
    """
    # The same id the middleware put on X-Request-Id, so a caller quoting the
    # header can be traced through the queue and into the worker's log lines.
    # Minting a second, unrelated id here is what breaks that chain: the
    # client reports one value and every line about their analysis carries
    # another.
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex

    require_choice(mode, "mode", MODES, "invalid_mode")
    # A deployment with a verification provider configured almost certainly
    # wants it used; the caller can still override per request.
    verify = verify or settings.server.verify_default
    require_choice(verify, "verify", VERIFY_POLICIES, "invalid_verify_policy")
    reference = clean_text(reference, "reference")
    fields = clean_text(fields, "fields")

    if genre:
        from ...artist import genres as g
        clean_text(genre, "genre", max_length=64)
        if not g.resolve(genre):
            raise api_error(422, "invalid_genre",
                            f"Unknown genre '{genre}'. See GET /v1/genres.")

    # Vetted here, at submission, so the caller gets an error they can act on.
    # Delivery re-checks; this is about feedback, not about the control.
    if webhook_url:
        from ...core.net import WebhookURLError, validate_webhook_url
        try:
            validate_webhook_url(webhook_url)
        except WebhookURLError as exc:
            raise api_error(422, exc.code, exc.message) from exc

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in settings.audio.allowed_suffixes:
        raise api_error(
            415, "unsupported_media_type",
            f"Unsupported file type '{suffix or 'unknown'}'. Allowed: "
            f"{', '.join(settings.audio.allowed_suffixes)}.")

    # The deep tier is the billable one. `require_scope("analyze")` above is
    # the coarse gate that admits the caller at all; this is the one that
    # separates the free tier from the paid one, and it runs before the upload
    # is staged so a refused caller is not charged bandwidth either.
    if mode in tiers.DEEP_MODES and not who.can(DEEP_SCOPE):
        raise api_error(
            403, "deep_tier_forbidden",
            f"mode={mode} uses the deep model and requires the 'deep' scope. "
            f"POST /v1/screen is free and needs no scope beyond 'screen'.")

    # Only meaningful when THIS process will run the analysis. An API
    # container in the distributed deployment never loads the backbone - that
    # is the point of the split - so checking its readiness here would refuse
    # every deep request on a correctly configured fleet.
    from ...services.queue import distributed as _distributed

    if not _distributed():
        detector = get_detector(settings)
        if detector is None and mode in tiers.DEEP_MODES:
            raise api_error(
                501, "deep_tier_unavailable",
                "This deployment serves the Level-1 screen only. Use "
                "POST /v1/screen, or route deep requests at an instance built "
                "with the deep dependencies.")
        if detector is not None and mode in ("ai", "full") and not detector.is_ready:
            raise api_error(503, "model_loading",
                            "The detection model is still loading. Retry once "
                            "GET /v1/health reports ready, or use mode=audio.")

    display_name = safe_filename(file.filename)
    path = stage_upload(file, request_id)
    await file.close()

    # Content dedup. Byte-identical audio already analysed in this mode is
    # returned as-is rather than re-run: on this hardware a full pass is
    # minutes of CPU, and a retry after a client timeout is the single most
    # common way the same bytes arrive twice.
    #
    # Matching is exact, on the SHA-256 of the upload. A re-export or re-encode
    # of the same song is different bytes and will be analysed again; catching
    # that needs acoustic fingerprinting, which is not implemented.
    if not no_cache:
        prior = _dedup_hit(path, mode, who.id)
        if prior:
            _unlink(path)
            return JSONResponse(status_code=200, content=prior)

    # Distributed when SQS carries the work and MongoDB carries the state.
    # Both are required; see services/queue.distributed().
    from ...services.queue import distributed

    remote = distributed()
    store = get_store()
    runner = None if remote else get_runner()

    # Everything that can refuse the submission runs before the job record
    # exists. A record created and then abandoned stays `queued` forever:
    # eviction only reclaims terminal jobs, so it would leak a slot, inflate
    # the queue depth on /health, and never be collected.
    limiter, bucket = get_limiter(), bucket_for(who)
    if not limiter.acquire(bucket, settings.server.max_inflight_per_key):
        _unlink(path)
        raise api_error(429, "too_many_inflight",
                        f"This key already has "
                        f"{settings.server.max_inflight_per_key} analyses in "
                        f"flight. Wait for one to finish.")

    job = None
    try:
        job = store.create(
            meta={"mode": mode, "filename": display_name,
                  "reference": reference, "verify_policy": verify,
                  "target_genre": genre, "request_id": request_id,
                  "api_key": who.fingerprint, "fields": fields},
            owner=who.id)

        if idempotency_key:
            from ...services.storage import get_mongo
            existing = get_mongo().claim_idempotency(idempotency_key, job.id)
            if existing:
                store.discard(job.id)
                limiter.release(bucket)
                _unlink(path)
                return AnalysisAccepted(
                    id=existing, status="duplicate", mode=mode,
                    filename=display_name,
                    poll_url=f"/v1/analyses/{existing}")

        if remote:
            # The worker fleet owns the work from here. The concurrency slot is
            # released immediately rather than on completion: it bounds
            # in-flight work in THIS process, and this process is about to do
            # none. Holding it would cap a key at max_inflight_per_key across
            # the whole fleet, which is a far tighter limit than intended.
            _dispatch_remote(path, job.id, mode, verify, display_name, genre,
                             reference, fields, webhook_url, who, request_id)
            # Drop the local record now that the fleet owns the job.
            #
            # It is this process's copy, and NOTHING in this process will ever
            # advance it: the worker that does the work is a different
            # container and reports progress to MongoDB. Left in the store it
            # is not merely stale, it is authoritative - `get_analysis` checks
            # the in-memory store FIRST and returns on a hit, so the Mongo
            # fallback that would have given the real status is never reached.
            #
            # Behind a load balancer that makes polling a coin flip: a poll
            # routed to this container sees `queued` forever while one routed
            # to any other container sees the true state. That is the "job
            # never completes" failure, and it is invisible in the metrics
            # because the work itself succeeded. Discarding here is what makes
            # every container agree with the database.
            #
            # Safe because `_dispatch_remote` writes the job to MongoDB BEFORE
            # publishing the message, so the record a poll falls back to
            # already exists. `job` stays valid for the response below; only
            # the store entry goes.
            store.discard(job.id)
            limiter.release(bucket)
            _unlink(path)
        else:
            def cleanup() -> None:
                limiter.release(bucket)
                _unlink(path)

            runner.submit(
                job,
                lambda progress: run_analysis(path, mode, verify, display_name,
                                              progress, target_genre=genre,
                                              persist={"id": job.id,
                                                       "filename": display_name,
                                                       "reference": reference,
                                                       "api_key_id": who.id,
                                                       "request_id": request_id}),
                webhook_url=webhook_url,
                cleanup=cleanup,
            )
    except Exception:
        # The pool refuses work while shutting down. `cleanup` is only wired
        # into the task, so on this path nothing else releases the slot or
        # removes the upload.
        if job is not None:
            store.discard(job.id)
        limiter.release(bucket)
        _unlink(path)
        log.warning("Could not queue analysis", exc_info=True)
        # `from None`: the cause is already in the log above with its
        # traceback, and chaining it here only risks the shutdown message
        # reaching a caller who can do nothing with it.
        raise api_error(503, "service_unavailable",
                        "The service is not accepting new work right now. "
                        "Retry shortly.") from None

    return AnalysisAccepted(id=job.id, status=job.status, mode=mode,
                            filename=display_name,
                            poll_url=f"/v1/analyses/{job.id}",
                            created_at=job.public().get("created_at"))


@router.get("/analyses/{job_id}", response_model=AnalysisStatus,
            responses={404: {"model": ErrorResponse}},
            summary="Fetch analysis status and result")
def get_analysis(job_id: str, who=Depends(require_scope("read"))):
    """Live job state, falling back to the stored record after eviction."""
    job = get_store().get(job_id)
    if job:
        # Checked on the live path too, not only on the stored fallback. The
        # live window is the entire time the result is interesting, so an
        # ownership check that only covers the database is not a check.
        if not job.owned_by(who.id):
            raise api_error(404, "not_found", "No analysis with that id.")
        body = job.public()
        fields_csv = job.meta.get("fields")
        if fields_csv and body.get("result"):
            from ..fields import filter_fields
            body["result"] = filter_fields(body["result"], fields_csv)
        return JSONResponse(status_code=200, content=body)

    # Not in this process. In a distributed deployment that is the NORMAL
    # case, not an error: the job is almost certainly running on a worker, so
    # the shared job collection is consulted before the archive.
    from ...services.queue import distributed

    if distributed():
        remote = _remote_job_body(job_id, who)
        if remote:
            return JSONResponse(status_code=200, content=remote)

    from ...services.storage import get_mongo
    doc = get_mongo().get_analysis(job_id, full=True)
    if not doc:
        raise api_error(404, "not_found",
                        "No analysis with that id. Results are retained for a "
                        "limited window after completion.")
    if who.id and doc.get("api_key_id") and doc["api_key_id"] != who.id:
        raise api_error(404, "not_found", "No analysis with that id.")

    result = doc.pop("report", None)
    return JSONResponse(status_code=200, content=jsonable({
        "id": doc.pop("_id", job_id), "status": "succeeded",
        "progress": "complete", "source": "stored", **doc, "result": result}))


@router.get("/analyses", summary="List stored analyses")
def list_analyses(limit: int = Query(50, ge=1, le=200),
                  skip: int = Query(0, ge=0),
                  is_ai: Optional[bool] = None, genre: Optional[str] = None,
                  who=Depends(require_scope("read"))):
    """Recent analyses for this key, newest first, plus live queue stats."""
    from ...services.storage import get_mongo

    mongo = get_mongo()
    filters: Dict = {}
    if is_ai is not None:
        filters["verdict.is_ai"] = is_ai
    if genre:
        filters["artist.primary_genre"] = genre

    rows = mongo.list_analyses(api_key_id=who.id, limit=limit, skip=skip,
                               **filters)
    return JSONResponse(status_code=200, content=jsonable({
        "queue": get_store().stats(), "count": len(rows),
        "analyses": rows, "persistence": mongo.enabled}))


# ------------------------------------------------------- distributed path --
def _dispatch_remote(path: str, job_id: str, mode: str, verify: str,
                     display_name: str, genre: Optional[str],
                     reference: Optional[str], fields: Optional[str],
                     webhook_url: Optional[str], who, request_id: str,
                     escalated_from: Optional[str] = None) -> None:
    """Hand the job to SQS for the worker fleet.

    ORDER MATTERS, TWICE.

    The audio goes to S3 FIRST, because the worker is a different container and
    cannot read this one's temp directory - a message published before the
    upload lands is a job that fails on fetch.

    The job record goes to MongoDB SECOND and the message THIRD. Published
    first, a fast worker can finish and update a document that does not exist,
    whereupon the upsert recreates it without an owner and the submitter is
    locked out of their own result.
    """
    from ...services.queue import QueuedJob, QueueUnavailable, get_queue
    from ...services.storage import get_audio_store, get_mongo

    store = get_audio_store()
    if not store.enabled:
        raise QueueUnavailable(
            "LABS_AUDIO_BUCKET is required for the distributed deployment: "
            "the worker cannot read this container's filesystem.")

    located = store.put(path, job_id, display_name)
    if not located or not located.get("key"):
        raise QueueUnavailable("could not stage the upload to S3")

    meta = {"mode": mode, "filename": display_name, "reference": reference,
            "verify_policy": verify, "target_genre": genre,
            "request_id": request_id, "api_key": who.fingerprint,
            "fields": fields, "escalated_from": escalated_from}
    get_mongo().create_job(job_id, meta=meta, api_key_id=who.id)

    get_queue().publish(QueuedJob(
        id=job_id, mode=mode, audio_key=located["key"],
        audio_bucket=located.get("bucket"), filename=display_name,
        verify_policy=verify, target_genre=genre, reference=reference,
        api_key_id=who.id, request_id=request_id, fields=fields,
        webhook_url=webhook_url, escalated_from=escalated_from))


def _remote_job_body(job_id: str, who) -> Optional[Dict]:
    """Job state for a job this container never ran."""
    from ...services.storage import get_mongo

    doc = get_mongo().get_job(job_id)
    if not doc:
        return None
    if who.id and doc.get("api_key_id") and doc["api_key_id"] != who.id:
        return None

    status = doc.get("status", "queued")
    body = {
        "id": job_id,
        "status": status,
        "progress": doc.get("progress", status),
        "created_at": doc.get("created_at"),
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "mode": doc.get("mode"),
        "filename": doc.get("filename"),
        "reference": doc.get("reference"),
        "source": "queue",
    }
    if doc.get("error"):
        body["error"] = doc["error"]
    if status == "succeeded":
        # The report lives in `analyses`, written by the worker. A succeeded
        # job whose report is not readable yet is reported as still running
        # rather than as succeeded-with-no-result, which a client would
        # reasonably treat as an empty verdict.
        stored = get_mongo().get_analysis(job_id, full=True)
        result = (stored or {}).pop("report", None)
        if result is None:
            body["status"] = "running"
            body["progress"] = "storing"
        else:
            body["result"] = result
    return jsonable(body)


# ---------------------------------------------------------------- helpers --
def _dedup_hit(path: str, mode: str,
               api_key_id: Optional[str]) -> Optional[Dict]:
    """A stored analysis of byte-identical audio in the same mode, if any.

    Best effort throughout: a storage problem here must degrade to "run the
    analysis again", never to a failed request.

    Scoped to `api_key_id`: the cache may only return a caller their OWN prior
    result. See find_report_by_hash for why an unscoped hash lookup both leaks
    across tenants and hands back a job id the caller cannot poll.
    """
    from ...services.storage import get_mongo
    from ...tools.base import file_digest

    mongo = get_mongo()
    if not mongo.enabled:
        return None
    # No owner, no cache: a global lookup is exactly the unsafe case.
    if not api_key_id:
        return None
    try:
        digest = file_digest(path)
        doc = mongo.find_report_by_hash(digest, mode=mode,
                                        api_key_id=api_key_id)
        if not doc:
            return None
        result = doc.pop("report", None)
        if result is None:
            return None
        log.info("Dedup hit for %s (%s)", digest[:12], mode)
        return jsonable({
            "id": doc.pop("_id", None),
            "status": "succeeded",
            "progress": "complete",
            "source": "cache",
            "cached": True,
            "cache_note": "Byte-identical audio was analysed previously; the "
                          "stored result was returned without re-running the "
                          "pipeline. Send no_cache=true to force a fresh run.",
            **doc,
            "result": result,
        })
    except Exception:
        log.debug("Dedup lookup failed", exc_info=True)
        return None


def _unlink(path: str) -> None:
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            log.warning("Could not remove staged upload %s", path)
