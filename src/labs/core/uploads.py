"""Staging an uploaded file to disk, safely.

Every untrusted byte enters the service through here, on both the analysis
route and the tools route. One implementation rather than one per route: the
two differ in what they do with the file afterwards, never in what they will
accept, and a second copy is how that stops being true.

Two things are enforced:

  - a size cap, applied as the stream is consumed rather than after, so an
    oversized upload is refused without ever being written in full;
  - a container check on the leading bytes, because the extension is a claim
    by the caller and the decode path hands the file to ffmpeg and libsndfile.
    Those are large C parsers with a history of memory-safety bugs, and there
    is no reason to let them see anything that is not recognisably audio.

Neither is a substitute for the decoder's own validation. They are the cheap
filters that keep obvious junk away from the expensive, riskier code.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Tuple

from fastapi import UploadFile

from ..api.errors import api_error
from .config import get_settings

log = logging.getLogger(__name__)

# Every staged file starts with this. The startup sweeper globs for it to
# remove uploads orphaned by a crash, so the prefix and the glob must agree -
# they are defined here, once, rather than written out at each call site where
# changing one and not the others would silently stop the sweep from finding
# anything.
STAGE_PREFIX = "labs-"


def stage_prefix(*parts: str) -> str:
    """Build a staging prefix that the orphan sweeper will match."""
    tail = "-".join(str(p) for p in parts if p)
    return f"{STAGE_PREFIX}{tail}-" if tail else STAGE_PREFIX


# Leading bytes for every container we accept. Offsets matter: ISO-BMFF
# (m4a/aac-in-mp4) puts a 4-byte box size before the 'ftyp' tag.
_MAGIC: Tuple[Tuple[bytes, int], ...] = (
    (b"RIFF", 0),      # wav
    (b"fLaC", 0),      # flac
    (b"OggS", 0),      # ogg, and opus which is Opus-in-Ogg
    (b"ID3", 0),       # mp3 with an ID3v2 tag
    (b"ftyp", 4),      # m4a / mp4 audio
    (b"\xff\xfb", 0),  # bare MPEG-1 Layer III frame headers
    (b"\xff\xfa", 0),
    (b"\xff\xf3", 0),
    (b"\xff\xf2", 0),
    (b"\xff\xf1", 0),  # AAC in ADTS
    (b"\xff\xf9", 0),
)


def looks_like_audio(head: bytes) -> bool:
    """True if `head` starts with a container signature we accept."""
    return any(head[off:off + len(sig)] == sig for sig, off in _MAGIC)


def unlink_quietly(path: str) -> None:
    """Remove a staged file, logging rather than raising if it will not go."""
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            log.warning("Could not remove staged upload %s", path)


def sweep_orphans(max_age_s: float = 3600) -> int:
    """Remove staged uploads a crash left behind. Called once at startup.

    Normal operation deletes each file in the job's cleanup callback, so
    anything older than `max_age_s` still sitting in the temp directory
    belongs to a process that died before it got there. Without this the
    container's disk fills one abandoned upload at a time.

    The age floor matters: an in-flight upload for a long analysis is on disk
    and legitimately in use, and a sweep with no cutoff would delete it out
    from under the running job.
    """
    import glob
    import time

    removed, cutoff = 0, time.time() - max_age_s
    pattern = os.path.join(tempfile.gettempdir(), f"{STAGE_PREFIX}*")
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("Swept %d orphaned staged upload(s)", removed)
    return removed


def stage_upload(file: UploadFile, prefix: str, settings=None) -> str:
    """Stream `file` to a temp path, enforcing type and size. Returns the path.

    Raises HTTPException with the API's error envelope on any rejection, and
    leaves nothing behind on disk when it does.

    `settings` is injectable, matching `authenticate()`. Callers that hold
    their own Settings instance are authoritative; falling back to a fresh
    get_settings() here would silently ignore a caller's configuration and
    re-read the process environment instead.
    """
    settings = settings or get_settings()

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in settings.audio.allowed_suffixes:
        raise api_error(
            415, "unsupported_media_type",
            f"Unsupported file type '{suffix or 'unknown'}'. Allowed: "
            f"{', '.join(settings.audio.allowed_suffixes)}.")

    limit = settings.audio.max_upload_bytes
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    written = 0
    checked = False
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                if not checked:
                    checked = True
                    # Refused on the first chunk, before the remainder is read
                    # or anything is written: a malformed 50 MB upload should
                    # cost one buffer, not a full disk write.
                    if not looks_like_audio(chunk[:16]):
                        raise api_error(
                            415, "unsupported_media_type",
                            "That file is not recognisable audio. Its contents "
                            "do not match any supported format, whatever the "
                            "extension says.")
                written += len(chunk)
                if written > limit:
                    raise api_error(
                        413, "payload_too_large",
                        f"File exceeds the {limit / 1e6:.0f} MB limit.")
                out.write(chunk)
    except Exception:
        unlink_quietly(path)
        raise

    if written == 0:
        unlink_quietly(path)
        raise api_error(400, "empty_file", "The uploaded file is empty.")
    return path
