"""POST /v1/escalations — the appeal path end to end.

The behaviours worth pinning are the ones a user hits on a bad day: an expired
retention window, a double-clicked button, somebody else's screen id.
"""
from __future__ import annotations

import pytest

from labs.verdicts import VERDICT_AI


@pytest.fixture()
def deep_allowed(monkeypatch):
    """Open the deep gate so the handler itself is reachable.

    The gate is real and tested separately in TestScope; these tests are about
    what the endpoint DOES once a caller is entitled to reach it, and leaving
    it closed would make every one of them a 403 assertion in disguise.
    """
    monkeypatch.setenv("LABS_GATE_DEEP", "0")


@pytest.fixture()
def retained(mongo):
    """A decisive Level-1 result with its audio retained."""
    mongo.save_screen(
        "scr_abc", result={"verdict": VERDICT_AI, "fake_probability": 0.97,
                           "confidence": 95.0},
        audio={"bucket": "b", "key": "uploads/scr_abc.mp3", "sha256": "d0"},
        meta={"filename": "track.mp3", "owner": None},
        ttl_seconds=3600)
    return mongo


@pytest.fixture()
def ready(monkeypatch):
    """A loaded backbone.

    The test environment sets LABS_EAGER_LOAD=false, so without this every
    appeal answers 503 model_loading — which is correct behaviour and is
    tested in TestReadiness, but would otherwise mask everything else.
    """
    from labs.api.v1 import escalations

    class _Ready:
        is_ready = True

    monkeypatch.setattr(escalations, "get_detector", lambda s=None: _Ready())


@pytest.fixture()
def no_deep(monkeypatch, ready):
    """Stop the appeal actually running a 60 s analysis."""
    from labs.api.v1 import escalations

    monkeypatch.setattr(escalations, "_materialise",
                        lambda doc, file, rid: "/tmp/fake-audio.mp3")
    monkeypatch.setattr(escalations, "_dispatch",
                        lambda *a, **kw: "job-xyz")
    monkeypatch.setattr(escalations, "_unlink", lambda p: None)


class TestHappyPath:
    def test_accepts_and_returns_a_job(self, client, retained, no_deep, deep_allowed):
        r = client.post("/v1/escalations",
                        data={"screen_id": "scr_abc",
                              "reason": "this is my own track"})
        assert r.status_code == 202, r.text

        body = r.json()
        assert body["id"] == "job-xyz"
        assert body["screen_id"] == "scr_abc"
        assert body["level_1_verdict"] == VERDICT_AI
        assert body["poll_url"] == "/v1/analyses/job-xyz"

    def test_records_the_appeal_as_pending(self, client, retained, no_deep, deep_allowed):
        """The record has to exist before the deep model answers, or an
        analysis that finishes fast has nothing to write its outcome onto."""
        client.post("/v1/escalations",
                    data={"screen_id": "scr_abc", "reason": "mine"})

        doc = retained.db().feedback.find_one({"screen_id": "scr_abc"})
        assert doc["kind"] == "escalation"
        assert doc["outcome"] == "pending"
        assert doc["reason"] == "mine"
        assert doc["level_1"]["verdict"] == VERDICT_AI
        assert doc["analysis_id"] == "job-xyz"

    def test_marks_the_screen_escalated(self, client, retained, no_deep, deep_allowed):
        client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert retained.get_screen("scr_abc")["escalated"] is True

    def test_a_reason_is_optional(self, client, retained, no_deep, deep_allowed):
        """Disagreeing should not require writing an essay."""
        assert client.post(
            "/v1/escalations", data={"screen_id": "scr_abc"}).status_code == 202


class TestIdempotency:
    def test_a_second_appeal_returns_the_first_analysis(self, client, retained,
                                                        no_deep, deep_allowed):
        """A double-clicked "I disagree" button must not start a second 60 s
        analysis of the same track."""
        first = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert first.status_code == 202

        second = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert second.json()["id"] == first.json()["id"]

    def test_only_one_feedback_row_is_written(self, client, retained, no_deep, deep_allowed):
        client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert retained.db().feedback.count_documents(
            {"screen_id": "scr_abc"}) == 1


class TestFailureModes:
    def test_unknown_screen_is_404(self, client, mongo, no_deep, deep_allowed):
        r = client.post("/v1/escalations", data={"screen_id": "scr_nope"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "screen_not_found"

    def test_the_404_message_points_somewhere_useful(self, client, mongo, deep_allowed, ready):
        """An expired window is the common case, so the error has to say what
        to do instead rather than just refusing."""
        r = client.post("/v1/escalations", data={"screen_id": "scr_nope"})
        assert "/v1/analyses" in r.json()["error"]["message"]

    def test_another_owners_screen_is_404_not_403(self, client, mongo,
                                                  deep_allowed, ready,
                                                  monkeypatch):
        """403 would confirm the id exists, which is free information about
        somebody else's traffic. The ownership branch answers 404."""
        from labs.api.v1 import escalations
        from labs.core.security import Principal

        mongo.save_screen("scr_theirs", result={"verdict": VERDICT_AI},
                          audio=None, meta={"owner": "someone-else"},
                          ttl_seconds=600)

        # An authenticated caller who is not the owner.
        me = Principal(id="me", name="me", scopes=("deep", "read"))
        client.app.dependency_overrides[
            escalations.require_scope] = lambda *a, **kw: (lambda: me)

        from labs.api.deps import principal as principal_dep

        client.app.dependency_overrides[principal_dep] = lambda: me
        try:
            r = client.post("/v1/escalations", data={"screen_id": "scr_theirs"})
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "screen_not_found"
        finally:
            client.app.dependency_overrides.clear()

    def test_unretrievable_audio_is_410(self, client, mongo, deep_allowed, ready):
        """The window expired between the screen and the appeal. 410 Gone is
        the honest code: it existed, it does not now."""
        mongo.save_screen("scr_noaudio", result={"verdict": VERDICT_AI},
                          audio=None, meta={}, ttl_seconds=600)

        r = client.post("/v1/escalations", data={"screen_id": "scr_noaudio"})
        assert r.status_code == 410
        assert r.json()["error"]["code"] == "audio_unavailable"

    def test_without_persistence_it_says_so(self, client, monkeypatch, deep_allowed, ready):
        """Appeals need shared state. Refusing clearly beats a 500."""
        from labs.services import storage

        class _Off:
            enabled = False

        monkeypatch.setattr(storage, "get_mongo", lambda cfg=None: _Off())
        r = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "appeals_unavailable"

    def test_missing_screen_id_is_422(self, client, deep_allowed, ready):
        assert client.post("/v1/escalations").status_code == 422


class TestScope:
    def test_appeals_need_the_deep_scope(self, monkeypatch):
        """The appeal runs the deep model, so it costs what the deep model
        costs. Anonymous callers cannot spend that."""
        from labs.core.security import ANONYMOUS

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        assert not ANONYMOUS.can("deep")

    def test_anonymous_is_refused(self, client, retained, monkeypatch):
        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        r = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert r.status_code == 403


class TestSummaryEndpoint:
    def test_requires_admin(self, client, mongo):
        """An overturn rate measures the detector's error rate. That is not an
        ordinary caller's to read."""
        r = client.get("/v1/feedback/summary")
        assert r.status_code in (401, 403)


class TestReadiness:
    def test_an_unloaded_backbone_is_refused_up_front(self, client, retained,
                                                      deep_allowed, monkeypatch):
        """REGRESSION.

        Without this the appeal returned 202 and then failed asynchronously
        with "Detector is not loaded" — which is the worst possible answer to
        somebody disputing a verdict, because it looks like the appeal was
        accepted and then quietly lost.
        """
        from labs.api.v1 import escalations

        class _NotReady:
            is_ready = False

        monkeypatch.setattr(escalations, "get_detector", lambda s=None: _NotReady())

        r = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "model_loading"

    def test_a_screen_only_image_says_so(self, client, retained, deep_allowed,
                                         monkeypatch):
        from labs.api.v1 import escalations

        monkeypatch.setattr(escalations, "get_detector", lambda s=None: None)

        r = client.post("/v1/escalations", data={"screen_id": "scr_abc"})
        assert r.status_code == 501
        assert r.json()["error"]["code"] == "deep_tier_unavailable"

    def test_a_ready_detector_is_accepted(self, client, retained, no_deep,
                                          deep_allowed, monkeypatch):
        from labs.api.v1 import escalations

        class _Ready:
            is_ready = True

        monkeypatch.setattr(escalations, "get_detector", lambda s=None: _Ready())
        assert client.post(
            "/v1/escalations", data={"screen_id": "scr_abc"}).status_code == 202
