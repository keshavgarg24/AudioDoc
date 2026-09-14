"""ACRCloud File Scanning client for second opinion AI music detection.

This is deliberately a SECOND stage. It never runs on its own: the caller
either asks for it explicitly, or the local verdict is weak enough that the
escalation policy triggers. That keeps the third party cost and latency off
the common path while still giving a tie break when it matters.

Contract, verified against the live API rather than assumed:

  POST /api/fs-containers/{cid}/files      multipart: file, data_type=audio, name
       -> 201 {"data": {"id": "...", "state": 0, ...}}
  GET  /api/fs-containers/{cid}/files/{id}
       -> 200 {"data": [ {...} ]}          NOTE: an array, not an object
       state 0 = queued, 1 = done

  results.ai_detection[] = [{
      start, end, duration, prediction: "ai_generated"|"human",
      likely_source: "suno"|"Human"|..., ai_probability: 0..100,
      source_probabilities: [{source, probability}], stem, model_id
  }]

ACRCloud's own threshold is ai_probability >= 50.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests

log = logging.getLogger(__name__)

# ACRCloud's own documented threshold. Overridable because it is a policy
# knob, not a property of their API: a deployment that would rather catch more
# and review more can lower it, and one that only wants near-certain matches
# can raise it. Left at their default so an unconfigured deployment behaves
# exactly as their documentation describes.
ACR_THRESHOLD = float(os.environ.get("LABS_ACR_THRESHOLD", "50.0"))
# ACRCloud rejects audio longer than this.
ACR_MAX_DURATION_S = 900


class ACRError(RuntimeError):
    """Any failure talking to ACRCloud. Never fatal to the local analysis."""


@dataclass(frozen=True)
class ACRConfig:
    enabled: bool
    base_url: str
    container_id: str
    token: str
    region: str
    timeout_s: float
    poll_interval_s: float
    poll_max_s: float
    delete_after: bool

    @classmethod
    def from_env(cls) -> "ACRConfig":
        return cls(
            enabled=os.environ.get("ACR_ENABLED", "false").lower() in ("1", "true", "yes"),
            base_url=os.environ.get("ACR_BASE_URL", "https://api-v2.acrcloud.com").rstrip("/"),
            container_id=os.environ.get("ACR_CONTAINER_ID", ""),
            token=os.environ.get("ACR_BEARER_TOKEN", ""),
            region=os.environ.get("ACR_REGION", "ap-southeast-1"),
            timeout_s=float(os.environ.get("ACR_TIMEOUT_S", "30")),
            poll_interval_s=float(os.environ.get("ACR_POLL_INTERVAL_S", "2")),
            poll_max_s=float(os.environ.get("ACR_POLL_MAX_S", "120")),
            # Uploaded files count against the container quota, so remove them
            # once the result is read unless the operator wants an audit trail.
            delete_after=os.environ.get("ACR_DELETE_AFTER", "true").lower()
                         in ("1", "true", "yes"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.token and self.container_id)


class ACRClient:
    def __init__(self, cfg: Optional[ACRConfig] = None):
        self.cfg = cfg or ACRConfig.from_env()
        self._session = requests.Session()

    # -- plumbing ----------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.token}",
            "Accept": "application/json",
        }

    def _files_url(self, suffix: str = "") -> str:
        return (f"{self.cfg.base_url}/api/fs-containers/"
                f"{self.cfg.container_id}/files{suffix}")

    # -- operations --------------------------------------------------------
    def upload(self, path: str, name: Optional[str] = None) -> str:
        """Upload one file for scanning. Returns the ACRCloud file id."""
        display = name or os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                res = self._session.post(
                    self._files_url(),
                    headers=self._headers(),
                    files={"file": (display, fh)},
                    data={"data_type": "audio", "name": display},
                    timeout=self.cfg.timeout_s,
                )
        except requests.RequestException as exc:
            raise ACRError(f"upload failed: {exc}") from exc

        if res.status_code not in (200, 201):
            raise ACRError(f"upload returned {res.status_code}: {res.text[:300]}")
        try:
            return res.json()["data"]["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ACRError(f"unexpected upload response: {res.text[:300]}") from exc

    def fetch(self, file_id: str) -> Optional[Dict]:
        """Read one scanned file. Returns None while still queued."""
        try:
            res = self._session.get(
                self._files_url(f"/{file_id}"),
                headers=self._headers(),
                timeout=self.cfg.timeout_s,
            )
        except requests.RequestException as exc:
            raise ACRError(f"fetch failed: {exc}") from exc

        if res.status_code != 200:
            raise ACRError(f"fetch returned {res.status_code}: {res.text[:300]}")

        try:
            payload = res.json().get("data")
        except ValueError as exc:
            raise ACRError("fetch returned non JSON") from exc

        # This endpoint answers with an array even for a single id.
        if isinstance(payload, list):
            payload = next((f for f in payload if f.get("id") == file_id), None)
        if not payload:
            return None
        return payload if payload.get("state") == 1 else None

    def delete(self, file_id: str) -> None:
        try:
            self._session.delete(self._files_url(f"/{file_id}"),
                                 headers=self._headers(),
                                 timeout=self.cfg.timeout_s)
        except requests.RequestException:
            # Best effort cleanup: never fail an analysis over this.
            log.warning("ACRCloud cleanup failed for %s", file_id, exc_info=True)

    def scan(self, path: str, name: Optional[str] = None) -> Dict:
        """Upload, wait for the scan, normalise, then clean up."""
        if not self.cfg.configured:
            raise ACRError("ACRCloud is not configured")

        started = time.time()
        file_id = self.upload(path, name)
        try:
            deadline = started + self.cfg.poll_max_s
            record = None
            while time.time() < deadline:
                record = self.fetch(file_id)
                if record is not None:
                    break
                time.sleep(self.cfg.poll_interval_s)

            if record is None:
                raise ACRError(
                    f"scan did not complete within {self.cfg.poll_max_s:.0f}s")

            return normalise(record, elapsed=time.time() - started)
        finally:
            if self.cfg.delete_after:
                self.delete(file_id)


# --------------------------------------------------------------------------
def normalise(record: Dict, elapsed: float = 0.0) -> Dict:
    """Flatten ACRCloud's segment array into a stable response shape.

    A scan can return several segments. The overall verdict uses the
    duration weighted mean probability so a long human passage is not
    outvoted by one short flagged window.
    """
    segments = ((record.get("results") or {}).get("ai_detection")) or []
    if not segments:
        raise ACRError("scan completed without an ai_detection result")

    total = sum(float(s.get("duration") or 0) for s in segments) or 1.0
    weighted = sum(float(s.get("ai_probability") or 0) * float(s.get("duration") or 0)
                   for s in segments) / total

    # Merge per source scores across segments the same way.
    source_totals: Dict[str, float] = {}
    for s in segments:
        w = float(s.get("duration") or 0) / total
        for sp in s.get("source_probabilities") or []:
            src = str(sp.get("source"))
            source_totals[src] = source_totals.get(src, 0.0) + float(
                sp.get("probability") or 0) * w

    sources = sorted(
        ({"source": k, "probability": round(v, 2)} for k, v in source_totals.items()),
        key=lambda d: -d["probability"])

    # Prefer the label attached to the longest segment.
    longest = max(segments, key=lambda s: float(s.get("duration") or 0))
    likely = longest.get("likely_source")

    ai_prob = round(weighted, 2)
    prediction = "ai_generated" if ai_prob >= ACR_THRESHOLD else "human"

    return {
        # Never "acrcloud": the vendor identity is an implementation detail
        # and must not appear in any response this service returns.
        "provider": "verification",
        "prediction": prediction,
        "ai_probability": ai_prob,
        "ai_probability_unit": "percent",
        "threshold": ACR_THRESHOLD,
        "likely_source": likely,
        "source_probabilities": sources,
        "segments": [{
            "start": round(float(s.get("start") or 0), 2),
            "end": round(float(s.get("end") or 0), 2),
            "prediction": s.get("prediction"),
            "ai_probability": s.get("ai_probability"),
            "likely_source": s.get("likely_source"),
        } for s in segments],
        "file_duration_s": record.get("duration"),
        "elapsed_seconds": round(elapsed, 2),
    }
