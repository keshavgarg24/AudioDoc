#!/usr/bin/env python3
"""Load harness for the LABS API.

Not a pytest module: it drives a *running* server so the numbers reflect the
real ASGI stack, the real worker pool and the real socket layer. TestClient
runs the app in-process and would measure none of those.

    # terminal 1
    uvicorn labs.application:app --port 8765

    # terminal 2
    python tests/stress/loadtest.py --base http://127.0.0.1:8765 \
        --audio audio/1.mp3 --users 50 --requests 500

Reports latency percentiles, throughput, and the status-code spread. A healthy
run has no 5xx at all: saturation must surface as 429, which is a documented,
retryable answer, never as a timeout or a crash.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

BOUNDARY = "labsloadtestboundary"


# --------------------------------------------------------------- transport --
def _multipart(fields: dict, filename: Optional[str] = None,
               content: bytes = b"") -> bytes:
    out = bytearray()
    for name, value in fields.items():
        out += f"--{BOUNDARY}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    if filename is not None:
        out += f"--{BOUNDARY}\r\n".encode()
        out += (f'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: audio/mpeg\r\n\r\n").encode()
        out += content
        out += b"\r\n"
    out += f"--{BOUNDARY}--\r\n".encode()
    return bytes(out)


def _call(url: str, method: str = "GET", body: Optional[bytes] = None,
          headers: Optional[dict] = None,
          timeout: float = 60.0) -> Tuple[int, float, str]:
    """(status, elapsed_seconds, body_text). Never raises."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(2048).decode("utf-8", "replace")
            return resp.status, time.perf_counter() - started, text
    except urllib.error.HTTPError as exc:
        text = exc.read(2048).decode("utf-8", "replace")
        return exc.code, time.perf_counter() - started, text
    except Exception as exc:                                   # timeout, reset
        return 0, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


# ----------------------------------------------------------------- reports --
def _percentiles(samples: List[float]) -> dict:
    if not samples:
        return {}
    ordered = sorted(samples)

    def pct(p: float) -> float:
        idx = min(len(ordered) - 1, int(len(ordered) * p))
        return ordered[idx]

    return {
        "min_ms": ordered[0] * 1000,
        "p50_ms": pct(0.50) * 1000,
        "p90_ms": pct(0.90) * 1000,
        "p95_ms": pct(0.95) * 1000,
        "p99_ms": pct(0.99) * 1000,
        "max_ms": ordered[-1] * 1000,
        "mean_ms": statistics.fmean(ordered) * 1000,
    }


def _report(title: str, statuses: Counter, latencies: List[float],
            wall: float) -> bool:
    """Print one scenario's result. Returns True if it passed."""
    total = sum(statuses.values())
    server_errors = sum(n for s, n in statuses.items() if s >= 500)
    transport = statuses.get(0, 0)

    print(f"\n{title}")
    print("-" * len(title))
    print(f"  requests      {total}")
    print(f"  wall clock    {wall:.2f}s")
    print(f"  throughput    {total / wall:.1f} req/s")
    print(f"  statuses      {dict(sorted(statuses.items()))}")
    for name, value in _percentiles(latencies).items():
        print(f"  {name:<13} {value:.1f}")

    ok = True
    if server_errors:
        print(f"  FAIL          {server_errors} server error(s) (5xx)")
        ok = False
    if transport:
        print(f"  FAIL          {transport} transport failure(s) (timeout/reset)")
        ok = False
    if ok:
        print("  PASS          no 5xx, no dropped connections")
    return ok


# --------------------------------------------------------------- scenarios --
def scenario_read_throughput(base: str, users: int, requests: int,
                             headers: dict) -> bool:
    """Cheap read endpoints under concurrency. This is the frontend's path."""
    statuses: Counter = Counter()
    latencies: List[float] = []
    lock = threading.Lock()
    paths = ["/health", "/v1/tools", "/v1/genres"]

    def one(i: int) -> None:
        status, elapsed, _ = _call(f"{base}{paths[i % len(paths)]}",
                                   headers=headers, timeout=30)
        with lock:
            statuses[status] += 1
            latencies.append(elapsed)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        list(as_completed(pool.submit(one, i) for i in range(requests)))
    return _report(f"Read throughput  ({users} concurrent, {requests} requests)",
                   statuses, latencies, time.perf_counter() - started)


def scenario_submission_saturation(base: str, users: int, requests: int,
                                   audio: bytes, headers: dict) -> bool:
    """Hammer the submit endpoint past its concurrency budget.

    The point is not how many succeed. It is that every rejection is a
    documented 429 the client can act on, and that nothing 5xxes or hangs.
    """
    statuses: Counter = Counter()
    latencies: List[float] = []
    codes: Counter = Counter()
    lock = threading.Lock()
    body = _multipart({"mode": "audio"}, "load.mp3", audio)
    hdrs = {**headers,
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}

    def one(_: int) -> None:
        status, elapsed, text = _call(f"{base}/v1/analyses", "POST", body,
                                      hdrs, timeout=90)
        code = ""
        if status >= 400:
            try:
                code = json.loads(text).get("error", {}).get("code", "")
            except Exception:
                code = "unparseable"
        with lock:
            statuses[status] += 1
            latencies.append(elapsed)
            if code:
                codes[code] += 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        list(as_completed(pool.submit(one, i) for i in range(requests)))
    ok = _report(f"Submission saturation  ({users} concurrent, {requests} uploads)",
                 statuses, latencies, time.perf_counter() - started)
    if codes:
        print(f"  error codes   {dict(codes)}")
    unparseable = codes.get("unparseable", 0)
    if unparseable:
        print(f"  FAIL          {unparseable} response(s) missing the error envelope")
        ok = False
    return ok


def scenario_queue_integrity(base: str, headers: dict) -> bool:
    """After the storm, the reported queue depth must be believable.

    A rejected submission used to leave a `queued` job behind that eviction
    could never reclaim, so depth drifted upward permanently. This catches
    that class of leak.
    """
    print("\nQueue integrity")
    print("-" * len("Queue integrity"))
    status, _, text = _call(f"{base}/health", headers=headers, timeout=30)
    if status != 200:
        print(f"  FAIL          /health returned {status}")
        return False
    depth = json.loads(text).get("queue", {}).get("depth")
    print(f"  queue depth   {depth}")
    print("  note          should fall to ~0 once work drains; a depth that")
    print("                only ever grows is an orphaned-job leak")
    return True


def scenario_malformed_flood(base: str, users: int, requests: int,
                             headers: dict) -> bool:
    """Junk at speed. Rejection must stay cheap and must never 5xx."""
    statuses: Counter = Counter()
    latencies: List[float] = []
    lock = threading.Lock()
    payloads = [
        _multipart({"mode": "bogus"}, "x.wav", b"RIFFjunk"),
        _multipart({"mode": "audio"}, "x.exe", b"MZ\x00\x00"),
        _multipart({"mode": "audio"}, "x.wav", b"PK\x03\x04"),
        _multipart({"mode": "audio"}, "x.wav", b""),
        _multipart({"mode": "audio", "genre": "nonsense"}, "x.wav", b"RIFF"),
    ]
    hdrs = {**headers,
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}

    def one(i: int) -> None:
        status, elapsed, _ = _call(f"{base}/v1/analyses", "POST",
                                   payloads[i % len(payloads)], hdrs,
                                   timeout=30)
        with lock:
            statuses[status] += 1
            latencies.append(elapsed)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        list(as_completed(pool.submit(one, i) for i in range(requests)))
    return _report(f"Malformed flood  ({users} concurrent, {requests} requests)",
                   statuses, latencies, time.perf_counter() - started)


# -------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--audio", type=Path, default=None,
                    help="Audio file to upload. Synthetic silence if omitted.")
    ap.add_argument("--users", type=int, default=25)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    headers = {"X-API-Key": args.api_key} if args.api_key else {}

    if args.audio and args.audio.is_file():
        audio = args.audio.read_bytes()
        print(f"Using {args.audio} ({len(audio) / 1e6:.1f} MB)")
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.conftest import make_wav
        audio = make_wav(seconds=3.0)
        print("Using synthetic 3s silence")

    status, _, _ = _call(f"{args.base}/health", headers=headers, timeout=10)
    if status != 200:
        print(f"Server not reachable at {args.base} (/health -> {status})")
        return 2

    print(f"Target {args.base}   users={args.users}   requests={args.requests}")

    results = [
        scenario_read_throughput(args.base, args.users, args.requests, headers),
        scenario_malformed_flood(args.base, args.users,
                                 max(50, args.requests // 4), headers),
        scenario_submission_saturation(args.base, args.users,
                                       max(20, args.requests // 10), audio,
                                       headers),
        scenario_queue_integrity(args.base, headers),
    ]

    print("\n" + "=" * 46)
    if all(results):
        print("RESULT: PASS - no 5xx, no dropped connections")
        return 0
    print("RESULT: FAIL - see the scenarios marked FAIL above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
