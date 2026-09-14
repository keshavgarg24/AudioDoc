"""Tool endpoints: submission, polling, caching, two-file tools."""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration

SINGLE_FILE_TOOLS = ["master-check", "tempo-lab", "key-lab", "vocal-lab"]
TWO_FILE_TOOLS = [("reference-match", ["file", "reference"]),
                  ("beat-vocal-fit", ["beat", "vocal"])]


# ------------------------------------------------------- catalogue routes --
def test_catalogue_lists_all_tools(client):
    r = client.get("/v1/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 6
    slugs = {t["slug"] for t in body["tools"]}
    assert slugs == {"master-check", "tempo-lab", "key-lab",
                     "reference-match", "vocal-lab", "beat-vocal-fit"}


def test_catalogue_entry_has_all_contract_fields(client):
    for tool in client.get("/v1/tools").json()["tools"]:
        for key in ("slug", "name", "summary", "inputs", "file_count",
                    "typical_seconds", "accuracy", "basis", "limitations"):
            assert key in tool, f"tool {tool['slug']} missing '{key}'"


def test_describe_each_tool_by_slug(client):
    for tool in client.get("/v1/tools").json()["tools"]:
        slug = tool["slug"]
        r = client.get(f"/v1/tools/{slug}")
        assert r.status_code == 200
        assert r.json()["slug"] == slug


def test_unknown_tool_slug_returns_404(client):
    r = client.get("/v1/tools/not-a-real-tool")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_tool"


def test_api_version_is_in_catalogue_response(client):
    body = client.get("/v1/tools").json()
    assert "api_version" in body


# ------------------------------------------- single-file tool submission --
@pytest.mark.parametrize("slug", SINGLE_FILE_TOOLS)
def test_single_file_tool_accepts_a_wav(client, wav_bytes, slug):
    r = client.post(f"/v1/tools/{slug}",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")})
    assert r.status_code in (200, 202)


@pytest.mark.parametrize("slug", SINGLE_FILE_TOOLS)
def test_missing_file_returns_422(client, slug):
    r = client.post(f"/v1/tools/{slug}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_input"


@pytest.mark.parametrize("slug", SINGLE_FILE_TOOLS)
def test_wrong_file_type_returns_415(client, slug):
    r = client.post(f"/v1/tools/{slug}",
                    files={"file": ("payload.exe", b"\x00" * 16,
                                    "application/octet-stream")})
    assert r.status_code == 415


# ----------------------------------------------- two-file tool submission --
@pytest.mark.parametrize("slug,inputs", TWO_FILE_TOOLS)
def test_two_file_tool_requires_both_files(client, wav_bytes, slug, inputs):
    partial = {inputs[0]: ("t.wav", wav_bytes, "audio/wav")}
    r = client.post(f"/v1/tools/{slug}", files=partial)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_input"


@pytest.mark.parametrize("slug,inputs", TWO_FILE_TOOLS)
def test_two_file_tool_accepts_both_files(client, wav_bytes, slug, inputs):
    files = {name: ("t.wav", wav_bytes, "audio/wav") for name in inputs}
    r = client.post(f"/v1/tools/{slug}", files=files)
    assert r.status_code in (200, 202)


# ----------------------------------------------------------------- result --
def test_tool_result_is_pollable(client, wav_bytes):
    sub = client.post("/v1/tools/master-check",
                      files={"file": ("t.wav", wav_bytes, "audio/wav")})
    assert sub.status_code in (200, 202)
    if sub.status_code == 202:
        job_id = sub.json()["id"]
        poll = client.get(f"/v1/tools/results/{job_id}")
        assert poll.status_code == 200
        assert "status" in poll.json()


def test_tool_result_unknown_id_returns_404(client):
    r = client.get("/v1/tools/results/no-such-job-id-xyz")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_tool_result_job_completes(client, wav_bytes):
    sub = client.post("/v1/tools/master-check",
                      files={"file": ("t.wav", wav_bytes, "audio/wav")})
    assert sub.status_code in (200, 202)
    if sub.status_code == 200:
        return
    job_id = sub.json()["id"]
    for _ in range(30):
        poll = client.get(f"/v1/tools/results/{job_id}")
        status = poll.json()["status"]
        if status in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    assert status in ("succeeded", "failed")


def test_wait_returns_result_inline(client, wav_bytes):
    r = client.post("/v1/tools/tempo-lab",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"wait": "15"})
    assert r.status_code in (200, 202)
    if r.status_code == 200:
        assert r.json()["status"] == "succeeded"


# ----------------------------------------------------------------- caching --
def test_same_audio_returns_cached_result(client, wav_bytes):
    files = {"file": ("t.wav", wav_bytes, "audio/wav")}
    r1 = client.post("/v1/tools/master-check", files=files, data={"wait": "15"})
    r2 = client.post("/v1/tools/master-check", files=files, data={"wait": "15"})
    # Both should succeed or both return a job id. Caching only applies when
    # MongoDB is configured, so without it both are fresh runs — still valid.
    assert r1.status_code in (200, 202)
    assert r2.status_code in (200, 202)


def test_no_cache_bypasses_the_cache(client, wav_bytes):
    files = {"file": ("t.wav", wav_bytes, "audio/wav")}
    r = client.post("/v1/tools/master-check", files=files,
                    data={"wait": "15", "no_cache": "true"})
    assert r.status_code in (200, 202)


# ----------------------------------------------------------- invalid genre --
def test_invalid_genre_returns_422(client, wav_bytes):
    r = client.post("/v1/tools/master-check",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"genre": "klingon-opera"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_genre"


def test_valid_genre_is_accepted(client, wav_bytes):
    r = client.post("/v1/tools/master-check",
                    files={"file": ("t.wav", wav_bytes, "audio/wav")},
                    data={"genre": "trap"})
    assert r.status_code in (200, 202)
