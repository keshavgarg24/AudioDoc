"""One place that records an analysis, used by every entry point.

Both the async `/v1/analyses` path and the synchronous `/predict` path call
`record()`, so a track is stored exactly the same way regardless of how it was
submitted. That matters because the bundled web interface uses `/predict`; if
persistence lived only in the async path, everything a user uploaded through
the site would be analysed and then thrown away.

Persistence is best-effort by design. A database or bucket problem degrades the
service to "analysis still works, nothing is recorded" rather than failing the
user's request, and it is always logged.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, Optional

log = logging.getLogger(__name__)


def record(*, analysis_id: Optional[str], report: Dict, path: Optional[str],
           filename: str, seconds: float, mode: str,
           api_key_id: Optional[str] = None,
           request_id: Optional[str] = None,
           reference: Optional[str] = None,
           source: str = "api") -> Optional[str]:
    """Store one analysis. Returns the id it was stored under, or None.

    `path` is the audio on disk; pass None to skip audio upload. The file is
    read before the caller deletes it, so this must run inside the request or
    job that owns the temp file.
    """
    from .storage import get_audio_store, get_mongo

    mongo = get_mongo()
    if not mongo.enabled:
        return None

    analysis_id = analysis_id or uuid.uuid4().hex

    # Compute SHA-256 now, before any cleanup can remove the temp file.
    # The dedup check in analyses.py queries audio.sha256, so it must be
    # present regardless of whether S3 storage is enabled. Without this,
    # a deployment with no S3 bucket never stores the SHA, _dedup_hit always
    # misses, and the same audio is re-analysed on every submission.
    audio_sha = None
    if path:
        try:
            from ..tools.base import file_digest
            audio_sha = file_digest(path)
        except Exception:
            log.debug("SHA-256 computation failed for %s", analysis_id, exc_info=True)

    audio = None
    if path:
        try:
            audio = get_audio_store().put(path, analysis_id, filename)
        except Exception:
            log.warning("Audio upload failed for %s", analysis_id, exc_info=True)

    # Always include the SHA even when S3 is not configured.
    if audio_sha:
        if audio is None:
            audio = {}
        audio.setdefault("sha256", audio_sha)

    try:
        mongo.save_analysis(analysis_id, report, meta={
            "filename": filename,
            "reference": reference,
            "api_key_id": api_key_id,
            "request_id": request_id,
            "source": source,
            "model": report.get("model") or {},
            "timing": {"analysis_seconds": round(seconds, 2)},
        }, audio=audio)
        mongo.record_usage(api_key_id, seconds, mode)
        return analysis_id
    except Exception:
        log.warning("Could not store analysis %s", analysis_id, exc_info=True)
        return None
