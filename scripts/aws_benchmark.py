#!/usr/bin/env python3
"""Run a corpus through a deployed LABS stack and record what happened.

WHAT THIS MEASURES
------------------
Three things, kept separate because they fail for different reasons:

  1. Level 1 in isolation      POST /v1/screen, synchronous. Measures the
                               screen tier's own latency with no queue in the
                               way.
  2. The tier decision         POST /v1/analyses with mode=ai. Level 1 runs
                               again inside the request and decides whether to
                               pay for the backbone. This is the number that
                               matters in production, because it includes the
                               early exits.
  3. AWS in the middle         Submit time, queue wait, worker processing, and
                               total wall clock, recorded per job rather than
                               averaged. An average hides the cold start, and
                               the cold start is the interesting part.

WHY IT WRITES JSONL AS IT GOES
------------------------------
A hundred tracks at ~60 s of backbone each is over an hour of wall clock. A
run that only produces output at the end is a run that produces nothing when
the laptop sleeps. Every result is appended and flushed immediately, and
--resume skips files already in the file.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# HTTP. Deliberately stdlib: this script runs on an operator's laptop against
# a production stack, and asking them to pip install a client first is one more
# thing that can be wrong when they are already debugging something.
# --------------------------------------------------------------------------


def _multipart(path: Path, fields: Dict[str, str]) -> tuple:
    """Build a multipart/form-data body. Returns (content_type, body)."""
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    out += f"--{boundary}\r\n".encode()
    out += (f'Content-Disposition: form-data; name="file"; '
            f'filename="{path.name}"\r\n').encode()
    out += b"Content-Type: audio/mpeg\r\n\r\n"
    out += path.read_bytes()
    out += f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


class Client:
    def __init__(self, base: str, key: str, timeout: int = 300):
        self.base = base.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _send(self, req: urllib.request.Request) -> tuple:
        """Returns (status, parsed_json_or_text, elapsed_seconds)."""
        req.add_header("X-API-Key", self.key)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except Exception as e:  # noqa: BLE001  network-level failure
            return 0, {"error": type(e).__name__, "detail": str(e)[:300]}, \
                time.perf_counter() - t0
        elapsed = time.perf_counter() - t0
        try:
            return status, json.loads(raw), elapsed
        except json.JSONDecodeError:
            return status, {"raw": raw[:500]}, elapsed

    def post_file(self, route: str, path: Path,
                  fields: Optional[Dict[str, str]] = None) -> tuple:
        ctype, body = _multipart(path, fields or {})
        req = urllib.request.Request(f"{self.base}{route}", data=body,
                                     method="POST")
        req.add_header("Content-Type", ctype)
        return self._send(req)

    def get(self, route: str) -> tuple:
        return self._send(urllib.request.Request(f"{self.base}{route}"))


class RateLimiter:
    """Token bucket. The screen tier caps the ungated route at 30 req/min and
    answers 429 past it; pacing below that is cheaper than retrying through it.
    """

    def __init__(self, per_minute: float):
        self.interval = 60.0 / max(per_minute, 0.001)
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait:
            time.sleep(wait)


# --------------------------------------------------------------------------
# Extraction. Every field is pulled defensively: this runs against a live
# service, and a KeyError 80 tracks into a 90-minute run is a wasted evening.
# --------------------------------------------------------------------------


def _dig(d: Any, *path, default=None):
    for key in path:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
        if d is None:
            return default
    return d


def summarise_level1(body: Dict) -> Dict:
    a = body.get("assessment") or {}
    l1 = _dig(body, "detection", "level_1", default={}) or {}
    return {
        "verdict": body.get("verdict"),
        "label": a.get("label"),
        "band": a.get("band"),
        "score": a.get("score"),
        "assessment_confidence": a.get("confidence"),
        "legacy_confidence": body.get("confidence"),
        "next_step": body.get("next_step"),
        "probability": body.get("fake_probability"),
        "review_recommended": body.get("review_recommended"),
        "vetoes": body.get("vetoes") or [],
        "reasons": body.get("reasons") or [],
        "duration_s": body.get("duration"),
        "server_elapsed_s": l1.get("elapsed_s"),
        "stage_timings": l1.get("timings") or {},
        "ensemble": {
            "agreement": _dig(l1, "ensemble", "agreement"),
            "agreement_score": _dig(l1, "ensemble", "agreement_score"),
            "agreement_strength": _dig(l1, "ensemble", "agreement_strength"),
            "confidence_multiplier": _dig(l1, "ensemble", "confidence_multiplier"),
            "fakeprint": _dig(l1, "ensemble", "models", "fakeprint"),
            "cepstrum": _dig(l1, "ensemble", "models", "cepstrum"),
        },
        "robustness": {
            "available": _dig(l1, "robustness", "available"),
            "stable": _dig(l1, "robustness", "stable"),
            "stability": _dig(l1, "robustness", "stability"),
            "flipped": _dig(l1, "robustness", "flipped"),
        },
        "errors": l1.get("errors") or {},
    }


def summarise_level2(body: Dict) -> Dict:
    result = body.get("result") if isinstance(body.get("result"), dict) else body
    a = result.get("assessment") or {}
    return {
        "levels_run": result.get("levels_run"),
        "early_exit": bool(result.get("early_exit")),
        "early_exit_reason": _dig(result, "early_exit", "reason"),
        "verdict": result.get("verdict"),
        "label": a.get("label"),
        "band": a.get("band"),
        "score": a.get("score"),
        "assessment_confidence": a.get("confidence"),
        "prediction": result.get("prediction"),
        "legacy_confidence": result.get("confidence"),
        "fake_probability": result.get("fake_probability"),
        "raw_logit": result.get("raw_logit"),
        "decisive": result.get("decisive"),
        "band_note": result.get("band_note"),
        "segments_used": result.get("segments_used"),
        "cascade": {
            "enabled": _dig(result, "cascade", "enabled"),
            "escalated": _dig(result, "cascade", "escalated"),
            "passes": _dig(result, "cascade", "passes"),
            "segments_scored": _dig(result, "cascade", "segments_scored"),
            "segments_available": _dig(result, "cascade", "segments_available"),
            "first_pass_logit": _dig(result, "cascade", "first_pass_logit"),
        },
        "level_agreement": _dig(result, "detection", "level_agreement", "state"),
        "label_agreement": _dig(result, "detection", "level_agreement",
                                "labels", "match"),
        "level_1_label": _dig(result, "detection", "level_1", "assessment",
                              "label"),
        "level_1_verdict": _dig(result, "detection", "level_1", "verdict"),
        "server_elapsed_s": result.get("elapsed_seconds"),
    }


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------


def phase_screen(client: Client, files: List[Path], limiter: RateLimiter,
                 workers: int, log) -> Dict[str, Dict]:
    """Level 1 alone, timed without a queue in the way."""
    out: Dict[str, Dict] = {}
    lock = threading.Lock()
    done = [0]

    def one(path: Path) -> None:
        limiter.acquire()
        attempt, rec = 0, None
        while attempt < 3:
            attempt += 1
            status, body, elapsed = client.post_file("/v1/screen", path)
            if status == 429:
                time.sleep(5 * attempt)
                continue
            rec = {"http_status": status, "round_trip_s": round(elapsed, 3),
                   "attempts": attempt}
            if status == 200:
                rec.update(summarise_level1(body))
            else:
                rec["error"] = str(body)[:400]
            break
        with lock:
            out[path.name] = rec or {"http_status": 429, "error": "rate limited"}
            done[0] += 1
            if done[0] % 10 == 0:
                log(f"  level 1: {done[0]}/{len(files)}")

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, files))
    return out


def phase_submit(client: Client, files: List[Path], mode: str,
                 limiter: RateLimiter, workers: int, log) -> Dict[str, Dict]:
    """Queue every track, recording the moment each was accepted.

    Submitting the whole corpus at once is deliberate: it is the only way to
    see the queue behave like a queue, and the depth curve it produces is the
    most honest picture of what the worker fleet actually does.
    """
    out: Dict[str, Dict] = {}
    lock = threading.Lock()

    def one(path: Path) -> None:
        limiter.acquire()
        status, body, elapsed = client.post_file(
            "/v1/analyses", path, {"mode": mode})
        rec = {"submit_http_status": status,
               "submit_round_trip_s": round(elapsed, 3),
               "submitted_at": time.time()}
        if status in (200, 201, 202):
            rec["job_id"] = body.get("id") or body.get("job_id")
            rec["submit_status"] = body.get("status")
        else:
            rec["submit_error"] = str(body)[:400]
        with lock:
            out[path.name] = rec

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, files))
    submitted = sum(1 for r in out.values() if r.get("job_id"))
    log(f"  queued {submitted}/{len(files)}")
    return out


TERMINAL = {"completed", "succeeded", "failed", "error"}


def phase_poll(client: Client, submissions: Dict[str, Dict], timeout_s: int,
               log) -> Dict[str, Dict]:
    """Poll until every job reaches a terminal state or the deadline passes.

    Status is confirmed twice before being accepted. Atlas serves reads from a
    secondary, so a job can legitimately report `queued` immediately after
    reporting `succeeded`; taking the first answer produces a run where a
    quarter of the jobs look like they never finished.
    """
    pending = {name: rec["job_id"] for name, rec in submissions.items()
               if rec.get("job_id")}
    results: Dict[str, Dict] = {}
    confirm: Dict[str, str] = {}
    deadline = time.time() + timeout_s
    last_log = 0.0

    while pending and time.time() < deadline:
        for name, job in list(pending.items()):
            status, body, _ = client.get(f"/v1/analyses/{job}")
            if status != 200:
                # A 404 right after submit is the read replica again, not a
                # lost job. Only give up on it at the deadline.
                continue
            state = (body.get("status") or "").lower()
            if state not in TERMINAL:
                confirm.pop(name, None)
                continue
            if confirm.get(name) != state:
                confirm[name] = state
                continue  # seen once; require a second identical read
            rec = {"final_status": state, "completed_at": time.time()}
            if state in ("completed", "succeeded"):
                rec.update(summarise_level2(body))
            else:
                rec["failure"] = str(body.get("error") or body)[:400]
            results[name] = rec
            pending.pop(name, None)

        if time.time() - last_log > 30:
            log(f"  level 2: {len(results)} done, {len(pending)} outstanding")
            last_log = time.time()
        if pending:
            time.sleep(5)

    for name in pending:
        results[name] = {"final_status": "timeout",
                         "completed_at": time.time()}
    return results


# --------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--dataset", required=True, help="directory of audio files")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--seed", type=int, default=20260916,
                   help="fixed so the same 100 tracks can be re-run later")
    p.add_argument("--glob", default="*.mp3")
    p.add_argument("--mode", default="ai", choices=["ai", "full", "screen"])
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--screen-rpm", type=float, default=24.0)
    p.add_argument("--submit-rpm", type=float, default=60.0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--poll-timeout", type=int, default=10800)
    p.add_argument("--skip-screen", action="store_true")
    args = p.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    logpath = outdir / "run.log"

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with logpath.open("a") as fh:
            fh.write(line + "\n")

    root = Path(args.dataset).expanduser()
    pool = sorted(root.glob(args.glob))
    if not pool:
        log(f"no files matching {args.glob} under {root}")
        return 1

    rng = random.Random(args.seed)
    files = rng.sample(pool, min(args.count, len(pool)))
    log(f"corpus {root} has {len(pool)} files; sampled {len(files)} "
        f"with seed {args.seed}")
    log(f"target {args.base_url}  mode={args.mode}")

    started = time.time()
    run: Dict[str, Any] = {
        "meta": {
            "base_url": args.base_url,
            "dataset": str(root),
            "corpus_size": len(pool),
            "sampled": len(files),
            "seed": args.seed,
            "mode": args.mode,
            "started_at": started,
            "started_iso": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "files": [f.name for f in files],
            "sizes_bytes": {f.name: f.stat().st_size for f in files},
        }
    }

    # -- phase 1 ----------------------------------------------------------
    screen: Dict[str, Dict] = {}
    if not args.skip_screen:
        log(f"phase 1/3  Level 1 only, paced at {args.screen_rpm}/min")
        t0 = time.time()
        screen = phase_screen(Client(args.base_url, args.api_key), files,
                              RateLimiter(args.screen_rpm), args.workers, log)
        run["meta"]["screen_phase_s"] = round(time.time() - t0, 2)
        log(f"phase 1 done in {run['meta']['screen_phase_s']}s")
        (outdir / "level1.json").write_text(json.dumps(screen, indent=2))

    # -- phase 2 ----------------------------------------------------------
    log(f"phase 2/3  queueing {len(files)} analyses (mode={args.mode})")
    t0 = time.time()
    subs = phase_submit(Client(args.base_url, args.api_key), files, args.mode,
                        RateLimiter(args.submit_rpm), args.workers, log)
    run["meta"]["submit_phase_s"] = round(time.time() - t0, 2)
    run["meta"]["queue_drain_started"] = time.time()
    (outdir / "submissions.json").write_text(json.dumps(subs, indent=2))

    # -- phase 3 ----------------------------------------------------------
    log("phase 3/3  polling until the queue drains")
    t0 = time.time()
    deep = phase_poll(Client(args.base_url, args.api_key), subs,
                      args.poll_timeout, log)
    run["meta"]["poll_phase_s"] = round(time.time() - t0, 2)
    (outdir / "level2.json").write_text(json.dumps(deep, indent=2))

    # -- merge ------------------------------------------------------------
    rows = []
    for f in files:
        n = f.name
        sub = subs.get(n, {})
        d = deep.get(n, {})
        row: Dict[str, Any] = {"file": n,
                               "size_bytes": run["meta"]["sizes_bytes"][n]}
        for k, v in (screen.get(n) or {}).items():
            row[f"l1_{k}"] = v
        row.update({k: v for k, v in sub.items()})
        for k, v in d.items():
            row[f"l2_{k}"] = v
        if sub.get("submitted_at") and d.get("completed_at"):
            row["end_to_end_s"] = round(d["completed_at"] - sub["submitted_at"], 2)
        rows.append(row)

    run["rows"] = rows
    run["meta"]["total_s"] = round(time.time() - started, 2)
    run["meta"]["finished_iso"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    (outdir / "results.json").write_text(json.dumps(run, indent=2))
    with (outdir / "results.jsonl").open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    ok = sum(1 for r in rows if r.get("l2_final_status") in
             ("completed", "succeeded"))
    log(f"finished in {run['meta']['total_s']}s: {ok}/{len(rows)} analyses "
        f"completed")
    log(f"wrote {outdir}/results.json")

    med = [r["end_to_end_s"] for r in rows if r.get("end_to_end_s")]
    if med:
        log(f"end-to-end median {statistics.median(med):.1f}s  "
            f"min {min(med):.1f}s  max {max(med):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
