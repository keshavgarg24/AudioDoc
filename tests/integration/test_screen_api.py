"""POST /v1/screen, and the free/paid boundary around it.

The auth tests here are the ones to be most careful about: they pin that the
free tier is reachable without a paid scope AND that holding the free scope
never grants the expensive one. Getting the second wrong gives the billable
work away, and it would not show up as a failure anywhere else.
"""
from __future__ import annotations

import dataclasses

import pytest

from tests.conftest import requires_screen_models


@pytest.fixture()
def tone_upload(tone_wav_bytes):
    return {"file": ("tone.wav", tone_wav_bytes, "audio/wav")}


class TestScopes:
    """Exercised through the real security model, not a mock."""

    def test_anonymous_holds_screen_but_not_deep(self, monkeypatch):
        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        from labs.core.security import ANONYMOUS

        assert ANONYMOUS.can("screen")
        assert not ANONYMOUS.can("deep")

    def test_screen_scope_never_implies_deep(self, monkeypatch):
        """THE test for the tier split. A free-tier key that inherited the
        expensive tier would give away the billable work silently."""
        from labs.core.security import Principal

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        free = Principal(id="k", name="free", scopes=("screen", "read"))
        assert free.can("screen")
        assert not free.can("deep")

    def test_analyze_grants_the_screen_but_not_the_backbone(self, monkeypatch):
        """A key that may submit an analysis may certainly submit the cheap
        version of one, so "analyze" implies "screen".

        It does NOT imply "deep". That is a deliberate breaking change: keys
        issued before the split have to be granted the scope explicitly, and
        the alternative - having them inherit it - gives the billable tier away
        to every key already in the wild.
        """
        from labs.core.security import Principal

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        legacy = Principal(id="k", name="legacy", scopes=("analyze", "read"))
        assert legacy.can("screen")
        assert not legacy.can("deep")

    def test_the_gate_can_be_opened_deployment_wide(self, monkeypatch):
        """The escape hatch for local development and for a deployment already
        behind its own gateway. Explicit, and not per-key, so it cannot be
        granted to one caller by accident."""
        from labs.core.security import Principal

        legacy = Principal(id="k", name="legacy", scopes=("analyze", "read"))

        monkeypatch.setenv("LABS_GATE_DEEP", "0")
        assert legacy.can("deep")

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        assert not legacy.can("deep")

    def test_anonymous_keeps_every_pre_existing_route(self, monkeypatch):
        """The tools and audio routes gate on "analyze". Removing it from
        anonymous broke 23 unrelated tests on the first attempt at this, which
        is the whole reason the deep gate is a separate scope rather than a
        redefinition of an existing one."""
        from labs.core.security import ANONYMOUS

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        assert ANONYMOUS.can("analyze")
        assert ANONYMOUS.can("read")
        assert ANONYMOUS.can("screen")
        assert not ANONYMOUS.can("deep")

    def test_admin_still_holds_everything(self):
        from labs.core.security import Principal

        admin = Principal(id="k", name="admin", scopes=("admin",))
        assert admin.can("screen")
        assert admin.can("deep")

    def test_deep_scope_does_not_imply_screen_by_accident(self, monkeypatch):
        """It should, via the explicit grant below - but only because "screen"
        is listed, never as a side effect of ordering."""
        from labs.core.security import Principal

        monkeypatch.setenv("LABS_GATE_DEEP", "1")
        deep_only = Principal(id="k", name="deep", scopes=("deep",))
        assert deep_only.can("deep")
        assert not deep_only.can("screen")


class TestValidation:
    def test_rejects_an_unsupported_extension(self, client):
        r = client.post("/v1/screen",
                        files={"file": ("x.txt", b"not audio", "text/plain")})
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "unsupported_media_type"

    def test_rejects_an_empty_file(self, client):
        r = client.post("/v1/screen",
                        files={"file": ("x.wav", b"", "audio/wav")})
        assert r.status_code in (400, 422)

    def test_rejects_a_non_audio_payload_with_an_audio_name(self, client):
        """An ungated endpoint is the one a stranger reaches first, so the
        magic-byte check has to hold and the error must not be a traceback."""
        r = client.post("/v1/screen",
                        files={"file": ("x.wav", b"\x00" * 4096, "audio/wav")})
        # 415: the upload stager checks magic bytes, so a .wav name over a
        # non-RIFF body is refused on content before anything decodes it.
        assert r.status_code == 415
        assert "error" in r.json()

    def test_missing_file_is_a_422(self, client):
        assert client.post("/v1/screen").status_code == 422


class TestDisabled:
    def test_returns_503_when_the_tier_is_off(self, client, monkeypatch,
                                              tone_upload):
        from labs.api.v1 import screen as screen_api

        off = dataclasses.replace(
            screen_api.settings,
            screen=dataclasses.replace(screen_api.settings.screen,
                                       enabled=False))
        monkeypatch.setattr(screen_api, "settings", off)

        r = client.post("/v1/screen", files=tone_upload)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "screen_disabled"


class TestAdmissionControl:
    def test_sheds_load_rather_than_queueing(self, client, monkeypatch,
                                             tone_upload):
        """Level 1 is CPU-bound and FastAPI would run it on a 40-thread pool.
        Past the core count that does not add throughput, it just turns a
        ~1.5 s promise into a minute, so a saturated instance must say so.
        """
        import threading

        from labs.api.v1 import screen as screen_api

        # Exhausted semaphore == every slot in use.
        monkeypatch.setattr(screen_api, "_SLOTS",
                            threading.BoundedSemaphore(1))
        screen_api._SLOTS.acquire()

        r = client.post("/v1/screen", files=tone_upload)
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "screen_busy"

    def test_the_slot_is_released_after_a_failure(self, client, monkeypatch):
        """A leaked permit would take the instance permanently out of service
        one bad upload at a time."""
        from labs.api.v1 import screen as screen_api

        before = screen_api._SLOTS._value
        client.post("/v1/screen",
                    files={"file": ("x.wav", b"\x00" * 64, "audio/wav")})
        assert screen_api._SLOTS._value == before


@requires_screen_models
class TestEndToEnd:
    def test_returns_a_verdict_synchronously(self, client, tone_upload):
        """No job id, no polling: the answer is in the response.

        This is the property that makes the tier usable from a frontend in one
        round trip, and it is why this endpoint is not async like the others.
        """
        r = client.post("/v1/screen", files=tone_upload)
        assert r.status_code == 200, r.text

        body = r.json()
        assert body["tier"] == "screen"
        assert body["levels_run"] == ["level_1_screen"]
        assert body["verdict"] in ("ai-generated", "human-made",
                                   "inconclusive", "unavailable")
        assert body["next_step"] in ("return", "escalate")
        assert "id" not in body
        assert "poll_url" not in body

    def test_reports_both_models_and_the_fusion(self, client, tone_upload):
        body = client.post("/v1/screen", files=tone_upload).json()
        level_1 = body["detection"]["level_1"]

        assert set(level_1["models"]) == {"fakeprint", "cepstrum"}
        assert level_1["ensemble"]["agreement"] in (
            "agree-ai", "agree-human", "disagree", "single")
        # The interpretation is the part a human reviewer reads.
        assert level_1["ensemble"]["note"]

    def test_does_not_name_level_2(self, client, tone_upload):
        """Level 1 must not imply it ran the backbone."""
        body = client.post("/v1/screen", files=tone_upload).json()
        assert body["detection"]["level_2"] is None

    def test_echoes_the_caller_reference(self, client, tone_wav_bytes):
        r = client.post("/v1/screen",
                        files={"file": ("t.wav", tone_wav_bytes, "audio/wav")},
                        data={"reference": "track-42"})
        assert r.json()["reference"] == "track-42"

    def test_carries_a_caveat_on_a_human_verdict(self, client, tone_upload):
        """A Level-1 "human-made" is not an exoneration and the response has
        to say so, because a caller will otherwise read it as one."""
        body = client.post("/v1/screen", files=tone_upload).json()
        if body["verdict"] == "human-made":
            assert "never seen" in body["caveat"]

    def test_reports_its_own_cost(self, client, tone_upload):
        body = client.post("/v1/screen", files=tone_upload).json()
        assert body["elapsed_seconds"] > 0
        assert body["detection"]["level_1"]["timings"]


@requires_screen_models
class TestHealth:
    def test_health_reports_screen_readiness_separately(self, client):
        """The two tiers fail independently: Level 1 can serve while the
        backbone is still loading, which is a cold container's first 12 s."""
        body = client.get("/v1/health").json()
        assert body["screen"]["enabled"] is True
        assert body["screen"]["ready"] is True
        assert set(body["screen"]["models"]) == {"fakeprint", "cepstrum"}

    def test_screen_mode_is_advertised(self, client):
        assert "screen" in client.get("/v1/health").json()["modes"]

    def test_ready_passes_on_screen_alone_when_not_eager(self, client):
        """A screen-only worker must not be held out of its target group
        waiting for a backbone it will never load."""
        body = client.get("/v1/ready").json()
        # conftest sets LABS_EAGER_LOAD=false.
        assert body["ready"] is True
        assert body["tiers"] == ["screen"]
