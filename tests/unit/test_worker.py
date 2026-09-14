"""The worker's message handling.

Everything here is about what happens when SQS misbehaves in the ways it is
DOCUMENTED to misbehave - at-least-once delivery, redelivery after a failed
delete - plus the distinction between an error worth retrying and one that will
fail identically forever.
"""
from __future__ import annotations

import pytest

from labs.analysis.audio import AudioError
from labs.services.queue import QueuedJob
from labs.worker import Worker


class _FakeQueue:
    visibility_timeout = 900

    def __init__(self):
        self.deleted = []
        self.extended = []

    def delete(self, receipt):
        self.deleted.append(receipt)

    def extend(self, receipt, seconds):
        self.extended.append((receipt, seconds))


class _FakeMongo:
    def __init__(self, terminal=False):
        self._terminal = terminal
        self.updates = []

    def job_is_terminal(self, job_id):
        return self._terminal

    def update_job(self, job_id, **fields):
        self.updates.append(fields)
        return True

    def get_analysis(self, *a, **kw):
        return None

    @property
    def enabled(self):
        return True

    def ensure_indexes(self):
        return {"enabled": True, "ok": True}

    def last(self, key):
        for u in reversed(self.updates):
            if key in u:
                return u[key]
        return None


@pytest.fixture()
def wired(monkeypatch):
    """A Worker with its queue, storage and analysis all stubbed."""
    from labs.services import storage

    mongo = _FakeMongo()
    monkeypatch.setattr(storage, "get_mongo", lambda cfg=None: mongo)

    w = Worker()
    w.queue = _FakeQueue()
    w._detector = object()
    # Heartbeats spawn a thread and sleep; not wanted in a unit test.
    monkeypatch.setattr(w, "_start_heartbeat", lambda job: _Done())
    return w, mongo


class _Done:
    def set(self):
        pass


def _job(**kw):
    base = dict(id="j1", mode="ai", audio_key="k", audio_bucket="b",
                filename="t.mp3", receipt="rh1")
    base.update(kw)
    return QueuedJob(**base)


class TestRedelivery:
    def test_a_terminal_job_is_dropped_not_rerun(self, wired, monkeypatch):
        """SQS is at-least-once, so a finished job WILL sometimes arrive
        again. Re-running it costs another 30-90 s of CPU for an answer
        already stored."""
        w, mongo = wired
        mongo._terminal = True
        ran = []
        monkeypatch.setattr(w, "_fetch", lambda job: ran.append(job) or "/x")

        w._handle(_job(), attempts=2)

        assert ran == []
        assert w.queue.deleted == ["rh1"]

    def test_retries_are_bounded(self, wired, monkeypatch):
        """Something about this file breaks the pipeline deterministically, so
        the next attempt fails the same way. Without a ceiling it recirculates
        forever, occupying a worker each time."""
        w, mongo = wired
        ran = []
        monkeypatch.setattr(w, "_fetch", lambda job: ran.append(1) or "/x")

        w._handle(_job(), attempts=99)

        assert ran == []
        assert mongo.last("status") == "failed"
        assert mongo.last("error")["code"] == "retries_exhausted"
        assert w.queue.deleted == ["rh1"]


class TestFailureClassification:
    def _fail_with(self, w, monkeypatch, exc):
        monkeypatch.setattr(w, "_fetch", lambda job: "/tmp/does-not-exist")

        def _boom(*a, **kw):
            raise exc

        import labs.tiers as tiers_mod

        monkeypatch.setattr(tiers_mod, "analyse", _boom)

    def test_bad_audio_fails_terminally(self, wired, monkeypatch):
        """A caller error fails identically on every retry, so retrying it
        just burns a worker slot to reach the same conclusion."""
        w, mongo = wired
        self._fail_with(w, monkeypatch, AudioError("too short"))

        w._handle(_job(), attempts=1)

        assert mongo.last("status") == "failed"
        assert mongo.last("error")["code"] == "invalid_audio"
        assert w.queue.deleted == ["rh1"], "a terminal failure must be deleted"

    def test_an_internal_error_is_left_for_redelivery(self, wired, monkeypatch):
        """It might be transient - an S3 blip, a cold NAT - so the message is
        NOT deleted and SQS retries it up to the ceiling."""
        w, mongo = wired
        self._fail_with(w, monkeypatch, RuntimeError("oom"))

        w._handle(_job(), attempts=1)

        assert mongo.last("status") == "queued"
        assert w.queue.deleted == [], "a retryable failure must not be deleted"

    def test_a_bad_request_fails_terminally(self, wired, monkeypatch):
        w, mongo = wired
        self._fail_with(w, monkeypatch, ValueError("unknown mode"))

        w._handle(_job(), attempts=1)

        assert mongo.last("error")["code"] == "invalid_request"
        assert w.queue.deleted == ["rh1"]


class TestFetch:
    def test_a_job_without_s3_is_rejected_clearly(self, wired):
        """The worker cannot read the API container's temp directory, so this
        misconfiguration has to name itself rather than surface as a missing
        file."""
        w, _ = wired
        with pytest.raises(ValueError, match="LABS_AUDIO_BUCKET"):
            w._fetch(_job(audio_key=None, audio_bucket=None))


class TestRefusalToStart:
    def test_no_queue_is_a_clean_exit(self, monkeypatch):
        monkeypatch.delenv("LABS_SQS_QUEUE_URL", raising=False)
        from labs.services import queue

        queue.reset()
        assert Worker().run() == 2

    def test_no_persistence_is_a_clean_exit(self, monkeypatch):
        """A distributed worker without shared state would analyse every track
        and have nowhere to publish the result. Refusing to start is far better
        than silently dropping the whole queue on the floor.
        """
        monkeypatch.setenv("LABS_SQS_QUEUE_URL", "https://sqs.x/q")
        from labs.services import queue, storage

        queue.reset()
        storage.reset()

        class _Off:
            enabled = False

        monkeypatch.setattr(storage, "get_mongo", lambda cfg=None: _Off())
        w = Worker()
        assert w.run() == 2


class TestConcurrencyStance:
    def test_the_heartbeat_is_well_inside_the_visibility_window(self):
        """If the heartbeat interval exceeded the visibility timeout the
        message would be handed to a second worker mid-analysis, and the track
        would be processed twice at full cost."""
        from labs.services.queue import SqsQueue
        from labs.worker import HEARTBEAT_SECONDS

        assert HEARTBEAT_SECONDS < SqsQueue("u").visibility_timeout / 2
