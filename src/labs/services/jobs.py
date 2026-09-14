"""In process job queue for long running analyses.

An analysis takes 40 seconds to 3 minutes on CPU. Holding an HTTP connection
open that long fails behind most load balancers and proxies, which commonly
cut idle requests at 30 or 60 seconds, so the production path is:

    POST /v1/analyses      -> 202 Accepted, returns a job id immediately
    GET  /v1/analyses/{id} -> status, then the result
    webhook                -> optional callback when it finishes

Deliberately in process and in memory. That is the right call for a single
container: it adds no infrastructure and no new failure mode. It does mean
jobs do not survive a restart and do not fan out across replicas, so the
DEPLOYMENT notes say to move to Redis/RQ or Celery when you run more than one
instance. `JobStore` is the seam for that swap.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = (SUCCEEDED, FAILED, CANCELLED)


@dataclass
class Job:
    id: str
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    progress: str = "queued"
    meta: Dict = field(default_factory=dict)
    # Who submitted this, for the ownership check on retrieval. Deliberately
    # outside `meta`, because `public()` splats meta into the response and this
    # is an internal identifier that no caller needs to see.
    owner: Optional[str] = None

    def owned_by(self, principal_id: Optional[str]) -> bool:
        """True if `principal_id` may read this job.

        An unowned job (open deployment, or a static env key with no identity)
        is readable by anyone, which is the pre-existing behaviour for those
        deployments. Once a job carries an owner, only that owner may read it.
        """
        if self.owner is None:
            return True
        return self.owner == principal_id

    def public(self, include_result: bool = True) -> Dict:
        out = {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "queue_seconds": round((self.started_at or time.time()) - self.created_at, 2),
            "duration_seconds": (
                round((self.finished_at or time.time()) - self.started_at, 2)
                if self.started_at else None),
            **self.meta,
        }
        if self.status == FAILED:
            out["error"] = {"code": self.error_code or "analysis_failed",
                            "message": self.error or "Analysis failed."}
        if include_result and self.status == SUCCEEDED:
            out["result"] = self.result
        return out


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class JobStore:
    """Bounded, thread safe, with time based eviction.

    Bounded on purpose: an unbounded dict of finished jobs holding full report
    payloads is a slow memory leak in a long lived process.
    """

    def __init__(self, max_jobs: int = 500, ttl_seconds: float = 3600):
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_jobs
        self._ttl = ttl_seconds

    def create(self, meta: Optional[Dict] = None,
               owner: Optional[str] = None) -> Job:
        job = Job(id=uuid.uuid4().hex, meta=meta or {}, owner=owner)
        with self._lock:
            self._jobs[job.id] = job
            self._evict()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def discard(self, job_id: str) -> None:
        """Drop a job that was created but never submitted.

        A submission can be refused after the job record exists (a duplicate
        idempotency key, an exhausted concurrency budget, a worker pool that
        will not accept the task). Without this the record stays `queued`
        forever: `_evict` only reclaims terminal jobs, so an abandoned one is
        never collected, it inflates the queue depth reported by /health, and
        under sustained refusals the store grows past `max_jobs` unbounded.
        """
        with self._lock:
            self._jobs.pop(job_id, None)

    def update(self, job_id: str, **fields) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def _evict(self) -> None:
        now = time.time()
        stale = [jid for jid, j in self._jobs.items()
                 if j.status in TERMINAL and j.finished_at
                 and (now - j.finished_at) > self._ttl]

        # A job that never started and is older than the TTL is abandoned: the
        # worker pool would have picked it up in milliseconds. Reclaiming it
        # here is the backstop for any submission path that creates a record
        # and then fails before handing it to the runner.
        stale += [jid for jid, j in self._jobs.items()
                  if j.status == QUEUED and j.started_at is None
                  and (now - j.created_at) > self._ttl]

        for jid in stale:
            self._jobs.pop(jid, None)

        while len(self._jobs) > self._max:
            # Drop the oldest terminal job first; never evict live work.
            victim = next((jid for jid, j in self._jobs.items()
                           if j.status in TERMINAL), None)
            if victim is None:
                break
            self._jobs.pop(victim, None)

    def stats(self) -> Dict:
        """Counts for monitoring.

        `pending` is the number that matters operationally: work accepted and
        not yet finished. `total` includes terminal jobs still inside the
        retention window, so it climbs to the store's ceiling under perfectly
        healthy traffic and is useless as an alerting signal on its own.
        """
        with self._lock:
            counts: Dict[str, int] = {}
            for j in self._jobs.values():
                counts[j.status] = counts.get(j.status, 0) + 1
            pending = counts.get(QUEUED, 0) + counts.get(RUNNING, 0)
            return {"total": len(self._jobs), "pending": pending,
                    "queued": counts.get(QUEUED, 0),
                    "running": counts.get(RUNNING, 0),
                    "by_status": counts}


class JobRunner:
    """Runs jobs on a bounded worker pool and fires optional webhooks."""

    def __init__(self, store: JobStore, workers: int = 2):
        self.store = store
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="analysis")

    def submit(self, job: Job, fn: Callable[[Callable[[str], None]], Dict],
               webhook_url: Optional[str] = None,
               cleanup: Optional[Callable[[], None]] = None) -> None:
        """Hand the job to the pool.

        Raises RuntimeError if the pool is shutting down. The caller owns the
        resources `cleanup` releases (the staged upload, the concurrency slot),
        so it must run them itself on that path — nothing else will.
        """
        self._pool.submit(self._run, job, fn, webhook_url, cleanup)

    def _run(self, job: Job, fn, webhook_url, cleanup) -> None:
        from ..core.logging import bind_request_id
        from ..core.metrics import record_analysis

        # ContextVars do not cross into a pool thread, so the id is re-bound
        # here. Without it every line this analysis logs - including the
        # traceback when it fails - is orphaned from the request that caused
        # it, which is precisely the line support needs to find.
        with bind_request_id(job.meta.get("request_id")):
            started = time.time()
            self.store.update(job.id, status=RUNNING, started_at=started,
                              progress="starting")

            def progress(stage: str) -> None:
                self.store.update(job.id, progress=stage)

            try:
                result = fn(progress)
                self.store.update(job.id, status=SUCCEEDED, result=result,
                                  finished_at=time.time(), progress="complete")
                record_analysis("succeeded", time.time() - started)

                # If this analysis exists because someone appealed a Level-1
                # verdict, close that loop now. Best effort by design - the
                # caller already has the result they asked for, so a
                # bookkeeping failure must not turn a finished analysis into a
                # failed one.
                escalated_from = job.meta.get("escalated_from")
                if escalated_from:
                    from .feedback import resolve as resolve_feedback

                    resolve_feedback(escalated_from, result)
            except Exception as exc:
                log.exception("Job %s failed", job.id)
                code = getattr(exc, "error_code", None) or _classify(exc)
                self.store.update(job.id, status=FAILED, error=str(exc),
                                  error_code=code, finished_at=time.time(),
                                  progress="failed")
                record_analysis("failed", time.time() - started)
            finally:
                if cleanup:
                    try:
                        cleanup()
                    except Exception:
                        log.warning("Job %s cleanup failed", job.id,
                                    exc_info=True)
                if webhook_url:
                    self._notify(job.id, webhook_url)

    def _notify(self, job_id: str, url: str) -> None:
        """Deliver the result, signed, with bounded retries.

        The body is signed with HMAC-SHA256 over "<timestamp>.<body>" so a
        receiver can verify both authenticity and freshness:

            expected = hmac_sha256(secret, f"{X-Webhook-Timestamp}.{raw_body}")
            compare against X-Webhook-Signature in constant time

        A failed webhook never fails the job; polling remains the fallback.

        The URL was already vetted at submission time and is re-vetted here,
        immediately before the request. The gap between accepting a job and
        delivering its result is minutes, which is ample time to repoint a DNS
        record at an internal address, so the submission-time check alone is
        not load bearing.

        The request keeps the original hostname rather than substituting the
        resolved IP, because swapping in a literal address breaks TLS
        certificate verification. That leaves a narrow re-resolution window
        between our check and urllib3's connect. Closing it entirely needs a
        pinning transport adapter; it is not closed here because the exposure
        is a millisecond race for the privilege of receiving one analysis
        report, while the attack this actually defends against - naming an
        internal or link-local host outright - is fully blocked.
        """
        import hashlib
        import hmac
        import json as _json

        job = self.store.get(job_id)
        if not job:
            return

        from ..core.config import get_settings
        from ..core.net import WebhookURLError, validate_webhook_url
        cfg = get_settings().server

        try:
            target = validate_webhook_url(url)
        except WebhookURLError as exc:
            log.warning("Webhook for job %s not delivered: %s", job_id, exc.code)
            return

        body = _json.dumps(job.public(), default=str).encode("utf-8")
        ts = str(int(time.time()))
        headers = {
            "User-Agent": "labs-webhook/1",
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts,
            "X-Webhook-Id": job_id,
        }
        if cfg.webhook_secret:
            headers["X-Webhook-Signature"] = hmac.new(
                cfg.webhook_secret.encode("utf-8"),
                f"{ts}.".encode("utf-8") + body,
                hashlib.sha256).hexdigest()

        attempts = max(1, cfg.webhook_retries)
        for attempt in range(1, attempts + 1):
            try:
                import requests
                resp = requests.post(
                    target.url, data=body, headers=headers, timeout=15,
                    # A permitted host that 302s to 169.254.169.254 would
                    # otherwise walk straight through the address check.
                    allow_redirects=False,
                )
                if 200 <= resp.status_code < 300:
                    log.info("Webhook delivered for job %s (attempt %d)",
                             job_id, attempt)
                    return
                log.warning("Webhook for job %s returned %s (attempt %d/%d)",
                            job_id, resp.status_code, attempt, attempts)
            except Exception:
                log.warning("Webhook delivery failed for job %s (attempt %d/%d)",
                            job_id, attempt, attempts, exc_info=True)
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 10))
        log.error("Webhook permanently failed for job %s after %d attempts; "
                  "the result is still readable by polling", job_id, attempts)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    def drain(self, timeout: float = 30.0) -> bool:
        """Stop accepting work and let running jobs finish. True if all did.

        Called on the way out of the lifespan. `shutdown(wait=False)` kills
        in-flight analyses mid-run, and their `finally` never executes: the
        staged upload stays on disk, the concurrency slot is never released,
        and a client polling that job sees `running` forever. Draining costs a
        bounded pause on deploy and avoids all three.
        """
        import threading as _t

        done = _t.Event()

        def _wait() -> None:
            self._pool.shutdown(wait=True)
            done.set()

        # shutdown(wait=True) has no timeout of its own, so it is parked on a
        # helper thread and bounded here. A job still running past the timeout
        # is abandoned, which is the same outcome as before but only after
        # giving it a real chance to finish.
        t = _t.Thread(target=_wait, name="job-drain", daemon=True)
        t.start()
        return done.wait(timeout)


def _classify(exc: Exception) -> str:
    from ..analysis.audio import AudioError
    if isinstance(exc, AudioError):
        return "invalid_audio"
    if isinstance(exc, ValueError):
        return "invalid_request"
    return "internal_error"


_store: Optional[JobStore] = None
_runner: Optional[JobRunner] = None
# Reentrant: get_runner() needs the store while already holding this lock, and
# a plain Lock would deadlock the process on its very first request.
_lock = threading.RLock()


def get_store() -> JobStore:
    global _store
    with _lock:
        if _store is None:
            _store = JobStore(
                max_jobs=int(os.environ.get("LABS_JOB_MAX", "500")),
                ttl_seconds=float(os.environ.get("LABS_JOB_TTL_S", "3600")))
        return _store


def get_runner() -> JobRunner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = JobRunner(
                get_store(),
                workers=int(os.environ.get("LABS_JOB_WORKERS", "2")))
        return _runner


def shutdown(drain_seconds: float = 30.0) -> None:
    """Let running jobs finish, then release the pool, so exit is clean."""
    global _runner
    with _lock:
        if _runner is None:
            return
        runner, _runner = _runner, None
    # Outside the lock: draining blocks, and holding _lock would stall every
    # get_store()/get_runner() caller for the whole drain window.
    if not runner.drain(drain_seconds):
        log.warning("Job drain timed out after %.0fs; abandoning in-flight work",
                    drain_seconds)


def reset() -> None:
    """Test hook: tear down runner and discard the cached store."""
    global _store, _runner
    with _lock:
        if _runner is not None:
            _runner.shutdown(wait=False)
            _runner = None
        _store = None
