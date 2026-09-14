"""Shared primitives for the tool layer.

Every tool measures the same audio in a different way, so the expensive part
(decoding, and the beat grid) is done once here and handed to whichever tool
asked for it. Tools call the measurement functions in app/analysis directly
rather than the path-based `analyse()` wrappers, because those each re-decode
the file and a two-file tool would otherwise decode six times.

The band layout is fixed and shared. Reference Match and Beat+Vocal Fit both
compare spectra, and their numbers are only comparable if the edges match.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Full-rate decode for anything loudness or stereo related; BS.1770 is defined
# on the delivered signal, so downsampling first would change the answer.
SR_FULL = 44100
# Musical analysis (chroma, onsets, tempo) is band-limited in practice and
# runs ~4x faster at this rate with no measurable change in the result.
SR_MUSICAL = 22050

# Analysis ceiling. Long-form uploads are truncated rather than rejected: the
# measurements below are all statistical over the programme, so ten minutes is
# already far more than enough to characterise a track.
MAX_SECONDS = 600.0

BANDS: Tuple[Tuple[str, str, float, float], ...] = (
    ("sub",       "Sub",         20.0,    60.0),
    ("bass",      "Bass",        60.0,   120.0),
    ("low_mid",   "Low mid",    120.0,   350.0),
    ("mid",       "Mid",        350.0,  2000.0),
    ("high_mid",  "High mid",  2000.0,  6000.0),
    ("presence",  "Presence",  6000.0, 10000.0),
    ("air",       "Air",      10000.0, 16000.0),
)


class ToolError(ValueError):
    """Input the caller can fix. Surfaces as a 422 with this message."""


def _f(x, nd: int = 2) -> Optional[float]:
    """Round for transport, turning non-finite values into None.

    JSON has no NaN or Infinity. Emitting them produces a body that strict
    parsers reject, so anything non-finite becomes null instead.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _i(x) -> Optional[int]:
    """Round to a whole number, for quantities that are counted, not measured.

    Tempo is the case this exists for. A BPM is reported to a musician who will
    type it into a DAW, and "128" is the answer to that question - "128.04" is
    the estimator's internal state leaking into the response. The extra digits
    are not precision either: beat-tracking resolution over a 3-minute track is
    nowhere near a hundredth of a BPM, so they encode noise and invite a reader
    to trust a difference that is not there.

    Genuinely measured quantities keep their decimals and use `_f`: LUFS, true
    peak, swing percentage and timing deviations are all read against real
    tolerances where a fraction changes the decision.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return int(round(v)) if math.isfinite(v) else None


def _db(x, floor: float = -120.0) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return floor
    return round(20.0 * math.log10(v), 2)


# --------------------------------------------------------------------------
# decoded audio
# --------------------------------------------------------------------------
@dataclass
class Decoded:
    """One decode, reused by every measurement a tool needs."""
    path: str
    stereo: np.ndarray          # [channels, n] at SR_FULL
    mono: np.ndarray            # [n] at SR_FULL
    musical: np.ndarray         # [n] at SR_MUSICAL
    sr: int = SR_FULL
    sr_musical: int = SR_MUSICAL
    duration: float = 0.0
    channels: int = 1
    _beats: Optional[List[float]] = field(default=None, repr=False)
    _beats_done: bool = field(default=False, repr=False)

    @property
    def is_stereo(self) -> bool:
        return self.channels > 1

    def beats(self) -> List[float]:
        """Beat grid, computed once and cached.

        librosa's tracker is used rather than the neural one from the detector:
        the tools must work with the detection model unloaded (mode=audio
        deployments, and any request that arrives while weights are still
        loading), and this keeps the tool layer independent of Stage-1/2.
        """
        if self._beats_done:
            return self._beats or []
        self._beats_done = True
        try:
            import librosa
            _, frames = librosa.beat.beat_track(
                y=self.musical, sr=self.sr_musical, units="frames")
            self._beats = [float(t) for t in librosa.frames_to_time(
                frames, sr=self.sr_musical)]
        except Exception:
            log.warning("Beat tracking failed for %s", self.path, exc_info=True)
            self._beats = []
        return self._beats or []


def decode(path: str) -> Decoded:
    """Load a file once at both rates the tools need."""
    import librosa

    try:
        y, sr = librosa.load(path, sr=SR_FULL, mono=False, duration=MAX_SECONDS)
    except Exception as exc:
        raise ToolError("That audio could not be decoded.") from exc

    if y.size == 0:
        raise ToolError("That audio file contains no samples.")
    if y.ndim == 1:
        y = y[np.newaxis, :]

    mono = y.mean(axis=0)
    duration = mono.size / float(sr)
    if duration < 1.0:
        raise ToolError(f"Audio is too short ({duration:.2f}s). Minimum is 1s.")

    musical = librosa.resample(mono, orig_sr=sr, target_sr=SR_MUSICAL)

    return Decoded(path=path, stereo=y, mono=mono, musical=musical,
                   duration=round(duration, 2), channels=int(y.shape[0]))


# --------------------------------------------------------------------------
# spectral bands
# --------------------------------------------------------------------------
def band_energy(mono: np.ndarray, sr: int,
                normalise: bool = True) -> Dict[str, Dict]:
    """Average energy per band, in dB.

    With `normalise` the overall level is removed, leaving only tonal balance.
    That is what makes two tracks comparable: without it a loud reference reads
    as "more of everything", which tells the user nothing they can act on.
    """
    import librosa

    spec = np.abs(librosa.stft(mono, n_fft=4096, hop_length=1024))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    power = (spec ** 2).mean(axis=1)

    total = float(power.sum()) or 1e-12
    out: Dict[str, Dict] = {}
    for key, label, lo, hi in BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        energy = float(power[sel].sum()) if sel.any() else 0.0
        share = energy / total
        out[key] = {
            "label": label,
            "range_hz": [lo, hi],
            "share_pct": _f(share * 100.0),
            "db": _db(math.sqrt(share) if normalise else math.sqrt(energy)),
        }
    return out


def band_delta(subject: Dict[str, Dict], reference: Dict[str, Dict]) -> List[Dict]:
    """Per-band difference in dB, subject minus reference.

    Positive means the subject has more energy there. Both inputs must come
    from `band_energy(normalise=True)` or the deltas measure level, not tone.
    """
    rows: List[Dict] = []
    for key, label, lo, hi in BANDS:
        s, r = subject.get(key, {}), reference.get(key, {})
        sd, rd = s.get("db"), r.get("db")
        if sd is None or rd is None:
            continue
        delta = sd - rd
        if abs(delta) < 1.0:
            verdict, severity = "matched", "ok"
        elif abs(delta) < 2.5:
            verdict = "slightly hot" if delta > 0 else "slightly light"
            severity = "minor"
        else:
            verdict = "too hot" if delta > 0 else "too light"
            severity = "major"
        rows.append({
            "band": key, "label": label, "range_hz": [lo, hi],
            "delta_db": _f(delta), "verdict": verdict, "severity": severity,
            "action": _band_action(label, delta),
        })
    return rows


def _band_action(label: str, delta: float) -> Optional[str]:
    if abs(delta) < 1.0:
        return None
    direction = "Cut" if delta > 0 else "Boost"
    return f"{direction} roughly {abs(delta):.1f} dB around the {label.lower()} band."


# --------------------------------------------------------------------------
# level matching
# --------------------------------------------------------------------------
def loudness_normalise(mono: np.ndarray, sr: int,
                       target_lufs: float = -18.0) -> np.ndarray:
    """Scale a signal to a fixed integrated loudness.

    Every cross-track comparison in this package runs on level-matched audio.
    Comparing a -6 LUFS master against a -14 LUFS rough mix without this step
    reports that the rough mix is quiet in all seven bands, which is true and
    useless.
    """
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sr)
        current = float(meter.integrated_loudness(mono))
        if not math.isfinite(current) or current < -70:
            return mono
        gain = 10.0 ** ((target_lufs - current) / 20.0)
        return mono * gain
    except Exception:
        log.debug("Loudness normalisation skipped", exc_info=True)
        return mono


# --------------------------------------------------------------------------
# tool registry
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    """One tool, as advertised by GET /v1/tools and used by the runner."""
    slug: str
    name: str
    summary: str
    inputs: Tuple[str, ...]              # form field names, in order
    scope: str = "analyze"
    typical_seconds: Tuple[int, int] = (5, 15)
    accuracy: str = ""
    basis: str = ""
    limitations: Tuple[str, ...] = ()

    @property
    def file_count(self) -> int:
        return len(self.inputs)


def params_fingerprint(tool: str, params: Dict) -> str:
    """Stable hash of the tool plus its options, for the result cache.

    Two runs of the same tool on the same audio may still differ if an option
    changed (a different target genre, a different reference), so the cache key
    has to cover the options and not just the file.
    """
    clean = {k: v for k, v in sorted(params.items()) if v is not None}
    blob = json.dumps({"tool": tool, "params": clean}, sort_keys=True,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def file_digest(path: str) -> str:
    """SHA-256 of a file, streamed so a 50 MB upload is not held in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
