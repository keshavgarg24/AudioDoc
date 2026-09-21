"""Signal-level and musicological analysis of a track.

This runs alongside the neural verdict and is deliberately independent of it:
it measures properties an engineer or A&R listener would name, in their terms -
brightness, band balance, crest factor, stereo width, key, harmonic/percussive
balance, onset density.

Several of these are diagnostic for synthetic audio in their own right:

  * **High-frequency ceiling.** Many generative vocoders and the codecs they are
    trained on roll off hard around 16 kHz. A sharp cliff well below Nyquist is
    a strong synthetic tell; natural recordings taper.
  * **Loudness uniformity.** Generated material tends to hold a near-constant
    short-term level with little macro-dynamic movement.
  * **Stereo width.** Mono-ish or artificially wide fields are common in
    generated output, where real productions place sources deliberately.
  * **Spectral flatness.** Vocoder artefacts raise noise-likeness in bands that
    should be tonal.

None of these is proof on its own - each is reported with the caveat that
genre and mastering explain many of the same numbers.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from .decode import AudioCache

log = logging.getLogger(__name__)

# Analysis sample rate. 22.05 kHz gives an 11.025 kHz Nyquist, which is too low
# to see the 16 kHz ceiling, so the band/ceiling work uses ANALYSIS_SR_HIGH.
ANALYSIS_SR = 22050
ANALYSIS_SR_HIGH = 44100
# Bound the excerpt so a 10-minute upload doesn't dominate request time.
MAX_ANALYSIS_SECONDS = 240

# Standard mixing bands (Hz). Names match how engineers actually refer to them.
BANDS = [
    ("Sub",        20,    60),
    ("Bass",       60,    250),
    ("Low mid",    250,   500),
    ("Mid",        500,   2000),
    ("High mid",   2000,  6000),
    ("Presence",   6000,  12000),
    ("Air",        12000, 20000),
]

PITCH_CLASSES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]

# Krumhansl-Schmuckler key profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _f(x, nd: int = 4) -> float:
    v = float(x)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(v, nd)


def _db(x, floor: float = -120.0) -> float:
    v = float(x)
    if v <= 1e-12:
        return floor
    return round(max(floor, 20.0 * math.log10(v)), 2)


# --------------------------------------------------------------------------
def _spectral(y: np.ndarray, sr: int) -> Dict:
    import librosa

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    rolloff85 = librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.85)[0]
    rolloff95 = librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.95)[0]
    bandwidth = librosa.feature.spectral_bandwidth(S=S, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(S=S)[0]
    flux = np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))

    # Long-term average spectrum, normalised to its own peak.
    lts = S.mean(axis=1)
    lts_db = 20 * np.log10(np.maximum(lts, 1e-10) / max(lts.max(), 1e-10))
    nyquist = sr / 2.0

    # Ceiling = the 99.5% energy rolloff. Referencing the peak instead would
    # just measure the natural bass-heavy spectral tilt: music sits 40 dB below
    # its bass peak by a few kHz, which is not a band limit.
    ceiling = float(np.median(
        librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.995)[0]))

    # Cliff: dB drop across the octave straddling the ceiling. A codec or
    # vocoder brick wall drops tens of dB; a natural taper only a few.
    def level_at(f0: float) -> float:
        sel = (freqs >= f0 * 0.92) & (freqs <= f0 * 1.08)
        return float(lts_db[sel].mean()) if sel.any() else -120.0

    below, above_c = level_at(ceiling * 0.85), level_at(min(ceiling * 1.25, nyquist * 0.99))
    cliff = below - above_c

    # Energy per mixing band, as a share of total.
    total = float(np.sum(lts ** 2)) or 1e-12
    bands = []
    for name, lo, hi_f in BANDS:
        sel = (freqs >= lo) & (freqs < min(hi_f, nyquist))
        share = float(np.sum(lts[sel] ** 2) / total) if sel.any() else 0.0
        bands.append({
            "name": name, "low": lo, "high": hi_f,
            "share": _f(share), "db": _db(math.sqrt(share) if share > 0 else 0.0),
        })

    return {
        "centroid_hz": _f(float(centroid.mean()), 1),
        "centroid_std": _f(float(centroid.std()), 1),
        "centroid_series": [_f(v, 1) for v in _downsample(centroid, 160)],
        "rolloff85_hz": _f(float(rolloff85.mean()), 1),
        "rolloff95_hz": _f(float(rolloff95.mean()), 1),
        "bandwidth_hz": _f(float(bandwidth.mean()), 1),
        # Raw flatness is a power ratio in the 1e-5..1e-1 range for music, which
        # is unreadable and impossible to threshold on directly. The dB form is
        # what gets interpreted; typical music lands between -50 and -15 dB.
        "flatness": _f(float(flatness.mean()), 6),
        "flatness_db": _f(10 * math.log10(max(float(flatness.mean()), 1e-12)), 2),
        "flatness_std": _f(float(flatness.std()), 6),
        "flux": _f(float(flux.mean()), 3),
        "flux_std": _f(float(flux.std()), 3),
        "ceiling_hz": _f(ceiling, 1),
        "nyquist_hz": _f(nyquist, 1),
        "ceiling_ratio": _f(ceiling / nyquist if nyquist else 0.0),
        "rolloff_cliff_db": _f(cliff, 1),
        "bands": bands,
        "spectrum": _spectrum_curve(freqs, lts_db, sr),
    }


def _spectrum_curve(freqs: np.ndarray, lts_db: np.ndarray, sr: int) -> List[Dict]:
    """Long-term average spectrum on log-spaced points, for plotting."""
    lo, hi = 20.0, min(sr / 2.0, 20000.0)
    pts = np.logspace(np.log10(lo), np.log10(hi), 96)
    out = []
    for f0 in pts:
        i = int(np.argmin(np.abs(freqs - f0)))
        out.append({"hz": _f(f0, 1), "db": _db(10 ** (lts_db[i] / 20.0))})
    return out


def _downsample(arr: np.ndarray, n: int) -> np.ndarray:
    if arr.size <= n:
        return arr
    idx = np.linspace(0, arr.size - 1, n).astype(int)
    return arr[idx]


# --------------------------------------------------------------------------
def _dynamics(y: np.ndarray, sr: int) -> Dict:
    import librosa

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rms_db = 20 * np.log10(np.maximum(rms, 1e-10))
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms_overall = float(np.sqrt(np.mean(y ** 2))) if y.size else 0.0

    # Crest factor: peak-to-RMS. Low == heavily limited ("loudness war").
    crest = _db(peak / rms_overall) if rms_overall > 0 else 0.0

    # DR-style measure: spread between loud and quiet passages.
    loud = float(np.percentile(rms_db, 95))
    quiet = float(np.percentile(rms_db, 10))
    dr = loud - quiet

    # Samples at or above full scale - a mastering/clipping indicator.
    clipped = float(np.mean(np.abs(y) >= 0.999)) if y.size else 0.0

    return {
        "peak_db": _db(peak),
        "rms_db": _db(rms_overall),
        "crest_factor_db": _f(crest, 2),
        "dynamic_range_db": _f(dr, 2),
        "loudness_std_db": _f(float(rms_db.std()), 2),
        "clipped_ratio": _f(clipped, 6),
        "rms_series": [_f(v, 2) for v in _downsample(rms_db, 160)],
        "rms_percentiles": {
            "p10": _f(quiet, 2), "p50": _f(float(np.percentile(rms_db, 50)), 2),
            "p95": _f(loud, 2),
        },
    }


# --------------------------------------------------------------------------
def _tonal(y: np.ndarray, sr: int, energies: Optional[tuple] = None,
           chroma: Optional[np.ndarray] = None) -> Dict:
    import librosa

    if chroma is None:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    profile = profile / (profile.sum() or 1.0)

    # Krumhansl-Schmuckler: correlate the chroma profile against all 24 keys.
    best, best_r, scores = None, -2.0, []
    for mode, ref in (("major", _MAJOR), ("minor", _MINOR)):
        for shift in range(12):
            r = float(np.corrcoef(profile, np.roll(ref, shift))[0, 1])
            scores.append(r)
            if r > best_r:
                best_r, best = r, (PITCH_CLASSES[shift], mode)
    scores.sort(reverse=True)
    # Gap to the runner-up: how unambiguous the key is.
    clarity = float(scores[0] - scores[1]) if len(scores) > 1 else 0.0

    # Energies come from the shared separation in `decode.AudioCache` when the
    # caller supplies one. Only the ratio is reported, and a ratio of spectral
    # energies equals a ratio of time-domain energies (Parseval), so the
    # inverse transform this used to pay for was never needed here.
    if energies is not None:
        h_energy, p_energy = energies
    else:
        harmonic, percussive = librosa.effects.hpss(y)
        h_energy = float(np.sum(harmonic ** 2))
        p_energy = float(np.sum(percussive ** 2))
    total = h_energy + p_energy or 1e-12

    return {
        "key": f"{best[0]} {best[1]}" if best else "unknown",
        "key_correlation": _f(best_r),
        "key_clarity": _f(clarity),
        "chroma": [{"pitch": PITCH_CLASSES[i], "weight": _f(float(profile[i]))}
                   for i in range(12)],
        "harmonic_ratio": _f(h_energy / total),
        "percussive_ratio": _f(p_energy / total),
    }


# --------------------------------------------------------------------------
def _onsets(y: np.ndarray, sr: int, duration: float) -> Dict:
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr)
    times = librosa.frames_to_time(frames, sr=sr)

    if times.size > 1:
        gaps = np.diff(times)
        regularity = float(1.0 - min(1.0, gaps.std() / (gaps.mean() or 1e-9)))
    else:
        gaps, regularity = np.array([]), 0.0

    return {
        "onset_count": int(times.size),
        "onset_density": _f(times.size / duration if duration else 0.0, 3),
        "onset_regularity": _f(max(0.0, regularity)),
        "onset_strength_mean": _f(float(env.mean()), 3),
        "mean_gap_s": _f(float(gaps.mean()) if gaps.size else 0.0, 3),
    }


# --------------------------------------------------------------------------
def _stereo(y_stereo: np.ndarray) -> Dict:
    """Mid/side balance. Computed before the mono downmix the model uses."""
    if y_stereo.ndim < 2 or y_stereo.shape[0] < 2:
        return {"is_stereo": False, "width": 0.0, "correlation": 1.0,
                "side_energy_db": -120.0}

    left, right = y_stereo[0], y_stereo[1]
    mid = (left + right) / 2.0
    side = (left - right) / 2.0
    m_e = float(np.sqrt(np.mean(mid ** 2)))
    s_e = float(np.sqrt(np.mean(side ** 2)))
    corr = float(np.corrcoef(left, right)[0, 1]) if left.size > 1 else 1.0

    return {
        "is_stereo": True,
        # 0 == mono, 1 == fully decorrelated.
        "width": _f(s_e / (m_e + s_e) if (m_e + s_e) > 0 else 0.0),
        "correlation": _f(corr if not math.isnan(corr) else 1.0),
        "side_energy_db": _db(s_e),
        "mid_energy_db": _db(m_e),
    }


# --------------------------------------------------------------------------
def extract(path: str, cache: Optional["AudioCache"] = None) -> Dict:
    """Full signal analysis. Returns {} if the file can't be analysed.

    `cache` shares the decode and the harmonic/percussive split with the other
    passes of the same request; without one this pass still works standalone
    and simply does the work itself.
    """
    from .decode import cache_for

    cache = cache_for(path, cache)
    try:
        # Stereo at high rate: needed for both width and the HF ceiling.
        y_hi, sr_hi = cache.audio(ANALYSIS_SR_HIGH, mono=False,
                                  duration=MAX_ANALYSIS_SECONDS)
        if y_hi.ndim == 1:
            y_hi = y_hi[np.newaxis, :]

        stereo = _stereo(y_hi)
        mono_hi = y_hi.mean(axis=0)
        duration = mono_hi.size / sr_hi

        spectral = _spectral(mono_hi, sr_hi)
        dynamics = _dynamics(mono_hi, sr_hi)

        # Tonal/onset work is cheaper and adequate at the lower rate. Taken
        # from the cache so it is the same array `musical` separates, rather
        # than a second resampling of the same signal.
        mono_lo, _ = cache.audio(ANALYSIS_SR, mono=True,
                                 duration=MAX_ANALYSIS_SECONDS)
        _, h_energy, p_energy = cache.split(ANALYSIS_SR, MAX_ANALYSIS_SECONDS)
        tonal = _tonal(mono_lo, ANALYSIS_SR, energies=(h_energy, p_energy),
                       chroma=cache.chroma(ANALYSIS_SR, MAX_ANALYSIS_SECONDS))
        onsets = _onsets(mono_lo, ANALYSIS_SR, duration)

        return {
            "spectral": spectral,
            "dynamics": dynamics,
            "tonal": tonal,
            "onsets": onsets,
            "stereo": stereo,
            "analysed_seconds": _f(duration, 2),
        }
    except Exception:
        log.exception("Signal analysis failed for %s", path)
        return {}
