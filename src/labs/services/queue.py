"""Where queued analyses live, and therefore whether the service can scale out.

TWO BACKENDS, ONE INTERFACE
---------------------------
    LocalQueue   the existing in-process ThreadPoolExecutor. No infrastructure,
                 no new failure mode, and the right choice for one container.
                 Jobs die with the process and cannot be seen by a sibling.

    SqsQueue     the job is a message and the state is a MongoDB document, so
                 any worker in the fleet can take it and any API container can
                 report on it.

The distinction matters for autoscaling, and not in a subtle way. With the
local queue, "scale out" adds capacity that the load balancer can only reach
for NEW submissions - the work already accepted by an overloaded container
stays there, and a scale-IN event destroys it. Neither problem is fixable
inside one process, which is why the seam exists.

WHICH ONE YOU GET
-----------------
SQS the moment LABS_SQS_QUEUE_URL is set AND persistence is configured, local
otherwise. Both conditions are required: an SQS job whose state has nowhere to
live would be accepted, run, and then be unreportable, because the API
container that took the submission is not the one that finished it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

LOCAL = "local"
SQS = "sqs"

# Long-poll duration. 20 s is the SQS maximum and the only sensible value: it
# is one billable ReceiveMessage request per 20 s of idle time instead of one
# per poll interval, which is the difference between cents and dollars a month
# per idle worker.
WAIT_SECONDS = 20


@dataclass
class QueuedJob:
    """What a worker needs in order to run an analysis it did not accept."""
    id: str
    mode: str
    audio_key: Optional[str]      # S3 key; the worker fetches it
    audio_bucket: Optional[str]
    filename: str
    verify_policy: str = "never"
    target_genre: Optional[str] = None
    reference: Optional[str] = None
    api_key_id: Optional[str] = None
    request_id: Optional[str] = None
    fields: Optional[str] = None
    webhook_url: Optional[str] = None
    # Set when this analysis exists because a human appealed a Level-1
    # verdict. The worker uses it to record which of the two was right.
    escalated_from: Optional[str] = None
    # Set by the receiver, not the sender. Used to delete the message and to
    # extend its visibility while a long analysis is still running.
    receipt: Optional[str] = None

    def payload(self) -> Dict:
        out = {k: v for k, v in self.__dict__.items() if k != "receipt"}
        return out

    @classmethod
    def from_payload(cls, data: Dict, receipt: Optional[str] = None) -> "QueuedJob":
        known = {k: data.get(k) for k in cls.__dataclass_fields__
                 if k != "receipt"}
        return cls(receipt=receipt, **known)


class QueueUnavailable(RuntimeError):
    """The backend refused the work. The caller must not report success."""


# --------------------------------------------------------------------------
class SqsQueue:
    """Durable, at-least-once, and shared across the fleet."""

    def __init__(self, queue_url: str, region: Optional[str] = None,
                 visibility_timeout: int = 900):
        self.queue_url = queue_url
        self.region = region
        # Must exceed the longest analysis, or SQS hands the message to a
        # second worker while the first is still working on it and the track
        # is analysed twice at full cost. 900 s against a ~90 s p99 is
        # deliberate headroom, and `extend` covers the pathological case.
        self.visibility_timeout = visibility_timeout
        self._client = None
        self._lock = threading.Lock()

    def _sqs(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import boto3

                    self._client = boto3.client("sqs", region_name=self.region)
        return self._client

    @property
    def kind(self) -> str:
        return SQS

    def publish(self, job: QueuedJob) -> None:
        try:
            self._sqs().send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(job.payload()),
                # Groups by analysis id so a FIFO queue - if someone chooses
                # one - deduplicates retries of the same submission rather
                # than serialising the whole fleet behind one group.
                **({"MessageGroupId": job.id,
                    "MessageDeduplicationId": job.id}
                   if self.queue_url.endswith(".fifo") else {}))
        except Exception as exc:
            raise QueueUnavailable(f"could not enqueue {job.id}: {exc}") from exc

    def receive(self, max_messages: int = 1):
        """Long-poll for work. Returns [] on any error rather than raising.

        A worker whose poll raises would exit its loop and be replaced by the
        orchestrator, turning a transient API blip into a rolling restart of
        the whole fleet.
        """
        try:
            resp = self._sqs().receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max(1, min(max_messages, 10)),
                WaitTimeSeconds=WAIT_SECONDS,
                VisibilityTimeout=self.visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"])
        except Exception:
            log.warning("SQS receive failed", exc_info=True)
            time.sleep(2)
            return []

        out = []
        for msg in resp.get("Messages", []):
            try:
                body = json.loads(msg["Body"])
            except Exception:
                log.error("Unparseable SQS message; deleting so it does not "
                          "poison the queue")
                self.delete(msg.get("ReceiptHandle"))
                continue
            job = QueuedJob.from_payload(body, receipt=msg.get("ReceiptHandle"))
            attempts = int(
                (msg.get("Attributes") or {}).get("ApproximateReceiveCount", 1))
            out.append((job, attempts))
        return out

    def delete(self, receipt: Optional[str]) -> None:
        if not receipt:
            return
        try:
            self._sqs().delete_message(QueueUrl=self.queue_url,
                                       ReceiptHandle=receipt)
        except Exception:
            # The message reappears after the visibility timeout and is
            # retried. The worker checks Mongo for a terminal state first, so
            # a redelivery of finished work is cheap rather than a re-analysis.
            log.warning("SQS delete failed; the message will be redelivered",
                        exc_info=True)

    def extend(self, receipt: Optional[str], seconds: int) -> None:
        """Push the visibility deadline out for a job still in progress."""
        if not receipt:
            return
        try:
            self._sqs().change_message_visibility(
                QueueUrl=self.queue_url, ReceiptHandle=receipt,
                VisibilityTimeout=seconds)
        except Exception:
            log.debug("Visibility extension failed", exc_info=True)

    def depth(self) -> Optional[int]:
        """Messages visible plus in flight - the autoscaling signal.

        Visible alone under-reports a saturated fleet: once every worker is
        busy the visible count falls to zero while the backlog is at its worst.
        """
        try:
            attrs = self._sqs().get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=["ApproximateNumberOfMessages",
                                "ApproximateNumberOfMessagesNotVisible"],
            )["Attributes"]
            return (int(attrs.get("ApproximateNumberOfMessages", 0))
                    + int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)))
        except Exception:
            log.debug("Queue depth unavailable", exc_info=True)
            return None


class LocalQueue:
    """Runs the job in this process, via the existing runner."""

    def __init__(self, run: Callable[[QueuedJob], None]):
        self._run = run

    @property
    def kind(self) -> str:
        return LOCAL

    def publish(self, job: QueuedJob) -> None:
        self._run(job)

    def depth(self) -> Optional[int]:
        from .jobs import get_store

        return get_store().stats().get("pending")


# --------------------------------------------------------------------------
_queue = None
_lock = threading.RLock()


def configured_url() -> Optional[str]:
    return os.environ.get("LABS_SQS_QUEUE_URL") or None


def distributed() -> bool:
    """True when the fleet can scale out.

    Both halves are required. SQS carries the WORK; MongoDB carries the STATE.
    With only the first, a job is accepted by one container and finished by
    another that cannot tell anyone, so the submitter polls forever.
    """
    from .storage import get_mongo

    return bool(configured_url()) and get_mongo().enabled


def get_queue() -> Optional[SqsQueue]:
    """The SQS queue, or None when this deployment is single-container."""
    global _queue
    if not configured_url():
        return None
    with _lock:
        if _queue is None:
            from ..core.config import get_settings

            cfg = get_settings()
            _queue = SqsQueue(
                configured_url(),
                region=(os.environ.get("LABS_SQS_REGION")
                        or cfg.storage.s3_region),
                visibility_timeout=int(
                    os.environ.get("LABS_SQS_VISIBILITY_S", "900")))
        return _queue


def reset() -> None:
    """Test hook."""
    global _queue
    with _lock:
        _queue = None
