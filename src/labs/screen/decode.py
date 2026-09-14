"""Level-1 audio front end.

Both Level-1 models were trained at 16 kHz, so ONE decode feeds both. The
reference implementation this is ported from decoded the file separately for
the fakeprint model, again for the CNN, and a third time for the bandwidth
screen. Three decodes of the same bytes is the single largest avoidable cost
in this tier, and it is why the reference measured ~20 s where this measures
seconds.

Nothing here imports torch. That is deliberate: the whole point of Level 1 is
that it is cheap enough to give away, and a tier that can ship without torch
is a ~250 MB container instead of a ~2 GB one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.config import ScreenConfig

log = logging.getLogger(__name__)

# Bandwidth analysis needs headroom above 8 kHz to find a ceiling at all, so it
# cannot share the 16 kHz decode - at 16 kHz every file appears to stop at
# Nyquist. It gets its own partial read, capped at this many seconds, which is
# cheap because it never touches the rest of the file.
BANDWIDTH_SR = 44100
BANDWIDTH_SECONDS = 90.0


@dataclass
class ScreenAudio:
    """One decode, shared by every Level-1 consumer."""
    y: np.ndarray                    # mono float32 @ cfg.sample_rate
    sr: int
    duration: float                  # seconds actually decoded
    full_duration: Optional[float]   # seconds in the file, if known


def load(path: str, cfg: ScreenConfig) -> ScreenAudio:
    """Decode to mono float32 at the model rate, truncated to the cap.

    Truncation matches the reference: it reads at most `max_duration_s`, which
    bounds the cost of the STFT on a long file. The fakeprint is a time-average
    so extending the window past a few minutes changes it very little.
    """
    import librosa

    y, sr = librosa.load(path, sr=cfg.sample_rate, mono=True,
                         duration=cfg.max_duration_s)
    y = np.ascontiguousarray(y, dtype=np.float32)
    if y.size == 0:
        raise ValueError("Decoded audio is empty.")

    full = None
    try:
        full = float(librosa.get_duration(path=path))
    except Exception:
        log.debug("Could not probe full duration for %s", path, exc_info=True)

    return ScreenAudio(y=y, sr=int(sr), duration=y.size / float(sr),
                       full_duration=full)


def load_wideband(path: str) -> Optional[tuple[np.ndarray, int]]:
    """A short high-rate read for the bandwidth screen, or None if it fails.

    Best effort by design. The bandwidth ceiling is explanatory evidence that
    the policy weights lightly, so it must never be able to fail a request.
    """
    try:
        import librosa

        y, sr = librosa.load(path, sr=BANDWIDTH_SR, mono=True,
                             duration=BANDWIDTH_SECONDS)
        if y.size < BANDWIDTH_SR:
            return None
        return np.ascontiguousarray(y, dtype=np.float32), int(sr)
    except Exception:
        log.debug("Wideband read failed for %s", path, exc_info=True)
        return None


def probe(path: str) -> dict:
    """ffprobe metadata for the container screen.

    Encoder strings and tag sparsity are forensically useful and cost nothing,
    but ffprobe may be absent, so an empty dict is a supported answer.
    """
    import json
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {}
        data = json.loads(out.stdout or "{}")
    except Exception:
        log.debug("ffprobe unavailable for %s", path, exc_info=True)
        return {}

    stream = next((s for s in data.get("streams", [])
                   if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    tags = {**(fmt.get("tags") or {}), **(stream.get("tags") or {})}
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0) or None,
        "channels": stream.get("channels"),
        "bit_rate": int(stream.get("bit_rate") or fmt.get("bit_rate") or 0) or None,
        "duration": float(fmt.get("duration") or stream.get("duration") or 0) or None,
        "format_name": fmt.get("format_name"),
        "encoder": tags.get("encoder") or tags.get("ENCODER") or tags.get("TSSE"),
        # Values are truncated because they reach a JSON response and a tag can
        # legally carry kilobytes of lyrics or an embedded image comment.
        "tags": {k: str(v)[:200] for k, v in tags.items()},
    }
