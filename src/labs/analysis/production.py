"""Mastering, delivery and encoding analysis.

Answers the questions an engineer asks before release: how loud is it against
each platform's target, does it clip after lossy encoding, is the low end mono
safe, and what has already been done to the file.

Everything here is measured on the full-length signal, not an excerpt, because
integrated loudness and true peak are defined over the whole programme.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from .decode import AudioCache

log = logging.getLogger(__name__)

# EBU R128 / streaming normalisation targets, in LUFS.
PLATFORM_TARGETS = [
    ("Spotify", -14.0), ("Apple Music", -16.0), ("YouTube", -14.0),
    ("Tidal", -14.0), ("Amazon Music", -14.0), ("Deezer", -15.0),
    ("Club / DJ", -9.0),
]
# Lossy codecs overshoot on decode, so masters are kept below this.
TRUE_PEAK_CEILING_DBTP = -1.0


def _f(x, nd: int = 3) -> float:
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
def loudness(y_stereo: np.ndarray, sr: int) -> Dict:
    """ITU-R BS.1770 integrated loudness, LRA, and true peak."""
    import pyloudnorm as pyln

    data = y_stereo.T if y_stereo.ndim > 1 else y_stereo
    try:
        meter = pyln.Meter(sr)
        integrated = float(meter.integrated_loudness(data))
    except Exception:
        log.exception("Loudness metering failed")
        return {}
    if math.isinf(integrated):
        integrated = -70.0

    mono = y_stereo.mean(axis=0) if y_stereo.ndim > 1 else y_stereo

    # Short-term (3 s) and momentary (400 ms) windows, hopped by 100 ms.
    def windowed(win_s: float) -> np.ndarray:
        win = int(win_s * sr)
        hop = max(1, int(0.1 * sr))
        if mono.size < win:
            return np.array([])
        out = []
        for i in range(0, mono.size - win, hop):
            seg = mono[i:i + win]
            ms = float(np.mean(seg ** 2))
            out.append(-0.691 + 10 * math.log10(ms) if ms > 1e-12 else -70.0)
        return np.array(out)

    st = windowed(3.0)
    mo = windowed(0.4)

    # Loudness range: 10th to 95th percentile of gated short-term values.
    if st.size:
        gated = st[st > (integrated - 20)]
        lra = float(np.percentile(gated, 95) - np.percentile(gated, 10)) if gated.size else 0.0
    else:
        lra = 0.0

    # True peak via 4x oversampling, which exposes intersample peaks.
    try:
        import scipy.signal as ss
        up = ss.resample_poly(mono, 4, 1)
        true_peak = float(np.max(np.abs(up)))
    except Exception:
        true_peak = float(np.max(np.abs(mono))) if mono.size else 0.0

    sample_peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(mono ** 2))) if mono.size else 0.0

    tp_db = _db(true_peak)
    plr = _f(tp_db - integrated, 2)

    targets = []
    for name, target in PLATFORM_TARGETS:
        delta = integrated - target
        if abs(delta) <= 1.0:
            verdict = "on target"
        elif delta > 0:
            verdict = f"{abs(delta):.1f} LU too loud"
        else:
            verdict = f"{abs(delta):.1f} LU too quiet"
        targets.append({"platform": name, "target_lufs": target,
                        "delta_lu": _f(delta, 2), "verdict": verdict})

    return {
        "integrated_lufs": _f(integrated, 2),
        "short_term_max_lufs": _f(float(st.max()) if st.size else integrated, 2),
        "momentary_max_lufs": _f(float(mo.max()) if mo.size else integrated, 2),
        "loudness_range_lu": _f(lra, 2),
        "true_peak_dbtp": tp_db,
        "sample_peak_db": _db(sample_peak),
        "rms_db": _db(rms),
        "plr_db": plr,
        "compliant_minus1_dbtp": bool(tp_db <= TRUE_PEAK_CEILING_DBTP),
        "clipped_samples": int(np.sum(np.abs(mono) >= 0.999)) if mono.size else 0,
        "platform_targets": targets,
        "short_term_series": [_f(v, 2) for v in _thin(st, 160)],
    }


def _thin(arr: np.ndarray, n: int) -> np.ndarray:
    if arr.size == 0:
        return arr
    if arr.size <= n:
        return arr
    return arr[np.linspace(0, arr.size - 1, n).astype(int)]


# --------------------------------------------------------------------------
def stereo_field(y_stereo: np.ndarray, sr: int) -> Dict:
    """Phase, width, and mono compatibility, including per-band width."""
    if y_stereo.ndim < 2 or y_stereo.shape[0] < 2:
        return {"is_stereo": False}

    import librosa

    left, right = y_stereo[0], y_stereo[1]
    mid = (left + right) / 2.0
    side = (left - right) / 2.0

    m_rms = float(np.sqrt(np.mean(mid ** 2)))
    s_rms = float(np.sqrt(np.mean(side ** 2)))
    corr = float(np.corrcoef(left, right)[0, 1]) if left.size > 1 else 1.0
    if math.isnan(corr):
        corr = 1.0

    l_rms = float(np.sqrt(np.mean(left ** 2)))
    r_rms = float(np.sqrt(np.mean(right ** 2)))
    balance_db = _db(l_rms) - _db(r_rms)

    # Per-band width: bass should be near-mono on a well-built master.
    bands = [("Sub", 20, 60), ("Bass", 60, 250), ("Low mid", 250, 500),
             ("Mid", 500, 2000), ("High mid", 2000, 6000),
             ("Presence", 6000, 12000), ("Air", 12000, 20000)]
    Sm = np.abs(librosa.stft(mid, n_fft=2048, hop_length=1024))
    Ss = np.abs(librosa.stft(side, n_fft=2048, hop_length=1024))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    band_width = []
    low_mono = True
    for name, lo, hi in bands:
        sel = (freqs >= lo) & (freqs < min(hi, sr / 2))
        if not sel.any():
            continue
        m_e = float(np.mean(Sm[sel] ** 2))
        s_e = float(np.mean(Ss[sel] ** 2))
        w = s_e / (m_e + s_e) if (m_e + s_e) > 0 else 0.0
        band_width.append({"name": name, "low": lo, "high": hi, "width": _f(w)})
        if name in ("Sub", "Bass") and w > 0.15:
            low_mono = False

    width = s_rms / (m_rms + s_rms) if (m_rms + s_rms) > 0 else 0.0

    if corr > 0.9:
        verdict = "in phase, mono safe"
    elif corr > 0.4:
        verdict = "moderately wide, mono safe"
    elif corr > 0.0:
        verdict = "very wide, check mono fold-down"
    else:
        verdict = "out of phase, will cancel in mono"

    return {
        "is_stereo": True,
        "correlation": _f(corr, 4),
        "verdict": verdict,
        "mid_side_ratio": _f(s_rms / m_rms if m_rms > 0 else 0.0, 4),
        "width": _f(width),
        "width_pct": _f(width * 100, 1),
        "channel_balance_db": _f(balance_db, 2),
        "mono_compatible": bool(corr > 0.2),
        "low_end_mono": bool(low_mono),
        "mid_energy_db": _db(m_rms),
        "side_energy_db": _db(s_rms),
        "band_width": band_width,
    }


# --------------------------------------------------------------------------
def encoding(path: str, sr: int, mono: np.ndarray) -> Dict:
    """Container, declared bitrate, and lowpass shelf.

    A lowpass shelf well below Nyquist is the fingerprint of a lossy codec. If
    the container claims a high bitrate but the shelf sits low, the file was
    very likely transcoded up from something worse.
    """
    out: Dict = {"container_format": os.path.splitext(path)[1].lstrip(".").upper()}

    if shutil.which("ffprobe"):
        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                meta = json.loads(proc.stdout)
                fmt = meta.get("format", {})
                stream = next((s for s in meta.get("streams", [])
                               if s.get("codec_type") == "audio"), {})
                out.update({
                    "container_format": (fmt.get("format_name") or "").split(",")[0].upper(),
                    "codec": stream.get("codec_name"),
                    "codec_long_name": stream.get("codec_long_name"),
                    "declared_bitrate_bps": int(fmt.get("bit_rate") or 0) or None,
                    "source_sample_rate_hz": int(stream.get("sample_rate") or 0) or None,
                    "channels": stream.get("channels"),
                    "encoder": (fmt.get("tags") or {}).get("encoder"),
                })
        except Exception:
            log.debug("ffprobe failed", exc_info=True)

    # Locate the lowpass shelf: the highest frequency holding real energy.
    try:
        import librosa
        S = np.abs(librosa.stft(mono, n_fft=4096, hop_length=2048))
        lts = S.mean(axis=1)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
        lts_db = 20 * np.log10(np.maximum(lts, 1e-10) / max(lts.max(), 1e-10))

        # Reference to the 1-5 kHz plateau rather than the bass peak.
        ref_sel = (freqs >= 1000) & (freqs <= 5000)
        ref = float(lts_db[ref_sel].mean()) if ref_sel.any() else 0.0
        above = np.where(lts_db > (ref - 35))[0]
        shelf = float(freqs[above[-1]]) if above.size else float(sr / 2)

        hi_idx = min(len(freqs) - 1, (above[-1] if above.size else 0) + 30)
        drop = float(lts_db[above[-1]] - lts_db[hi_idx]) if above.size else 0.0

        out["lowpass_shelf_hz"] = _f(shelf, 1)
        out["shelf_drop_db"] = _f(drop, 2)

        # Map the shelf onto the codec that typically produces it.
        if shelf >= 19500:
            inferred = "lossless or very high bitrate"
        elif shelf >= 18500:
            inferred = "MP3 320 kbps or AAC 256 kbps"
        elif shelf >= 16000:
            inferred = "MP3 192 to 256 kbps"
        elif shelf >= 15000:
            inferred = "MP3 128 kbps or AAC 128 kbps"
        else:
            inferred = "heavily compressed, below 128 kbps equivalent"
        out["inferred_encoding_history"] = inferred

        declared = out.get("declared_bitrate_bps") or 0
        # High declared bitrate plus a low shelf means the bits are wasted on
        # material a previous encode already discarded.
        suspect = bool(declared >= 256000 and shelf < 17500)
        out["transcode_suspected"] = suspect
        out["transcode_note"] = (
            "declared bitrate is high but bandwidth is limited, so the source "
            "was probably re-encoded from a lower quality file"
            if suspect else "no contradiction between container and bandwidth")
    except Exception:
        log.debug("Shelf detection failed", exc_info=True)

    return out


# --------------------------------------------------------------------------
def sound_design(mono: np.ndarray, sr: int) -> Dict:
    """Reverb tail, harmonic distortion, and slow modulation."""
    import librosa
    import scipy.signal as ss

    out: Dict = {}

    # RT60 from the decay slope after the loudest onset.
    try:
        env = librosa.onset.onset_strength(y=mono, sr=sr)
        if env.size > 10:
            peak = int(np.argmax(env))
            tail = env[peak:peak + 200]
            if tail.size > 20:
                tail_db = 20 * np.log10(np.maximum(tail, 1e-10) / max(tail.max(), 1e-10))
                below = np.where(tail_db < -20)[0]
                if below.size:
                    frames = int(below[0])
                    t20 = frames * 512 / sr
                    rt60 = t20 * 3.0
                    out["reverb_rt60_ms"] = _f(rt60 * 1000, 1)
                    out["reverb_character"] = (
                        "dry, tight space" if rt60 < 0.3 else
                        "room" if rt60 < 0.8 else
                        "hall or large space" if rt60 < 2.0 else
                        "very long tail, cathedral or heavy send")
    except Exception:
        log.debug("RT60 estimation failed", exc_info=True)

    # THD proxy: energy at harmonics of the strongest partial.
    try:
        seg = mono[:sr * 20] if mono.size > sr * 20 else mono
        spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
        freqs = np.fft.rfftfreq(seg.size, 1 / sr)
        band = (freqs > 50) & (freqs < 1000)
        if band.any():
            f0 = float(freqs[band][int(np.argmax(spec[band]))])
            fundamental = float(spec[np.argmin(np.abs(freqs - f0))])
            harmonics = 0.0
            for k in range(2, 7):
                fk = f0 * k
                if fk < sr / 2:
                    harmonics += float(spec[np.argmin(np.abs(freqs - fk))]) ** 2
            thd = math.sqrt(harmonics) / fundamental if fundamental > 0 else 0.0
            out["thd_ratio"] = _f(min(thd, 10.0), 4)
            out["saturation"] = (
                "clean" if thd < 0.1 else
                "lightly saturated" if thd < 0.3 else
                "driven" if thd < 0.8 else "heavily distorted")
    except Exception:
        log.debug("THD estimation failed", exc_info=True)

    # Slow amplitude modulation: tremolo, sidechain pumping, wow and flutter.
    try:
        rms = librosa.feature.rms(y=mono, hop_length=512)[0]
        if rms.size > 64:
            centred = rms - rms.mean()
            frame_rate = sr / 512
            f, pxx = ss.periodogram(centred, fs=frame_rate)
            keep = (f > 0.1) & (f < 12)
            if keep.any():
                fk, pk = f[keep], pxx[keep]
                top = np.argsort(pk)[::-1][:4]
                out["modulation_hz"] = [_f(float(fk[i]), 3) for i in top
                                        if pk[i] > pk.mean() * 2]
    except Exception:
        log.debug("Modulation detection failed", exc_info=True)

    return out


# --------------------------------------------------------------------------
def analyse(path: str, cache: Optional["AudioCache"] = None) -> Dict:
    """Full production pass. Returns {} if the file cannot be analysed.

    Loudness and true peak are measured on the whole file, since both are
    defined over the entire programme rather than an excerpt - so this pass
    alone asks the cache for an uncapped decode.

    `cache` shares that decode with the other passes of the same request.
    """
    from .decode import cache_for

    cache = cache_for(path, cache)
    try:
        y, sr = cache.audio(44100, mono=False)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        mono = y.mean(axis=0)

        loud = loudness(y, sr)
        stereo = stereo_field(y, sr)
        return {
            "loudness": loud,
            "stereo": stereo,
            "encoding": encoding(path, sr, mono),
            "sound_design": sound_design(mono, sr),
            "release_readiness": release_readiness(loud, stereo),
        }
    except Exception:
        log.exception("Production analysis failed for %s", path)
        return {}


def release_readiness(loud: Dict, stereo: Dict) -> Dict:
    """Concrete blockers between this file and a clean release."""
    issues: List[str] = []
    if not loud:
        return {"status": "unknown", "issues": ["Loudness metering unavailable."]}

    tp = loud.get("true_peak_dbtp", -1.0)
    if tp > TRUE_PEAK_CEILING_DBTP:
        issues.append(
            f"True peak {tp:.2f} dBTP exceeds the {TRUE_PEAK_CEILING_DBTP:.0f} dBTP "
            f"ceiling, so it can distort after lossy encoding.")
    if loud.get("clipped_samples", 0) > 0:
        issues.append(f"{loud['clipped_samples']} samples sit at full scale.")

    lufs = loud.get("integrated_lufs", -14)
    if lufs > -8:
        issues.append(f"Integrated loudness {lufs:.1f} LUFS is very hot and will "
                      f"be turned down by every streaming platform.")
    elif lufs < -20:
        issues.append(f"Integrated loudness {lufs:.1f} LUFS is quiet against "
                      f"streaming targets.")

    lra = loud.get("loudness_range_lu", 0)
    if lra < 3:
        issues.append(f"Loudness range {lra:.1f} LU is very compressed, so the "
                      f"arrangement has little dynamic movement.")

    if stereo.get("is_stereo"):
        if not stereo.get("mono_compatible", True):
            issues.append("Phase correlation is low, so the mix loses content "
                          "when folded to mono.")
        if not stereo.get("low_end_mono", True):
            issues.append("Low end carries significant side energy, which is "
                          "unstable on club systems and vinyl.")

    return {
        "status": "ready" if not issues else "needs work",
        "issues": issues or ["No delivery blockers detected."],
    }
