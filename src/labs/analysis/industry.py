"""Industry facing analysis: catalogue features and marketplace QC.

Two audiences:

  * A&R, sync and playlist teams work in the Spotify style vocabulary
    (energy, danceability, valence, acousticness, instrumentalness,
    speechiness, liveness). Those are ESTIMATES derived from measurements in
    this pipeline, not values fetched from Spotify, and are labelled as such.

  * A beats marketplace needs upload QC: is it silent at the head, is it
    clipped, is it mono, is there a producer voice tag, is it loud enough to
    sit beside the rest of the catalogue.

None of this touches the AI probability. It is additive metadata only.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)


def _f(x, nd: int = 3) -> float:
    v = float(x)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(v, nd)


def _c(x) -> float:
    return _f(max(0.0, min(1.0, float(x))))


def _scale(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _c((v - lo) / (hi - lo))


# --------------------------------------------------------------------------
def catalogue_features(features: Dict, musical: Dict, production: Dict) -> Dict:
    """Spotify style audio features, estimated from local measurements."""
    sp = features.get("spectral", {})
    dy = features.get("dynamics", {})
    tn = features.get("tonal", {})
    on = features.get("onsets", {})
    rh = musical.get("rhythm", {})
    gr = musical.get("groove", {})
    sd = production.get("sound_design", {})
    loud = production.get("loudness", {})

    bands = {b["name"]: b["share"] for b in sp.get("bands", [])}
    low = bands.get("Sub", 0) + bands.get("Bass", 0)

    bpm = rh.get("bpm") or 120
    density = on.get("onset_density", 2)
    centroid = sp.get("centroid_hz", 2000)
    crest = dy.get("crest_factor_db", 12)
    lufs = loud.get("integrated_lufs", -14)
    harmonic = tn.get("harmonic_ratio", 0.6)
    percussive = tn.get("percussive_ratio", 0.4)
    minor = musical.get("harmony", {}).get("mode") == "minor"

    # Energy: loudness plus spectral drive.
    energy = _c(_scale(lufs, -24, -6) * 0.45
                + _scale(centroid, 700, 4200) * 0.25
                + _scale(density, 0.6, 6) * 0.30)

    # Danceability: a steady tempo in the danceable band, strong beat, and a
    # tight grid all contribute.
    tempo_fit = 1.0 - min(1.0, abs(bpm - 120) / 60.0)
    steady = 1.0 if rh.get("is_constant_tempo") else 0.55
    grid_tight = 1.0 - _scale(gr.get("mean_abs_deviation_ms", 20), 0, 45)
    danceability = _c(tempo_fit * 0.35 + steady * 0.2
                      + grid_tight * 0.2 + _c(percussive * 2) * 0.25)

    # Valence: brightness and mode carry most of perceived positivity.
    valence = _c(_scale(centroid, 600, 4000) * 0.5
                 + (0.15 if not minor else 0.0)
                 + _scale(bpm, 70, 150) * 0.2
                 + (1 - _c(low)) * 0.15)

    # Acousticness: wide dynamics, natural high end, low distortion.
    acoustic = _c(_scale(crest, 8, 20) * 0.4
                  + _c(harmonic) * 0.3
                  + (1 - _c(float(sd.get("thd_ratio", 0.2)) * 2)) * 0.3)

    # Instrumentalness: vocals live in the midrange, so a quiet midrange with
    # little speech-like modulation reads as instrumental.
    mid = bands.get("Mid", 0) + bands.get("High mid", 0) * 0.5
    instrumentalness = _c(1.0 - _scale(mid, 0.05, 0.45))

    # Speechiness: dense onsets with a low harmonic ratio in the vocal band.
    speechiness = _c(_scale(density, 3, 9) * 0.5 * (1 - _c(harmonic)))

    # Liveness: a long tail plus loose timing suggests a room and a performance.
    rt60 = float(sd.get("reverb_rt60_ms", 0)) / 1000.0
    liveness = _c(_scale(rt60, 0.3, 2.5) * 0.5
                  + _scale(gr.get("mean_abs_deviation_ms", 0), 10, 45) * 0.5)

    return {
        "estimated": True,
        "note": ("Derived from local measurements in this pipeline. These are "
                 "estimates in the familiar catalogue vocabulary, not values "
                 "retrieved from any streaming provider."),
        "energy": energy,
        "danceability": danceability,
        "valence": valence,
        "acousticness": acoustic,
        "instrumentalness": instrumentalness,
        "speechiness": speechiness,
        "liveness": liveness,
        # Already a whole number from `musical.rhythm`; passed through rather
        # than re-rounded so it cannot drift back into a float here.
        "tempo_bpm": bpm,
        "key": musical.get("harmony", {}).get("key"),
        "camelot": musical.get("harmony", {}).get("camelot"),
        "mode": musical.get("harmony", {}).get("mode"),
        "time_signature": rh.get("time_signature", "4/4"),
        "loudness_lufs": loud.get("integrated_lufs"),
    }


# --------------------------------------------------------------------------
def vocal_presence(features: Dict, musical: Dict) -> Dict:
    """Does this sound like it has a lead vocal, or is it an instrumental?

    A beats marketplace cares about this directly: an "instrumental" listing
    with a vocal left in it is a support ticket.
    """
    sp = features.get("spectral", {})
    tn = features.get("tonal", {})
    bands = {b["name"]: b["share"] for b in sp.get("bands", [])}

    # A sung lead concentrates energy 300 Hz to 4 kHz and is strongly harmonic.
    mid = bands.get("Mid", 0) + bands.get("High mid", 0) * 0.6
    harmonic = tn.get("harmonic_ratio", 0.5)
    score = _c(_scale(mid, 0.06, 0.40) * 0.65 + _c(harmonic) * 0.35)

    if score > 0.62:
        verdict, note = "likely vocal", (
            "Strong sustained midrange energy consistent with a lead vocal or a "
            "prominent lead instrument.")
    elif score > 0.34:
        verdict, note = "uncertain", (
            "Midrange activity is ambiguous. It could be a lead instrument, a "
            "vocal chop, or a busy arrangement.")
    else:
        verdict, note = "likely instrumental", (
            "Little sustained midrange energy, consistent with an instrumental "
            "or a beat with the topline removed.")

    return {"verdict": verdict, "score": score, "midrange_share": _f(mid),
            "note": note,
            "caveat": ("Estimated from the spectral balance, not from source "
                       "separation, so a lead synth can read like a vocal.")}


# --------------------------------------------------------------------------
def beat_structure(musical: Dict, duration: float) -> Dict:
    """Placement facts a producer or A&R listener checks first."""
    arr = musical.get("arrangement", {})
    sections = arr.get("sections") or []
    rh = musical.get("rhythm", {})

    intro_s: Optional[float] = None
    drop_s: Optional[float] = None
    if sections:
        first = sections[0]
        if first.get("energy", 1) < 0.9:
            intro_s = first.get("duration")
        peak = max(sections, key=lambda s: s.get("energy", 0))
        drop_s = peak.get("start")

    bar_s = None
    bpm = rh.get("bpm")
    if bpm:
        bar_s = 4 * 60.0 / float(bpm)

    return {
        "intro_length_s": _f(intro_s, 2) if intro_s is not None else None,
        "intro_bars": _f(intro_s / bar_s, 1) if (intro_s and bar_s) else None,
        "first_peak_s": _f(drop_s, 2) if drop_s is not None else None,
        "bar_seconds": _f(bar_s, 3) if bar_s else None,
        "total_bars": rh.get("bar_count"),
        "section_count": arr.get("section_count"),
        "arrangement_note": arr.get("phrasing_note"),
        "hook_hint": (
            f"Highest energy section begins at {drop_s:.0f}s."
            if drop_s is not None else
            "No clear energy peak was detected."),
    }


# --------------------------------------------------------------------------
def quality_control(path: str, features: Dict, production: Dict) -> Dict:
    """Upload QC for a marketplace listing.

    Returns a pass/warn/fail per check plus an overall gate, so a platform can
    reject or flag a bad upload before it reaches a buyer.
    """
    import librosa

    checks: List[Dict] = []

    def add(name: str, status: str, detail: str, value=None):
        checks.append({"check": name, "status": status,
                       "detail": detail, "value": value})

    loud = production.get("loudness", {})
    stereo = production.get("stereo", {})
    enc = production.get("encoding", {})
    # --- head and tail silence, DC offset, noise floor -------------------
    try:
        y, sr = librosa.load(path, sr=22050, mono=True, duration=600)
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        thresh = max(peak * 0.01, 1e-4)

        nz = np.where(np.abs(y) > thresh)[0]
        lead_in = float(nz[0] / sr) if nz.size else 0.0
        tail = float((y.size - nz[-1]) / sr) if nz.size else 0.0

        dc = float(np.mean(y)) if y.size else 0.0
        # Noise floor from the quietest tenth of frames.
        rms = librosa.feature.rms(y=y, hop_length=1024)[0]
        floor = float(np.percentile(rms, 10)) if rms.size else 0.0
        floor_db = 20 * math.log10(max(floor, 1e-10))

        if lead_in > 1.5:
            add("lead_in_silence", "warn",
                f"{lead_in:.2f}s of silence before the first audible content. "
                f"Trim it so the preview starts on the music.", _f(lead_in, 2))
        else:
            add("lead_in_silence", "pass",
                f"Starts within {lead_in:.2f}s.", _f(lead_in, 2))

        if tail > 4.0:
            add("tail_silence", "warn",
                f"{tail:.2f}s of silence at the end.", _f(tail, 2))
        else:
            add("tail_silence", "pass", f"Ends within {tail:.2f}s.", _f(tail, 2))

        if abs(dc) > 0.002:
            add("dc_offset", "fail",
                f"DC offset {dc:+.4f} wastes headroom and can click on edit "
                f"points. Apply a DC filter.", _f(dc, 5))
        else:
            add("dc_offset", "pass", "No significant DC offset.", _f(dc, 5))

        if floor_db > -50:
            add("noise_floor", "warn",
                f"Noise floor around {floor_db:.0f} dBFS is audible in quiet "
                f"passages.", _f(floor_db, 1))
        else:
            add("noise_floor", "pass",
                f"Noise floor {floor_db:.0f} dBFS.", _f(floor_db, 1))
    except Exception:
        log.debug("QC waveform checks failed", exc_info=True)

    # --- clipping and true peak -----------------------------------------
    clipped = loud.get("clipped_samples", 0)
    if clipped and clipped > 50:
        add("clipping", "fail", f"{clipped} samples at full scale. Re-export "
                                f"with headroom.", clipped)
    elif clipped:
        add("clipping", "warn", f"{clipped} samples at full scale.", clipped)
    else:
        add("clipping", "pass", "No clipped samples.", 0)

    tp = loud.get("true_peak_dbtp")
    if tp is not None:
        if tp > -0.1:
            add("true_peak", "fail",
                f"True peak {tp:.2f} dBTP will distort after lossy encoding. "
                f"Target -1.0 dBTP.", tp)
        elif tp > -1.0:
            add("true_peak", "warn",
                f"True peak {tp:.2f} dBTP is above the -1.0 dBTP delivery "
                f"ceiling.", tp)
        else:
            add("true_peak", "pass", f"True peak {tp:.2f} dBTP.", tp)

    # --- loudness against catalogue -------------------------------------
    lufs = loud.get("integrated_lufs")
    if lufs is not None:
        if lufs < -20:
            add("loudness", "warn",
                f"{lufs:.1f} LUFS is quiet next to a normalised catalogue.", lufs)
        elif lufs > -7:
            add("loudness", "warn",
                f"{lufs:.1f} LUFS is very hot and will be turned down "
                f"everywhere.", lufs)
        else:
            add("loudness", "pass", f"{lufs:.1f} LUFS.", lufs)

    # --- stereo integrity ------------------------------------------------
    if stereo.get("is_stereo"):
        corr = stereo.get("correlation", 1)
        if corr < 0:
            add("phase", "fail",
                f"Correlation {corr:.2f} means the mix partly cancels in mono.",
                corr)
        elif corr < 0.3:
            add("phase", "warn",
                f"Correlation {corr:.2f} is wide. Check mono fold-down.", corr)
        else:
            add("phase", "pass", f"Correlation {corr:.2f}.", corr)

        if not stereo.get("low_end_mono", True):
            add("low_end_mono", "warn",
                "Low end carries side energy, which is unstable on club "
                "systems and in a vinyl cut.", stereo.get("width"))
        else:
            add("low_end_mono", "pass", "Low end is effectively mono.", None)
    else:
        add("channels", "warn",
            "File is mono. Most marketplaces expect a stereo master.", 1)

    # --- delivered bandwidth --------------------------------------------
    shelf = enc.get("lowpass_shelf_hz")
    if shelf is not None:
        if shelf < 16000:
            add("bandwidth", "warn",
                f"Content stops at {shelf/1000:.1f} kHz, which suggests the "
                f"master came from a lossy source. Upload from the original "
                f"render.", _f(shelf, 1))
        else:
            add("bandwidth", "pass",
                f"Full bandwidth to {shelf/1000:.1f} kHz.", _f(shelf, 1))

    if enc.get("transcode_suspected"):
        add("transcode", "warn", enc.get("transcode_note", "Transcode suspected."),
            enc.get("declared_bitrate_bps"))

    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    gate = "fail" if fails else "warn" if warns else "pass"

    return {
        "gate": gate,
        "failed": fails,
        "warnings": warns,
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "checks": checks,
        "summary": (
            "Ready to publish." if gate == "pass" else
            f"{warns} item(s) to review before publishing." if gate == "warn" else
            f"{fails} blocking issue(s) must be fixed before publishing."),
    }


# --------------------------------------------------------------------------
def build(path: str, features: Dict, musical: Dict, production: Dict,
          duration: float) -> Dict:
    features = features or {}
    musical = musical or {}
    production = production or {}
    return {
        "catalogue_features": catalogue_features(features, musical, production),
        "vocal_presence": vocal_presence(features, musical),
        "structure": beat_structure(musical, duration),
        "quality_control": quality_control(path, features, production),
    }
