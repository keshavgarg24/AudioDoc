"""Authentication and authorisation enforcement."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _make_principal(scopes=("analyze", "read")):
    from labs.core.security import Principal
    return Principal(id=None, name="test", fingerprint="labs_test_aaa...",
                     scopes=scopes, source="environment")


def _patch_auth(monkeypatch, fn):
    """Patch authenticate at the site deps.py reads it from."""
    import labs.api.deps as deps_mod
    monkeypatch.setattr(deps_mod, "authenticate", fn)


# --------------------------------------------------------------- open mode --
def test_open_deployment_allows_submission(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code in (200, 202)


def test_health_is_always_open(client):
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 200


def test_genres_are_always_open(client):
    assert client.get("/v1/genres").status_code == 200


def test_catalogue_is_always_open(client):
    assert client.get("/v1/tools").status_code == 200


# ------------------------------------------------------------------ closed --
def test_missing_key_returns_401_when_auth_enforced(monkeypatch, wav_bytes):
    from fastapi.testclient import TestClient

    from labs.application import app

    def strict_auth(raw, settings=None):
        if not raw:
            return None, "missing_api_key"
        return None, "invalid_api_key"

    _patch_auth(monkeypatch, strict_auth)
    client = TestClient(app)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "missing_api_key"


def test_wrong_key_returns_401(monkeypatch, wav_bytes):
    from fastapi.testclient import TestClient

    from labs.application import app

    def strict_auth(raw, settings=None):
        return None, "invalid_api_key"

    _patch_auth(monkeypatch, strict_auth)
    client = TestClient(app)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"},
                    headers={"X-API-Key": "bad-key"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_correct_key_is_accepted(monkeypatch, wav_bytes):
    from fastapi.testclient import TestClient

    from labs.application import app

    def good_auth(raw, settings=None):
        if raw == "good-key":
            return _make_principal(), None
        return None, "invalid_api_key" if raw else "missing_api_key"

    _patch_auth(monkeypatch, good_auth)
    client = TestClient(app)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"},
                    headers={"X-API-Key": "good-key"})
    assert r.status_code in (200, 202)


def test_health_stays_open_when_auth_enforced(monkeypatch):
    from fastapi.testclient import TestClient

    from labs.application import app

    _patch_auth(monkeypatch, lambda raw, settings=None: (None, "missing_api_key"))
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 200


# ------------------------------------------------------------------ scope ---
def test_read_only_key_cannot_submit(monkeypatch, wav_bytes):
    from fastapi.testclient import TestClient

    from labs.application import app

    _patch_auth(monkeypatch,
                lambda raw, settings=None: (_make_principal(scopes=("read",)), None))
    client = TestClient(app)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"},
                    headers={"X-API-Key": "read-key"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_read_only_key_can_read_catalogue(monkeypatch):
    from fastapi.testclient import TestClient

    from labs.application import app

    _patch_auth(monkeypatch,
                lambda raw, settings=None: (_make_principal(scopes=("read",)), None))
    client = TestClient(app)
    assert client.get("/v1/tools").status_code == 200


def test_forbidden_response_uses_error_envelope(monkeypatch, wav_bytes):
    from fastapi.testclient import TestClient

    from labs.application import app

    _patch_auth(monkeypatch,
                lambda raw, settings=None: (_make_principal(scopes=("read",)), None))
    client = TestClient(app)

    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"},
                    headers={"X-API-Key": "read-key"})
    assert r.status_code == 403
    assert "error" in r.json()
    assert r.json()["error"]["code"] == "forbidden"


# ------------------------------------------------------------------- scopes --
def test_admin_implies_all_scopes():
    from labs.core.security import Principal
    p = Principal(id="1", name="admin", scopes=("admin",), fingerprint="x")
    assert p.can("analyze")
    assert p.can("read")
    assert p.can("admin")


def test_analyze_does_not_imply_admin():
    from labs.core.security import Principal
    p = Principal(id="1", name="w", scopes=("analyze",), fingerprint="x")
    assert p.can("analyze")
    assert not p.can("admin")


def test_read_does_not_imply_analyze():
    from labs.core.security import Principal
    p = Principal(id="1", name="r", scopes=("read",), fingerprint="x")
    assert p.can("read")
    assert not p.can("analyze")
