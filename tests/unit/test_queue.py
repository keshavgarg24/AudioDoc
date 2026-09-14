"""The distributed queue, and the invariants that make scale-out safe.

boto3 is stubbed rather than mocked against real SQS. What is being tested is
the contract the worker depends on - message round-tripping, the at-least-once
guard, the ordering of the S3/Mongo/SQS writes - not that AWS works.
"""
from __future__ import annotations

import json

import pytest

from labs.services import queue as q


@pytest.fixture(autouse=True)
def _reset_queue():
    yield
    q.reset()


class _FakeSqs:
    """Records calls and replays canned messages."""

    def __init__(self, messages=None, fail_on=()):
        self.sent = []
        self.deleted = []
        self.visibility = []
        self._messages = list(messages or [])
        self._fail_on = set(fail_on)

    def send_message(self, **kw):
        if "send" in self._fail_on:
            raise RuntimeError("network down")
        self.sent.append(kw)
        return {"MessageId": "m1"}

    def receive_message(self, **kw):
        if "receive" in self._fail_on:
            raise RuntimeError("network down")
        if not self._messages:
            return {}
        return {"Messages": [self._messages.pop(0)]}

    def delete_message(self, **kw):
        if "delete" in self._fail_on:
            raise RuntimeError("network down")
        self.deleted.append(kw["ReceiptHandle"])

    def change_message_visibility(self, **kw):
        self.visibility.append((kw["ReceiptHandle"], kw["VisibilityTimeout"]))

    def get_queue_attributes(self, **kw):
        return {"Attributes": {"ApproximateNumberOfMessages": "4",
                               "ApproximateNumberOfMessagesNotVisible": "3"}}


def _queue(monkeypatch, fake, url="https://sqs.x/q"):
    sq = q.SqsQueue(url, region="us-east-1")
    monkeypatch.setattr(sq, "_sqs", lambda: fake)
    return sq


def _job(**kw):
    base = dict(id="j1", mode="ai", audio_key="uploads/j1.mp3",
                audio_bucket="b", filename="t.mp3")
    base.update(kw)
    return q.QueuedJob(**base)


class TestRoundTrip:
    def test_payload_survives_the_wire(self, monkeypatch):
        """Every field the worker reads has to make it across. A dropped
        audio_key is a job that fails on fetch after being accepted."""
        fake = _FakeSqs()
        _queue(monkeypatch, fake).publish(
            _job(target_genre="house", reference="r1", api_key_id="k1",
                 request_id="req1", webhook_url="https://h/x"))

        body = json.loads(fake.sent[0]["MessageBody"])
        back = q.QueuedJob.from_payload(body)
        assert back.id == "j1"
        assert back.audio_key == "uploads/j1.mp3"
        assert back.target_genre == "house"
        assert back.webhook_url == "https://h/x"

    def test_receipt_is_not_published(self, monkeypatch):
        """The receipt is assigned by the RECEIVER. Serialising one would
        travel a stale handle that deletes nothing."""
        fake = _FakeSqs()
        _queue(monkeypatch, fake).publish(_job(receipt="stale"))
        assert "receipt" not in json.loads(fake.sent[0]["MessageBody"])

    def test_unknown_fields_are_ignored(self):
        """A worker on an older image must not crash on a field a newer API
        container added, or a deploy becomes an outage during the rollout."""
        job = q.QueuedJob.from_payload(
            {"id": "j1", "mode": "ai", "filename": "t.mp3",
             "audio_key": "k", "audio_bucket": "b",
             "some_future_field": "whatever"})
        assert job.id == "j1"

    def test_publish_failure_is_raised_not_swallowed(self, monkeypatch):
        """The caller must not answer 202 for work that was never queued."""
        sq = _queue(monkeypatch, _FakeSqs(fail_on=("send",)))
        with pytest.raises(q.QueueUnavailable):
            sq.publish(_job())


class TestReceive:
    def test_returns_job_and_attempt_count(self, monkeypatch):
        fake = _FakeSqs(messages=[{
            "Body": json.dumps(_job().payload()),
            "ReceiptHandle": "rh1",
            "Attributes": {"ApproximateReceiveCount": "2"}}])

        (job, attempts), = _queue(monkeypatch, fake).receive()
        assert job.id == "j1"
        assert job.receipt == "rh1"
        assert attempts == 2

    def test_receive_failure_returns_empty_rather_than_raising(self, monkeypatch):
        """A worker whose poll raised would exit its loop and be replaced,
        turning a transient API blip into a rolling restart of the fleet."""
        sq = _queue(monkeypatch, _FakeSqs(fail_on=("receive",)))
        monkeypatch.setattr(q.time, "sleep", lambda _s: None)
        assert sq.receive() == []

    def test_unparseable_message_is_deleted(self, monkeypatch):
        """Otherwise it recirculates forever, occupying a worker each time."""
        fake = _FakeSqs(messages=[{"Body": "not json", "ReceiptHandle": "rh9"}])
        assert _queue(monkeypatch, fake).receive() == []
        assert fake.deleted == ["rh9"]

    def test_delete_failure_is_survivable(self, monkeypatch):
        """The message reappears and is retried; the worker's terminal-state
        check makes that cheap rather than a re-analysis."""
        sq = _queue(monkeypatch, _FakeSqs(fail_on=("delete",)))
        sq.delete("rh1")            # must not raise


class TestDepth:
    def test_counts_in_flight_as_well_as_visible(self, monkeypatch):
        """THE autoscaling signal.

        Visible-only under-reports exactly when it matters: once every worker
        is busy the visible count falls toward zero while the backlog is at its
        worst, so an alarm on it would scale IN during a pile-up.
        """
        assert _queue(monkeypatch, _FakeSqs()).depth() == 7

    def test_depth_is_none_when_unavailable(self, monkeypatch):
        sq = q.SqsQueue("https://sqs.x/q")

        def _boom():
            raise RuntimeError("no")

        monkeypatch.setattr(sq, "_sqs", _boom)
        assert sq.depth() is None


class TestFifo:
    def test_fifo_queues_get_dedup_attributes(self, monkeypatch):
        fake = _FakeSqs()
        _queue(monkeypatch, fake, url="https://sqs.x/q.fifo").publish(_job())
        assert fake.sent[0]["MessageGroupId"] == "j1"
        assert fake.sent[0]["MessageDeduplicationId"] == "j1"

    def test_standard_queues_do_not(self, monkeypatch):
        """Sending those attributes to a standard queue is an API error."""
        fake = _FakeSqs()
        _queue(monkeypatch, fake).publish(_job())
        assert "MessageGroupId" not in fake.sent[0]


class TestModeSelection:
    def test_no_url_means_single_container(self, monkeypatch):
        monkeypatch.delenv("LABS_SQS_QUEUE_URL", raising=False)
        assert q.get_queue() is None
        assert q.distributed() is False

    def test_sqs_without_persistence_is_not_distributed(self, monkeypatch):
        """Both halves are required. SQS carries the WORK and MongoDB carries
        the STATE; with only the first, a job accepted by one container and
        finished by another is unreportable and the submitter polls forever.
        """
        monkeypatch.setenv("LABS_SQS_QUEUE_URL", "https://sqs.x/q")
        monkeypatch.delenv("LABS_MONGO_URI", raising=False)
        from labs.services import storage

        storage.reset()
        assert q.distributed() is False

    def test_both_configured_is_distributed(self, monkeypatch, mongo):
        monkeypatch.setenv("LABS_SQS_QUEUE_URL", "https://sqs.x/q")
        assert q.distributed() is True
        assert q.get_queue() is not None

    def test_visibility_timeout_exceeds_the_worst_analysis(self, monkeypatch):
        """A timeout under the analysis time hands the message to a second
        worker mid-run and the track is analysed twice at full cost."""
        monkeypatch.setenv("LABS_SQS_QUEUE_URL", "https://sqs.x/q")
        assert q.get_queue().visibility_timeout >= 300

    def test_long_poll_is_at_the_sqs_maximum(self):
        """20 s is one billable ReceiveMessage per 20 s of idle time instead of
        one per poll interval - the difference between cents and dollars a
        month for every idle worker."""
        assert q.WAIT_SECONDS == 20
