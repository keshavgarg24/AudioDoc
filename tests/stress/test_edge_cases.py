"""Adversarial and edge-case probes.

These are the inputs a fuzzer, a confused client or an attacker sends. Every
test here asserts the service answers with a documented status code and the
standard error envelope rather than a stack trace, a hang, or a 500.

Run with:  pytest tests/stress -q
"""
from __future__ import annotations

import io
import struct
import threading

import pytest

from tests.conftest import make_wav

pytestmark = pytest.mark.stress

WAV = make_wav()

# Anything the service answers with must be one of these. A 500 means an
# unhandled exception reached the client.
DOCUMENTED = {200, 202, 400, 401, 403, 404, 413, 415, 422, 429, 503}


def _assert_documented(r, note=""):
    assert r.status_code in DOCUMENTED, (
        f"undocumented status {r.status_code} {note}: {r.text[:300]}")
    if r.status_code >= 400:
        body = r.json()
        assert "error" in body, f"error envelope missing {note}: {body}"
        assert "code" in body["error"], f"error.code missing {note}: {body}"


# ------------------------------------------------------------- filenames ---
@pytest.mark.parametrize("filename", [
    "../../../../etc/passwd.wav",
    "..\\..\\..\\windows\\system32\\config\\sam.wav",
    "/absolute/path/track.wav",
    "track\x00.wav",                     # null byte truncation
    "track\n\rHeader-Injection: yes.wav",
    "%2e%2e%2f%2e%2e%2ftrack.wav",
    "a" * 4000 + ".wav",                 # very long name
    "‮txt.wav",                     # right-to-left override
    "🎵🎶.wav",
    ".wav",
    "track.WAV",                         # case
    "track.wav.exe",
    "track.exe.wav",
])
def test_hostile_filenames_are_handled(client, filename):
    """No path traversal, no header injection, no crash."""
    r = client.post("/v1/analyses",
                    files={"file": (filename, WAV, "audio/wav")},
                    data={"mode": "audio"})
    _assert_documented(r, f"filename={filename!r}")
    # A traversal attempt must never be echoed back as an absolute path.
    if r.status_code in (200, 202):
        echoed = r.json().get("filename", "")
        assert not echoed.startswith("/"), f"absolute path echoed: {echoed}"
        assert ".." not in echoed, f"traversal echoed: {echoed}"


def test_no_filename_at_all(client):
    r = client.post("/v1/analyses",
                    files={"file": ("", WAV, "audio/wav")},
                    data={"mode": "audio"})
    _assert_documented(r)


# ----------------------------------------------------------- file content ---
@pytest.mark.parametrize("payload,label", [
    (b"", "empty"),
    (b"\x00", "single null"),
    (b"PK\x03\x04" + b"\x00" * 64, "zip"),
    (b"\x7fELF" + b"\x00" * 64, "elf binary"),
    (b"#!/bin/sh\nrm -rf /\n", "shell script"),
    (b"<?php system($_GET['c']); ?>", "php webshell"),
    (b"<svg onload=alert(1)>", "svg xss"),
    (b"%PDF-1.4\n", "pdf"),
    (b"RIFF", "truncated riff header"),
    (b"RIFF\xff\xff\xff\xffWAVE", "riff with bogus length"),
    (b"\xff\xfb" + b"\x00" * 8, "mp3 magic but no frames"),
])
def test_hostile_file_contents(client, payload, label):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", payload, "audio/wav")},
                    data={"mode": "audio"})
    _assert_documented(r, f"payload={label}")
    # Nothing that is not audio may be accepted for analysis.
    if label not in ("truncated riff header", "riff with bogus length",
                     "mp3 magic but no frames"):
        assert r.status_code in (400, 415), f"{label} was not rejected"


def test_declared_wav_that_is_actually_a_zip_is_refused(client):
    r = client.post("/v1/analyses",
                    files={"file": ("song.wav", b"PK\x03\x04" + b"\x00" * 200,
                                    "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_wav_header_claiming_enormous_size_does_not_allocate(client):
    """A RIFF header may claim 4 GB; we must not believe it."""
    header = struct.pack("<4sI4s", b"RIFF", 0xFFFFFFFF, b"WAVE")
    r = client.post("/v1/analyses",
                    files={"file": ("big.wav", header + b"\x00" * 1024,
                                    "audio/wav")},
                    data={"mode": "audio"})
    _assert_documented(r)


# ------------------------------------------------------------- parameters ---
@pytest.mark.parametrize("mode", [
    " ", "AI", "Ai", "ai ", "ai\n", "null", "None", "0", "-1",
    "ai;audio", "ai,audio", "../ai", "a" * 1000,
    "{'$ne': null}", '{"$gt": ""}',          # NoSQL injection shapes
    "'; DROP TABLE analyses;--",             # SQL injection shape
    "<script>alert(1)</script>",
])
def test_hostile_mode_values(client, mode):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": mode})
    assert r.status_code == 422, f"mode={mode!r} was not rejected"
    assert r.json()["error"]["code"] == "invalid_mode"


def test_empty_mode_falls_back_to_the_default(client):
    """`mode=` is treated as "not supplied", which is the documented default.

    Starlette hands an empty form value to FastAPI identically to an absent
    one, so the route cannot tell them apart to reject the first: by the time
    any application code runs, both are already the parameter default. This
    matches how HTML forms behave and is asserted here so the behaviour is
    deliberate rather than merely current.
    """
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": ""})
    _assert_documented(r)
    assert r.status_code != 422 or r.json()["error"]["code"] != "invalid_mode"


@pytest.mark.parametrize("genre", [
    "'; DROP TABLE x;--", "{'$ne': null}", "<script>x</script>",
    "../../etc", "a" * 5000, "\x00trap",
])
def test_hostile_genre_values(client, genre):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio", "genre": genre})
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("invalid_genre", "invalid_request")


@pytest.mark.parametrize("wait", ["-1", "-99999", "abc", "", "1e400",
                                  "NaN", "Infinity", "99999999", "1,5"])
def test_hostile_wait_values(client, wait):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio", "wait": wait})
    _assert_documented(r, f"wait={wait!r}")


@pytest.mark.parametrize("value", ["-1", "0", "999999999999", "abc",
                                   "1e100", "NaN", "-0"])
def test_hostile_limit_and_skip(client, value):
    """Pagination must never 500 or attempt an unbounded query."""
    r = client.get(f"/v1/analyses?limit={value}")
    _assert_documented(r, f"limit={value!r}")
    r = client.get(f"/v1/analyses?skip={value}")
    _assert_documented(r, f"skip={value!r}")


# --------------------------------------------------------------- webhooks ---
@pytest.mark.parametrize("url,expect", [
    ("http://example.com/hook", "webhook_requires_https"),
    ("https://127.0.0.1/hook", "webhook_destination_not_allowed"),
    ("https://localhost/hook", "webhook_destination_not_allowed"),
    ("https://169.254.169.254/latest/meta-data/", "webhook_destination_not_allowed"),
    ("https://10.0.0.1/hook", "webhook_destination_not_allowed"),
    ("https://192.168.1.1/hook", "webhook_destination_not_allowed"),
    ("https://172.16.0.1/hook", "webhook_destination_not_allowed"),
    ("https://[::1]/hook", "webhook_destination_not_allowed"),
    ("https://0.0.0.0/hook", "webhook_destination_not_allowed"),
])
def test_ssrf_targets_are_refused(client, url, expect):
    """Webhook SSRF: cloud metadata and private ranges must be refused."""
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio", "webhook_url": url})
    assert r.status_code == 422, f"{url} was accepted"
    assert r.json()["error"]["code"] == expect


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://x/", "ftp://x/", "javascript:alert(1)",
    "not a url", "https://", "", " ",
    "https://user:pass@example.com/hook",
])
def test_malformed_webhook_urls(client, url):
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")},
                    data={"mode": "audio", "webhook_url": url})
    _assert_documented(r, f"webhook={url!r}")
    if url.strip():
        assert r.status_code == 422, f"{url!r} was accepted"


@pytest.mark.parametrize("url", ["https://...", "https://.", "https://..",
                                 "https://-.example", "https://exam ple.com"])
def test_webhook_urls_httpx_cannot_even_encode(url):
    """Validated directly: the test client refuses to send these at all.

    A raw socket client is under no such constraint, so the validator itself
    has to reject them rather than relying on the caller's HTTP library.
    """
    from labs.core.net import WebhookURLError, validate_webhook_url

    with pytest.raises(WebhookURLError):
        validate_webhook_url(url)


# ------------------------------------------------------------------ paths ---
@pytest.mark.parametrize("job_id", [
    "../../../etc/passwd", "%2e%2e%2f", "a" * 5000,
    "{'$ne': null}", "null", "0", "-1",
])
def test_hostile_job_ids(client, job_id):
    r = client.get(f"/v1/analyses/{job_id}")
    _assert_documented(r, f"job_id={job_id!r}")
    assert r.status_code in (404, 422)


@pytest.mark.parametrize("slug", [
    "../../etc/passwd", "master-check/../../../", "a" * 5000,
    "{'$ne': null}", "master check", "MASTER-CHECK",
])
def test_hostile_tool_slugs(client, slug):
    r = client.get(f"/v1/tools/{slug}")
    _assert_documented(r, f"slug={slug!r}")
    assert r.status_code in (404, 422)


def test_unrouted_paths_still_use_the_error_envelope(client):
    """A routing 404 is the one response most likely to skip the envelope."""
    for path in ("/v1/nope", "/v1/analyses/a/b/c", "/nothing-here",
                 "/v1/tools/x/y/z"):
        r = client.get(path)
        assert r.status_code in (404, 405), f"{path} -> {r.status_code}"
        body = r.json()
        assert "error" in body, f"{path} bypassed the envelope: {body}"
        assert "code" in body["error"]


def test_wrong_method_uses_the_error_envelope(client):
    r = client.delete("/v1/analyses")
    assert r.status_code in (404, 405)
    assert "error" in r.json()


# ------------------------------------------------------------------ auth ----
@pytest.mark.parametrize("key", [
    "", " ", "\x00", "a" * 10000, "Bearer x", "null", "undefined",
    "{'$ne': null}", '{"$gt": ""}', "' OR '1'='1",
])
def test_hostile_api_keys_when_auth_enforced(monkeypatch, key):
    """A malformed key is rejected cleanly, never a 500."""
    from fastapi.testclient import TestClient

    import labs.api.deps as deps_mod
    from labs.application import app

    def strict(raw, settings=None):
        return (None, "missing_api_key") if not raw else (None, "invalid_api_key")

    monkeypatch.setattr(deps_mod, "authenticate", strict)
    c = TestClient(app)
    r = c.post("/v1/analyses",
               files={"file": ("t.wav", WAV, "audio/wav")},
               data={"mode": "audio"},
               headers={"X-API-Key": key})
    assert r.status_code == 401
    assert r.json()["error"]["code"] in ("missing_api_key", "invalid_api_key")


def test_api_key_is_never_echoed_in_a_response(monkeypatch):
    """A leaked key in a response body or header is a credential disclosure."""
    from fastapi.testclient import TestClient

    import labs.api.deps as deps_mod
    from labs.application import app

    secret = "labs_live_SUPERSECRETVALUE123456"
    monkeypatch.setattr(deps_mod, "authenticate",
                        lambda raw, settings=None: (None, "invalid_api_key"))
    c = TestClient(app)
    r = c.post("/v1/analyses",
               files={"file": ("t.wav", WAV, "audio/wav")},
               data={"mode": "audio"},
               headers={"X-API-Key": secret})
    assert secret not in r.text
    assert secret not in str(dict(r.headers))


# ------------------------------------------------------- error hygiene -----
def test_errors_never_leak_internals(client):
    """No stack traces, file paths, or dependency names in error bodies."""
    probes = [
        ("/v1/analyses/does-not-exist", "get", None, None),
        ("/v1/tools/nope", "get", None, None),
        ("/v1/analyses", "post", {"file": ("t.wav", b"junk", "audio/wav")},
         {"mode": "audio"}),
        ("/v1/analyses", "post", {"file": ("t.wav", WAV, "audio/wav")},
         {"mode": "bogus"}),
    ]
    leaks = ("Traceback", "File \"/", "/Users/", "/home/", "site-packages",
             "pymongo", "torch", "librosa", "sqlalchemy", "labs.services",
             "labs.ml", "0x7f")
    for path, method, files, data in probes:
        r = (client.get(path) if method == "get"
             else client.post(path, files=files, data=data))
        for token in leaks:
            assert token not in r.text, f"{path} leaked {token!r}: {r.text[:300]}"


def test_every_error_has_request_id_header(client):
    """Correlation must survive the error path or support cannot trace it."""
    for r in (client.get("/v1/analyses/nope"),
              client.get("/v1/tools/nope"),
              client.post("/v1/analyses", data={"mode": "audio"})):
        assert r.headers.get("X-Request-Id"), \
            f"missing X-Request-Id on {r.status_code}"


def test_caller_request_id_is_echoed(client):
    r = client.get("/health", headers={"X-Request-Id": "trace-me-123"})
    assert r.headers["X-Request-Id"] == "trace-me-123"


# ------------------------------------------------------------ concurrency ---
def test_concurrent_submissions_do_not_corrupt_state(client):
    """Parallel submits must each get a distinct id and a clean status."""
    results, errors = [], []
    lock = threading.Lock()

    def submit():
        try:
            r = client.post("/v1/analyses",
                            files={"file": ("t.wav", WAV, "audio/wav")},
                            data={"mode": "audio"})
            with lock:
                results.append((r.status_code, r.json()))
        except Exception as exc:                      # pragma: no cover
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"exceptions during concurrent submit: {errors}"
    for status, body in results:
        assert status in DOCUMENTED, f"{status}: {body}"

    ids = [b["id"] for s, b in results if s == 202 and "id" in b]
    assert len(ids) == len(set(ids)), "duplicate job ids handed out"


def test_concurrent_polls_of_same_job(client):
    sub = client.post("/v1/analyses",
                      files={"file": ("t.wav", WAV, "audio/wav")},
                      data={"mode": "audio"})
    if sub.status_code != 202:
        pytest.skip("submission was not queued")
    job_id = sub.json()["id"]

    seen, lock = [], threading.Lock()

    def poll():
        r = client.get(f"/v1/analyses/{job_id}")
        with lock:
            seen.append(r.status_code)

    threads = [threading.Thread(target=poll) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(s == 200 for s in seen), f"inconsistent poll statuses: {set(seen)}"


# ------------------------------------------------------------- exhaustion ---
def test_many_form_fields_are_ignored_not_fatal(client):
    data = {"mode": "audio"}
    data.update({f"junk_{i}": "x" * 100 for i in range(500)})
    r = client.post("/v1/analyses",
                    files={"file": ("t.wav", WAV, "audio/wav")}, data=data)
    _assert_documented(r)


def test_duplicate_form_fields(client):
    body, boundary = _multipart([
        ("mode", "audio"), ("mode", "full"), ("mode", "bogus"),
    ], filename="t.wav", content=WAV)
    r = client.post("/v1/analyses", content=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    _assert_documented(r)


def test_oversized_upload_is_refused(client):
    """Above the cap the server must refuse, not buffer the whole thing."""
    big = b"RIFF" + b"\x00" * (60 * 1024 * 1024)
    r = client.post("/v1/analyses",
                    files={"file": ("big.wav", big, "audio/wav")},
                    data={"mode": "audio"})
    assert r.status_code in (413, 415), f"got {r.status_code}"


def test_unicode_and_control_chars_in_reference(client):
    for ref in ["\x00\x01\x02", "🎵" * 100, "a" * 10000,
                "<script>alert(1)</script>", "{'$ne': null}"]:
        r = client.post("/v1/analyses",
                        files={"file": ("t.wav", WAV, "audio/wav")},
                        data={"mode": "audio", "reference": ref})
        _assert_documented(r, f"reference={ref[:30]!r}")


# ------------------------------------------------------------------ tools ---
@pytest.mark.parametrize("slug", ["master-check", "tempo-lab", "key-lab",
                                  "vocal-lab"])
def test_tool_rejects_second_unexpected_file(client, slug):
    r = client.post(f"/v1/tools/{slug}",
                    files={"file": ("t.wav", WAV, "audio/wav"),
                           "unexpected": ("x.wav", WAV, "audio/wav")})
    _assert_documented(r, slug)


def test_two_file_tool_with_swapped_field_names(client):
    r = client.post("/v1/tools/beat-vocal-fit",
                    files={"file": ("a.wav", WAV, "audio/wav"),
                           "reference": ("b.wav", WAV, "audio/wav")})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_input"


# --------------------------------------------------------------- helpers ---
def _multipart(fields, filename, content, boundary="stressboundary"):
    buf = io.BytesIO()
    for name, value in fields:
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        buf.write(f"{value}\r\n".encode())
    buf.write(f"--{boundary}\r\n".encode())
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode())
    buf.write(content)
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    return buf.getvalue(), boundary
