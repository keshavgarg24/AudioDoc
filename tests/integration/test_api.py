"""The HTTP surface, exercised through the real ASGI app."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ----------------------------------------------------------------- health ---
def test_health_is_open_and_always_answers(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ready" in body["model"]


def test_unversioned_health_mirrors_the_versioned_one(client):
    """Load balancers are configured once and outlive an API version."""
    assert client.get("/health").json() == client.get("/v1/health").json()


def test_health_does_not_disclose_infrastructure(client):
    """This endpoint is unauthenticated. It must not describe how the
    detector is built or where its artifacts live."""
    body = client.get("/v1/health").json()
    for leaked in ("artifacts", "storage", "verification", "revision",
                   "stage1_source", "stage2_source"):
        assert leaked not in body
    assert "error" not in body["model"]

    flat = str(body).lower()
    for term in ("s3://", "bucket", "mongodb://", "huggingface", "/models"):
        assert term not in flat


def test_diagnostics_requires_admin_scope(client, monkeypatch):
    from labs.core import security
    from labs.core.config import get_settings

    settings = get_settings()
    object.__setattr__(settings.server, "require_auth", True)
    object.__setattr__(settings.server, "api_keys", ("analyst-key",))

    monkeypatch.setattr(
        security, "authenticate",
        lambda raw, settings=None: (
            security.Principal(id="1", name="analyst", scopes=("analyze", "read"),
                               fingerprint="labs_live_aaaaaa..."), None))

    r = client.get("/v1/diagnostics", headers={"X-API-Key": "analyst-key"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_readiness_reports_503_until_weights_are_resident(client):
    r = client.get("/v1/ready")
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert r.json()["ready"] is False
        assert r.json()["error"]["code"] == "model_loading"


# ------------------------------------------------------------- catalogue ---
def test_tool_catalogue_is_served(client):
    r = client.get("/v1/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 6
    assert len(body["tools"]) == 6


def test_each_catalogue_entry_carries_its_claims(client):
    for tool in client.get("/v1/tools").json()["tools"]:
        assert tool["accuracy"], f"{tool['slug']} has no stated accuracy"
        assert tool["limitations"], f"{tool['slug']} has no stated limitations"


def test_single_tool_can_be_described(client):
    r = client.get("/v1/tools/master-check")
    assert r.status_code == 200
    assert r.json()["slug"] == "master-check"


def test_unknown_tool_is_a_clean_404(client):
    r = client.get("/v1/tools/not-a-tool")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_tool"


def test_genres_are_served(client):
    r = client.get("/v1/genres")
    assert r.status_code == 200
    assert r.json()["count"] > 0


# ---------------------------------------------------------------- errors ---
def test_every_error_uses_the_shared_envelope(client):
    r = client.get("/v1/tools/not-a-tool")
    body = r.json()
    assert set(body) == {"error"}
    assert set(body["error"]) >= {"code", "message"}


def test_missing_file_is_a_422_naming_the_field(client):
    r = client.post("/v1/analyses", data={"mode": "ai"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_invalid_mode_is_refused(client, wav_bytes):
    r = client.post(
        "/v1/analyses",
        files={"file": ("t.wav", wav_bytes, "audio/wav")},
        data={"mode": "telepathy"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_mode"


def test_invalid_verify_policy_is_refused(client, wav_bytes):
    r = client.post(
        "/v1/analyses",
        files={"file": ("t.wav", wav_bytes, "audio/wav")},
        data={"mode": "audio", "verify": "sometimes"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_verify_policy"


def test_unsupported_extension_is_refused(client, wav_bytes):
    r = client.post(
        "/v1/analyses",
        files={"file": ("payload.exe", wav_bytes, "application/octet-stream")},
        data={"mode": "audio"})
    assert r.status_code == 415


def test_file_that_is_not_audio_is_refused(client):
    r = client.post(
        "/v1/analyses",
        files={"file": ("t.wav", b"PK\x03\x04" + b"\x00" * 256, "audio/wav")},
        data={"mode": "audio"})
    assert r.status_code == 415


def test_webhook_to_a_private_address_is_refused_at_submission(client, wav_bytes):
    """Rejected when the caller can still act on it, not silently at delivery."""
    r = client.post(
        "/v1/analyses",
        files={"file": ("t.wav", wav_bytes, "audio/wav")},
        data={"mode": "audio",
              "webhook_url": "https://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "webhook_destination_not_allowed"


def test_unknown_result_id_is_a_clean_404(client):
    r = client.get("/v1/tools/results/deadbeef")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------- headers ---
def test_every_response_carries_a_request_id(client):
    r = client.get("/v1/health")
    assert r.headers["X-Request-Id"]
    assert r.headers["X-API-Version"]
    assert "X-Response-Time-ms" in r.headers


def test_caller_supplied_request_id_is_echoed(client):
    r = client.get("/v1/health", headers={"X-Request-Id": "trace-me-123"})
    assert r.headers["X-Request-Id"] == "trace-me-123"


def test_errors_also_carry_the_request_id(client):
    r = client.get("/v1/tools/not-a-tool", headers={"X-Request-Id": "err-1"})
    assert r.headers["X-Request-Id"] == "err-1"


# --------------------------------------------------------------- openapi ---
def test_openapi_document_is_valid_and_complete(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "LABS API"
    for path in ("/v1/analyses", "/v1/tools", "/v1/health"):
        assert path in spec["paths"]


def test_no_retired_surface_is_advertised(client):
    """Every documented path is versioned; /health is the one deliberate
    exception and is excluded from the schema."""
    spec = client.get("/openapi.json").json()
    assert all(p.startswith("/v1/") for p in spec["paths"])
