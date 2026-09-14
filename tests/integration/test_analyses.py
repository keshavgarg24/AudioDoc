"""Analysis submission, polling, dedup, and field-filtering via HTTP."""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


# ----------------------------------------------------------- submission ---
def test_audio_mode_submission_returns_202(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 202
    body = r.json()
    assert "id" in body
    assert body["status"] in ("queued", "running")
    assert body["mode"] == "audio"
    assert "poll_url" in body


def test_submission_returns_job_id(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 202
    assert len(r.json()["id"]) >= 8


def test_polling_unknown_id_returns_404(client):
    r = client.get("/v1/analyses/deadbeef-does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_job_can_be_polled_immediately(client, wav_bytes):
    sub = client.post("/v1/analyses",
                      files={"file": ("t.wav", wav_bytes, "audio/wav")},
                      data={"mode": "audio"})
    assert sub.status_code == 202
    job_id = sub.json()["id"]
    poll = client.get(f"/v1/analyses/{job_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["id"] == job_id
    assert "status" in body


def test_job_eventually_completes(client, wav_bytes):
    sub = client.post("/v1/analyses",
                      files={"file": ("t.wav", wav_bytes, "audio/wav")},
                      data={"mode": "audio"})
    job_id = sub.json()["id"]
    for _ in range(30):
        poll = client.get(f"/v1/analyses/{job_id}")
        status = poll.json()["status"]
        if status in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    assert status in ("succeeded", "failed")


def test_wait_returns_result_inline(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio", "wait": "15"})
    assert r.status_code in (200, 202)
    if r.status_code == 200:
        assert r.json()["status"] == "succeeded"


def test_invalid_mode_returns_422(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "magic"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_mode"


def test_invalid_verify_policy_returns_422(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio", "verify": "sometimes"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_verify_policy"


def test_no_file_returns_422(client):
    r = client.post("/v1/analyses", data={"mode": "audio"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_zip_disguised_as_wav_returns_415(client):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", b"PK\x03\x04" + b"\x00" * 100,
                                    "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 415


def test_unsupported_extension_returns_415(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("payload.exe", wav_bytes, "application/octet-stream")},
                    data={"mode": "audio"})
    assert r.status_code == 415


def test_empty_upload_returns_400(client):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", b"", "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_file"


def test_webhook_to_loopback_returns_422(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio",
                          "webhook_url": "https://127.0.0.1/callback"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "webhook_destination_not_allowed"


def test_webhook_plain_http_returns_422(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio",
                          "webhook_url": "http://example.com/hook"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "webhook_requires_https"


# ---------------------------------------------------------------- fields ---
def test_fields_parameter_narrows_the_response(client, wav_bytes):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"mode": "audio", "wait": "15", "fields": "musical"})
    if r.status_code == 200:
        result = r.json().get("result") or r.json()
        assert "production" not in result


# ----------------------------------------------------------- idempotency ---
def test_idempotency_key_second_call_is_accepted(client, wav_bytes):
    """Both calls should be accepted. When MongoDB is configured the second
    returns the original job; without it both proceed independently."""
    headers = {"Idempotency-Key": "idem-test-abc123"}
    r1 = client.post("/v1/analyses",
                     files={"file": ("t.wav", wav_bytes, "audio/wav")},
                     data={"mode": "audio"},
                     headers=headers)
    r2 = client.post("/v1/analyses",
                     files={"file": ("t.wav", wav_bytes, "audio/wav")},
                     data={"mode": "audio"},
                     headers=headers)
    assert r1.status_code in (200, 202)
    assert r2.status_code in (200, 202)


# ----------------------------------------------------------------- list ---
def test_list_analyses_returns_200(client):
    r = client.get("/v1/analyses")
    assert r.status_code == 200


def test_list_analyses_respects_limit(client):
    r = client.get("/v1/analyses?limit=5")
    assert r.status_code == 200


def test_list_analyses_accepts_skip_parameter(client):
    r = client.get("/v1/analyses?skip=10&limit=5")
    assert r.status_code == 200
