"""The analysis worker: a separate process that consumes the queue.

WHY THIS IS NOT THE API
-----------------------
An analysis is 30-90 s of saturated CPU. Running it inside the API process, as
the single-container path does, has three consequences that only a split fixes:

  * the health check competes with the work. Two analyses on a 4-core box and
    /health starts missing its deadline, so the load balancer removes a
    container that is working perfectly.
  * the scaling signal is wrong. API containers should scale on request count
    and workers on queue depth; one process has to pick one, and whichever it
    picks is wrong for the other half of its job.
  * a deploy is destructive. Replacing an API container mid-analysis discards
    work the caller was told had been accepted.

So: API containers validate, store and enqueue, and never analyse. Workers
analyse and never serve.

CONCURRENCY
-----------
One analysis at a time per worker, and there is deliberately no knob for it.
The pipeline is CPU-bound, so two concurrent analyses on the same box each run
at roughly half speed - identical aggregate throughput, double the latency and
double the peak memory. No setting of a concurrency dial improves on one, so
capacity comes from more CONTAINERS and container size comes from
LABS_TORCH_THREADS.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import tempfile
import threading
import time
from typing import Optional

from .core.config import get_settings
from .services.queue import QueuedJob, get_queue

log = logging.getLogger(__name__)

# Give up on a message SQS has handed us this many times. Something about this
# specific file breaks the pipeline deterministically, so the next attempt will
# fail the same way; without a ceiling it recirculates forever and occupies a
# worker that could be doing real work. Configure a redrive policy on the queue
# and these land in the dead-letter queue instead.
MAX_ATTEMPTS = 3

# How often to push the visibility deadline out while an analysis runs. Must be
# comfortably under the queue's visibility timeout or the message is handed to
# a second worker mid-analysis and the track is processed twice at full cost.
HEARTBEAT_SECONDS = 120


class Worker:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.queue = get_queue()
        self._stop = threading.Event()
        self._detector = None

    # -- lifecycle ----------------------------------------------------------
    def install_signal_handlers(self) -> None:
        """Finish the current analysis, then exit.

        ECS sends SIGTERM and waits out the stop timeout before SIGKILL. A
        worker that died immediately would leave the message invisible until
        its timeout expired, stalling that job for up to 15 minutes; draining
        instead lets it finish and delete the message cleanly.
        """
        def _handle(signum, _frame):
            log.info("Signal %s received; finishing the current analysis and "
                     "then exiting", signum)
            self._stop.set()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    def warm(self) -> None:
        """Load everything before taking work.

        A cold worker that pulls a message first would hold it invisible for
        the ~10 s of model load on top of the analysis. Warming first means the
        queue depth reflects real backlog rather than startup.
        """
        from .ml.detector import get_detector
        from .screen import models as screen_models

        if self.settings.screen.enabled:
            screen_models.warm(self.settings.screen)

        self._detector = get_detector(self.settings)
        started = time.time()
        self._detector.load()
        log.info("Worker ready in %.1fs", time.time() - started)

    # -- the loop -----------------------------------------------------------
    def run(self) -> int:
        if self.queue is None:
            log.error("LABS_SQS_QUEUE_URL is not set. The worker has no queue "
                      "to consume; run the API alone for the single-container "
                      "deployment.")
            return 2

        from .services.storage import get_mongo

        if not get_mongo().enabled:
            # Without persistence the worker's results are unreachable: the API
            # container that accepted the submission is not this process, so
            # there is nowhere to publish a result the submitter can poll.
            log.error("LABS_MONGO_URI is not set. A distributed worker cannot "
                      "report results without shared state; refusing to start "
                      "rather than silently dropping every analysis.")
            return 2

        get_mongo().ensure_indexes()
        self.install_signal_handlers()
        self.warm()

        log.info("Polling %s", self.queue.queue_url)
        while not self._stop.is_set():
            for job, attempts in self.queue.receive(max_messages=1):
                if self._stop.is_set():
                    # Do NOT delete: let it return to the queue for a sibling
                    # rather than dropping accepted work on the floor.
                    log.info("Draining; returning job %s to the queue", job.id)
                    self.queue.extend(job.receipt, 0)
                    break
                self._handle(job, attempts)
        log.info("Worker stopped")
        return 0

    def _handle(self, job: QueuedJob, attempts: int) -> None:
        from .services.storage import get_mongo

        mongo = get_mongo()

        # SQS is at-least-once, and a 90 s analysis against a 900 s visibility
        # timeout still leaves redelivery possible after a failed delete. This
        # is much cheaper than re-running the pipeline to discover we already
        # had the answer.
        if mongo.job_is_terminal(job.id):
            log.info("Job %s is already terminal; dropping the redelivery",
                     job.id)
            self.queue.delete(job.receipt)
            return

        if attempts > MAX_ATTEMPTS:
            log.error("Job %s has been delivered %d times; failing it rather "
                      "than recirculating", job.id, attempts)
            mongo.update_job(
                job.id, status="failed", progress="failed",
                finished_at=time.time(),
                error={"code": "retries_exhausted",
                       "message": "The analysis failed repeatedly."})
            self.queue.delete(job.receipt)
            return

        heartbeat = self._start_heartbeat(job)
        started = time.time()
        path = None
        try:
            mongo.update_job(job.id, status="running", progress="fetching",
                             started_at=started)
            path = self._fetch(job)
            mongo.update_job(job.id, progress="analysing")

            from . import tiers
            from .api.v1.analyses import attach_detection

            report = tiers.analyse(
                path, mode=job.mode, settings=self.settings,
                detector=self._detector, display_name=job.filename,
                target_genre=job.target_genre,
                progress=lambda stage: mongo.update_job(job.id, progress=stage))

            if not report.get("early_exit"):
                report = attach_detection(report, job.mode, job.verify_policy,
                                          path, job.filename)

            info = self._detector.info
            report["model"] = {
                "revision": getattr(info, "revision", None),
                "device": getattr(info, "device", None),
                "api_version": self.settings.server.api_version,
            }

            self._persist(job, report, path, time.time() - started)

            # Same appeal bookkeeping as the in-process runner, so the
            # feedback loop closes identically in both topologies.
            if job.escalated_from:
                from .services.feedback import resolve as resolve_feedback

                resolve_feedback(job.escalated_from, report)

            mongo.update_job(job.id, status="succeeded", progress="complete",
                             finished_at=time.time(),
                             duration_seconds=round(time.time() - started, 2))
            log.info("Job %s finished in %.1fs", job.id, time.time() - started)
            self.queue.delete(job.receipt)
        except Exception as exc:
            log.exception("Job %s failed", job.id)
            code = self._classify(exc)
            # A caller error will fail identically on every retry, so it is
            # terminal now. An internal error might be transient, so the
            # message is left to be redelivered until MAX_ATTEMPTS.
            terminal = code in ("invalid_audio", "invalid_request")
            mongo.update_job(
                job.id, status="failed" if terminal else "queued",
                progress="failed" if terminal else "queued",
                finished_at=time.time() if terminal else None,
                error={"code": code, "message": str(exc)[:500]})
            if terminal:
                self.queue.delete(job.receipt)
        finally:
            heartbeat.set()
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    log.warning("Could not remove %s", path)

    # -- helpers ------------------------------------------------------------
    def _start_heartbeat(self, job: QueuedJob) -> threading.Event:
        """Keep extending the message's visibility while we hold it."""
        done = threading.Event()

        def _beat() -> None:
            while not done.wait(HEARTBEAT_SECONDS):
                self.queue.extend(job.receipt, self.queue.visibility_timeout)

        threading.Thread(target=_beat, name=f"hb-{job.id[:8]}",
                         daemon=True).start()
        return done

    def _fetch(self, job: QueuedJob) -> str:
        """Bring the audio local. It was uploaded to S3 by the API container.

        This is the reason S3 is mandatory for the distributed deployment: the
        process that received the bytes is not the process that analyses them,
        so a local temp file is not reachable.
        """
        if not (job.audio_bucket and job.audio_key):
            raise ValueError(
                "Queued job carries no S3 audio location. The distributed "
                "deployment requires LABS_AUDIO_BUCKET, because the worker "
                "cannot read the API container's temp directory.")

        import boto3

        suffix = os.path.splitext(job.audio_key)[1] or ".audio"
        fh = tempfile.NamedTemporaryFile(prefix="labs-job-", suffix=suffix,
                                         delete=False)
        fh.close()
        boto3.client("s3", region_name=self.settings.storage.s3_region) \
            .download_file(job.audio_bucket, job.audio_key, fh.name)
        return fh.name

    def _persist(self, job: QueuedJob, report: dict, path: str,
                 seconds: float) -> None:
        """Store the report. A storage failure must not fail the analysis."""
        from .services.persistence import record

        try:
            record(analysis_id=job.id, report=report, path=path,
                   filename=job.filename or "audio", seconds=seconds,
                   mode=job.mode, api_key_id=job.api_key_id,
                   request_id=job.request_id, reference=job.reference,
                   source="worker")
        except Exception:
            log.warning("Persistence failed for %s", job.id, exc_info=True)

    @staticmethod
    def _classify(exc: Exception) -> str:
        from .analysis.audio import AudioError

        if isinstance(exc, AudioError):
            return "invalid_audio"
        if isinstance(exc, ValueError):
            return "invalid_request"
        return "internal_error"


def main(argv: Optional[list] = None) -> int:
    from .core.logging import configure

    configure()
    return Worker().run()


if __name__ == "__main__":
    sys.exit(main())
