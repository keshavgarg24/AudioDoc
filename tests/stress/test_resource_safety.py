"""Regressions for resource leaks found by audit.

Each test here failed before the fix it guards. They are cheap and they cover
the failure modes that only appear after hours of production traffic, which is
exactly the class of bug a test suite is otherwise blind to.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_wav

pytestmark = pytest.mark.stress

WAV = make_wav()


# ------------------------------------------------------------ orphan jobs ---
def test_rejected_submission_leaves_no_orphan_job(client):
    """A 429 must not leave a `queued` job behind.

    `_evict` only reclaims terminal jobs, so an abandoned `queued` record is
    never collected: it leaks a slot, inflates the queue depth reported by
    /health, and lets the store grow past max_jobs without bound.
    """
    from labs.core.security import get_limiter
    from labs.services.jobs import get_store

    store = get_store()
    before = store.stats()["total"]

    limiter = get_limiter()
    for _ in range(4):
        limiter.acquire("-", 4)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "too_many_inflight"

    assert store.stats()["total"] == before, "a rejected submission created a job"
    stuck = [j for j in store._jobs.values() if j.status == "queued"]
    assert not stuck, f"{len(stuck)} orphaned queued job(s) left behind"


def test_rejected_tool_submission_leaves_no_orphan_job(client):
    from labs.core.security import get_limiter
    from labs.services.jobs import get_store

    store = get_store()
    before = store.stats()["total"]

    limiter = get_limiter()
    for _ in range(4):
        limiter.acquire("-", 4)

    r = client.post("/v1/tools/tempo-lab",
                    files={"file": ("t.wav", WAV, "audio/wav")})
    assert r.status_code == 429
    assert store.stats()["total"] == before


def test_abandoned_queued_jobs_are_eventually_evicted():
    """The backstop: a job that never started is reclaimed after the TTL."""
    import time

    from labs.services.jobs import JobStore

    store = JobStore(max_jobs=10, ttl_seconds=0.01)
    job = store.create(meta={"mode": "audio"})
    assert store.get(job.id) is not None

    time.sleep(0.05)
    store.create(meta={"mode": "audio"})       # any create triggers eviction
    assert store.get(job.id) is None, "abandoned queued job was never reclaimed"


def test_discard_removes_a_job(client):
    from labs.services.jobs import get_store

    store = get_store()
    job = store.create(meta={"mode": "audio"})
    assert store.get(job.id) is not None
    store.discard(job.id)
    assert store.get(job.id) is None


# ------------------------------------------------------- concurrency slot ---
def test_inflight_slot_is_released_when_the_queue_refuses_work(client,
                                                               monkeypatch):
    """If the pool will not take the job, the slot must not leak.

    `cleanup` is only wired into the running task, so on this path nothing
    else releases it. A leak here permanently locks the key out at its
    concurrency limit until the process restarts.
    """
    from labs.core.security import get_limiter
    from labs.services import jobs as jobs_mod

    runner = jobs_mod.get_runner()

    def refuse(*a, **kw):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(runner, "submit", refuse)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"

    inflight = get_limiter().snapshot()["inflight"]
    assert not inflight.get("-"), f"concurrency slot leaked: {inflight}"


def test_staged_upload_is_removed_when_the_queue_refuses_work(client,
                                                              monkeypatch):
    import glob
    import os
    import tempfile

    from labs.core.uploads import STAGE_PREFIX
    from labs.services import jobs as jobs_mod

    pattern = os.path.join(tempfile.gettempdir(), f"{STAGE_PREFIX}*")
    before = set(glob.glob(pattern))

    runner = jobs_mod.get_runner()
    monkeypatch.setattr(runner, "submit",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no")))

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 503
    leaked = set(glob.glob(pattern)) - before
    assert not leaked, f"staged upload left on disk: {leaked}"


# ----------------------------------------------------------- rate limiter ---
def test_limiter_prunes_idle_buckets():
    """Unbounded bucket growth is a slow leak in a long-lived process."""
    from labs.core.security import RateLimiter

    limiter = RateLimiter(per_minute=60)
    for i in range(RateLimiter._PRUNE_ABOVE + 500):
        limiter.check(f"key-{i}")

    tracked = limiter.snapshot()["tracked_keys"]
    assert tracked <= RateLimiter._PRUNE_ABOVE + 500

    # Age every bucket out of the window, then force a sweep.
    import time as _t
    for q in limiter._hits.values():
        q.clear()
    limiter._hits["fresh"].append(_t.time())
    limiter.check("trigger-prune")
    assert limiter.snapshot()["tracked_keys"] < tracked, "idle buckets not pruned"


def test_limiter_never_prunes_a_bucket_with_work_in_flight():
    """Pruning an in-flight bucket would reset its concurrency accounting."""
    from labs.core.security import RateLimiter

    limiter = RateLimiter(per_minute=60)
    limiter.acquire("busy", 4)
    for i in range(RateLimiter._PRUNE_ABOVE + 50):
        limiter.check(f"k{i}")
    limiter._prune(__import__("time").time() + 120)
    assert limiter.snapshot()["inflight"].get("busy") == 1


# -------------------------------------------------------------- ownership ---
def test_a_key_cannot_read_another_keys_live_job(monkeypatch):
    """IDOR on the live path: the window that matters most."""
    from fastapi.testclient import TestClient

    import labs.api.deps as deps_mod
    from labs.application import app
    from labs.core.security import Principal

    def as_key(key_id):
        return Principal(id=key_id, name=key_id, scopes=("analyze", "read"),
                         source="database", fingerprint=f"labs_live_{key_id}")

    current = {"id": "alice"}
    monkeypatch.setattr(deps_mod, "authenticate",
                        lambda raw, settings=None: (as_key(current["id"]), None))
    c = TestClient(app)

    sub = c.post("/v1/analyses",
                 files={"file": ("t.wav", WAV, "audio/wav")},
                 data={"mode": "audio"})
    assert sub.status_code in (200, 202)
    if sub.status_code != 202:
        pytest.skip("submission did not queue")
    job_id = sub.json()["id"]

    assert c.get(f"/v1/analyses/{job_id}").status_code == 200   # owner

    current["id"] = "mallory"
    stolen = c.get(f"/v1/analyses/{job_id}")
    assert stolen.status_code == 404, "another key read this job"
    assert stolen.json()["error"]["code"] == "not_found"


def test_owner_is_not_leaked_in_the_response(monkeypatch):
    """The ownership id is internal and must not appear in a poll response."""
    from fastapi.testclient import TestClient

    import labs.api.deps as deps_mod
    from labs.application import app
    from labs.core.security import Principal

    monkeypatch.setattr(
        deps_mod, "authenticate",
        lambda raw, settings=None: (Principal(
            id="secret-key-id-123", name="k", scopes=("analyze", "read"),
            source="database", fingerprint="labs_live_abc..."), None))
    c = TestClient(app)

    sub = c.post("/v1/analyses",
                 files={"file": ("t.wav", WAV, "audio/wav")},
                 data={"mode": "audio"})
    if sub.status_code != 202:
        pytest.skip("submission did not queue")
    body = c.get(f"/v1/analyses/{sub.json()['id']}").text
    assert "secret-key-id-123" not in body


# --------------------------------------------------------- security headers --
def test_security_headers_are_present_on_every_response(client):
    for path in ("/health", "/v1/tools", "/v1/genres"):
        r = client.get(path)
        for header, expected in (("X-Content-Type-Options", "nosniff"),
                                 ("X-Frame-Options", "DENY"),
                                 ("Referrer-Policy", "no-referrer")):
            assert r.headers.get(header) == expected, \
                f"{path} missing {header}"


def test_security_headers_are_present_on_errors(client):
    r = client.get("/v1/analyses/nope")
    assert r.status_code == 404
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_hsts_is_off_by_default_and_on_when_configured(monkeypatch):
    from fastapi.testclient import TestClient

    from labs.application import create_app

    assert "Strict-Transport-Security" not in TestClient(
        create_app()).get("/health").headers

    monkeypatch.setenv("LABS_HSTS_SECONDS", "31536000")
    import labs.application as app_mod
    from labs.core.config import get_settings
    monkeypatch.setattr(app_mod, "settings", get_settings())
    hsts = TestClient(create_app()).get("/health").headers.get(
        "Strict-Transport-Security")
    assert hsts and "max-age=31536000" in hsts


def test_request_id_header_cannot_be_used_for_header_injection(client):
    r = client.get("/health", headers={"X-Request-Id": "abc"})
    assert r.headers["X-Request-Id"] == "abc"
    # An over-long id is truncated rather than reflected wholesale.
    r = client.get("/health", headers={"X-Request-Id": "z" * 500})
    assert len(r.headers["X-Request-Id"]) <= 128


# -------------------------------------------------------------- filenames ---
def test_windows_style_traversal_is_stripped_from_the_display_name(client):
    r = client.post("/v1/analyses",
                    files={"file": (r"..\..\..\windows\system32\sam.wav",
                                    WAV, "audio/wav")},
                    data={"mode": "audio"})
    if r.status_code not in (200, 202):
        pytest.skip(f"submission refused with {r.status_code}")
    name = r.json()["filename"]
    assert ".." not in name and "\\" not in name and "/" not in name, name
    assert name == "sam.wav"


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.wav", "passwd.wav"),
    (r"..\..\sam.wav", "sam.wav"),
    ("/abs/path/t.wav", "t.wav"),
    ("t\x00.wav", "t.wav"),
    ("...", "audio"),
    ("", "audio"),
    (None, "audio"),
])
def test_safe_filename_normalises(raw, expected):
    from labs.api.validate import safe_filename
    assert safe_filename(raw) == expected


def test_safe_filename_is_bounded():
    from labs.api.validate import MAX_FILENAME, safe_filename
    assert len(safe_filename("a" * 9000 + ".wav")) <= MAX_FILENAME


# ------------------------------------------------------------- pagination ---
@pytest.mark.parametrize("qs,expect", [
    ("limit=0", 422), ("limit=-1", 422), ("limit=201", 422),
    ("limit=999999", 422), ("skip=-1", 422),
    ("limit=200", 200), ("limit=1", 200), ("skip=0", 200),
])
def test_pagination_bounds_are_enforced(client, qs, expect):
    r = client.get(f"/v1/analyses?{qs}")
    assert r.status_code == expect, f"?{qs} -> {r.status_code}"
