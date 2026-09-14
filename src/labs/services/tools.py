"""Runs a tool: content dedup, decode, execute, cache.

Deduplication is by SHA-256 of the uploaded bytes plus a fingerprint of the
options. That combination is exact - the same file with the same options always
produces the same key, and any change to either produces a different one - so a
cache hit can return a stored result with no risk of answering the wrong
question.

The limit is worth being precise about, because it is easy to oversell: this
matches byte-identical files only. A re-export at a different bitrate, a
re-encode, or a trim of one sample is a different file and will be analysed
again. Catching those needs acoustic fingerprinting, which is a different
technique with its own error rates and is not implemented here.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable, Dict, List, Optional

from .. import tools as registry
from ..tools.base import ToolError

log = logging.getLogger(__name__)


def cache_key(tool: str, digests: List[str], params: Dict) -> str:
    """Exact key for (these bytes, this tool, these options)."""
    blob = "|".join([tool, *sorted(digests),
                     registry.params_fingerprint(tool, params)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run(tool: str, files: Dict[str, str], params: Optional[Dict] = None,
        progress: Optional[Callable[[str], None]] = None,
        use_cache: bool = True) -> Dict:
    """Execute one tool and return its envelope.

    `files` maps the tool's declared input names to paths on disk.
    """
    params = dict(params or {})
    module = registry.get(tool)
    if module is None:
        raise ToolError(f"Unknown tool '{tool}'.")

    spec = module.SPEC

    missing = [name for name in spec.inputs if not files.get(name)]
    if missing:
        raise ToolError(
            f"{spec.name} requires {len(spec.inputs)} file(s): "
            f"{', '.join(spec.inputs)}. Missing: {', '.join(missing)}.")

    def tick(stage: str) -> None:
        if progress:
            progress(stage)

    tick("hashing")
    digests = {name: registry.file_digest(files[name]) for name in spec.inputs}
    key = cache_key(tool, list(digests.values()), params)

    if use_cache:
        hit = _cached(key)
        if hit is not None:
            log.info("Tool cache hit for %s (%s)", tool, key[:12])
            return {
                "tool": tool, "name": spec.name, "result": hit,
                "cached": True,
                "cache_key": key,
                "inputs": _input_meta(digests),
                "duration_seconds": 0.0,
            }

    started = time.time()
    tick("decoding")
    decoded = {name: registry.decode(files[name]) for name in spec.inputs}

    kwargs: Dict = dict(params)

    tick("measuring")
    positional = [decoded[name] for name in spec.inputs]
    try:
        result = module.run(*positional, **kwargs)
    except ToolError:
        raise
    except Exception as exc:
        log.exception("Tool %s failed", tool)
        raise ToolError(
            f"{spec.name} could not complete on this input.") from exc

    elapsed = round(time.time() - started, 2)
    result.setdefault("meta", {})
    result["meta"].update({
        "tool": tool,
        "name": spec.name,
        "accuracy": spec.accuracy,
        "basis": spec.basis,
        "measured_at": time.time(),
    })

    if use_cache:
        _store(key, tool, digests, params, result)
        _record_audio_files(digests, decoded, files)

    tick("finalising")
    return {
        "tool": tool, "name": spec.name, "result": result,
        "cached": False,
        "cache_key": key,
        "inputs": _input_meta(digests, decoded),
        "duration_seconds": elapsed,
    }


def _input_meta(digests: Dict[str, str],
                decoded: Optional[Dict] = None) -> List[Dict]:
    rows = []
    for name, digest in digests.items():
        row = {"input": name, "sha256": digest}
        if decoded and name in decoded:
            d = decoded[name]
            row.update({"duration_seconds": d.duration,
                        "channels": d.channels,
                        "sample_rate": d.sr})
        rows.append(row)
    return rows



# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
def _record_audio_files(digests: Dict[str, str],
                        decoded: Optional[Dict],
                        files: Dict[str, str]) -> None:
    """Upsert one record per audio input into the audio_files collection.

    Keyed by SHA-256 of the raw bytes. On a cache hit the record already
    exists and the upsert is a no-op (MongoDB $setOnInsert).
    On first analysis the audio's duration, sample-rate and channel count are
    saved so any later tool can find them by hash without re-decoding the file.
    """
    import os

    from .storage import _utcnow, get_mongo

    mongo = get_mongo()
    if not mongo.enabled:
        return

    db = mongo.db()
    for name, sha256 in digests.items():
        meta: Dict = {"sha256": sha256}
        if decoded and name in decoded:
            d = decoded[name]
            meta.update({
                "duration_seconds": d.duration,
                "channels": d.channels,
                "sample_rate": d.sr,
            })
        path = files.get(name)
        if path and os.path.exists(path):
            meta["bytes"] = os.path.getsize(path)

        # One update per input rather than a bulk_write: a tool has one or two
        # inputs, so batching saves nothing measurable, and a per-input write
        # means a failure on one does not discard the others.
        try:
            db.audio_files.update_one(
                {"_id": sha256},
                {
                    "$setOnInsert": {**meta, "first_seen_at": _utcnow()},
                    "$set": {"last_seen_at": _utcnow()},
                    "$inc": {"analysis_count": 1},
                },
                upsert=True,
            )
        except Exception:
            log.debug("audio_files record failed for %s", sha256[:12],
                      exc_info=True)


def _cached(key: str) -> Optional[Dict]:
    from .storage import get_mongo

    mongo = get_mongo()
    if not mongo.enabled:
        return None
    try:
        return mongo.get_tool_result(key)
    except Exception:
        log.debug("Tool cache read failed", exc_info=True)
        return None


def _store(key: str, tool: str, digests: Dict[str, str], params: Dict,
           result: Dict) -> None:
    from .storage import get_mongo

    mongo = get_mongo()
    if not mongo.enabled:
        return
    try:
        mongo.save_tool_result(key, tool, digests, params, result)
    except Exception:
        log.debug("Tool cache write failed", exc_info=True)
