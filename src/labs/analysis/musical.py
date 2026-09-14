"""Musicological analysis: tempo, groove, harmony, drums, arrangement.

Written in the vocabulary a producer uses. Drum voices are approximated from
band-limited onset detection rather than source separation, which keeps the
whole pass under a few seconds instead of the minutes a stem model costs. That
is a deliberate accuracy trade and is labelled as an approximation wherever it
surfaces.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from .decode import AudioCache

log = logging.getLogger(__name__)

ANALYSIS_SR = 22050
MAX_SECONDS = 240

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler key profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Camelot wheel, for DJs matching keys.
_CAMELOT_MAJOR = {"B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B",
                  "A#": "6B", "F": "7B", "C": "8B", "G": "9B", "D": "10B",
                  "A": "11B", "E": "12B"}
_CAMELOT_MINOR = {"G#": "1A", "D#": "2A", "A#": "3A", "F": "4A", "C": "5A",
                  "G": "6A", "D": "7A", "A": "8A", "E": "9A", "B": "10A",
                  "F#": "11A", "C#": "12A"}

# Chord templates over 12 pitch classes.
_CHORD_SHAPES = {
    "": [0, 4, 7], "min": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8],
    "sus2": [0, 2, 7], "sus4": [0, 5, 7], "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11], "min7": [0, 3, 7, 10],
}

# Drum voice bands (Hz). An approximation of kick / snare / hat energy.
_DRUM_BANDS = [("kick", 30, 120), ("snare", 180, 900), ("hat", 6000, 12000)]


def _f(x, nd: int = 3) -> float:
    v = float(x)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(v, nd)


def _i(x):
    """Whole numbers for tempo. See `labs.tools.base._i` for the reasoning.

    Returns None rather than 0 for a non-finite input: a missing tempo and a
    tempo of zero are different facts, and 0 BPM would be rendered as a real
    reading by anything downstream that only checks for None.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return int(round(v))


def _thin(arr, n: int):
    a = np.asarray(arr)
    if a.size <= n:
        return a
    return a[np.linspace(0, a.size - 1, n).astype(int)]


# --------------------------------------------------------------------------
def rhythm(y: np.ndarray, sr: int, duration: float,
           tracker_beats: Optional[List[float]] = None) -> Dict:
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # Prefer beat_this's grid when the caller has one: it is a dedicated beat
    # tracker and locks far better than librosa's on dense or half-time
    # material, which otherwise lands on the wrong metrical level.
    if tracker_beats and len(tracker_beats) > 8:
        beats = np.asarray([t for t in tracker_beats if t < duration], dtype=float)
        iv = np.diff(beats)
        tempo = float(60.0 / np.median(iv)) if iv.size else 0.0
        beat_source = "beat_this"
    else:
        tempo, beats = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, units="time")
        tempo = float(np.atleast_1d(tempo)[0])
        beats = np.asarray(beats, dtype=float)
        beat_source = "librosa"

    # Tempo over time. Derive this from the SAME beat grid the summary stats
    # use: librosa's independent estimator disagrees wildly with a dedicated
    # tracker, which would put a wildly spiky curve next to a "constant tempo"
    # figure in the report.
    if beats.size > 3:
        iv = np.diff(beats)
        local = 60.0 / np.clip(iv, 1e-6, None)
        # Median filter over five beats to suppress single-beat tracking slips
        # without hiding real tempo movement.
        if local.size >= 5:
            k = 5
            pad = np.pad(local, (k // 2, k // 2), mode="edge")
            local = np.array([np.median(pad[i:i + k]) for i in range(local.size)])
        curve = [{"time": _f(t, 2), "bpm": _i(b)}
                 for t, b in zip(_thin(beats[:-1], 120), _thin(local, 120),
                                 strict=False)]
    else:
        curve = []

    if beats.size > 2:
        iv = np.diff(beats)
        jitter_ms = float(np.std(iv) * 1000)
        bpm_sigma = float(np.std(60.0 / np.clip(iv, 1e-6, None)))
    else:
        jitter_ms, bpm_sigma = 0.0, 0.0

    pulse_clarity = float(np.clip(
        np.max(librosa.autocorrelate(onset_env, max_size=int(sr / 512 * 4)))
        / (np.sum(onset_env ** 2) + 1e-9), 0, 1)) if onset_env.size else 0.0

    # Half-time and double-time feels report a tracker tempo at the wrong
    # metrical level, so report both and say which is likely notated.
    notated, level, reason = tempo, "as tracked", "tracker tempo matches the notated pulse"
    if tempo < 95:
        notated, level = tempo * 2, "half-time"
        reason = "slow tracked pulse against dense subdivision, likely notated at double"
    elif tempo > 190:
        notated, level = tempo / 2, "double-time"
        reason = "very fast tracked pulse, likely notated at half"

    return {
        "bpm": _i(notated),
        "bpm_tracked": _i(tempo),
        "beat_source": beat_source,
        "metrical_level": level,
        "metrical_reason": reason,
        "bpm_curve": curve,
        "bpm_stability_sigma": _f(bpm_sigma, 3),
        "beat_jitter_ms": _f(jitter_ms, 2),
        "is_constant_tempo": bool(bpm_sigma < 3.0),
        "pulse_clarity": _f(pulse_clarity, 3),
        "beat_count": int(beats.size),
        "bar_count": int(beats.size // 4),
        "beats_per_bar": 4,
        "time_signature": "4/4",
        "beat_times": [_f(t, 3) for t in _thin(beats, 400)],
    }


# --------------------------------------------------------------------------
def groove(y: np.ndarray, sr: int, beat_times: List[float],
           y_percussive: Optional[np.ndarray] = None) -> Dict:
    """How the performance sits against a strict grid.

    Onsets are taken from the percussive component when available. Measuring
    on the full mix folds in sustained melodic entries, which legitimately sit
    further off the grid and inflate the deviation figure.
    """
    import librosa

    src = y_percussive if y_percussive is not None else y
    onsets = librosa.onset.onset_detect(y=src, sr=sr, units="time")
    beats = np.asarray(beat_times, dtype=float)
    if onsets.size < 8 or beats.size < 4:
        return {"available": False}

    beat_len = float(np.median(np.diff(beats)))
    if beat_len <= 0:
        return {"available": False}

    # Measure every onset against its NEAREST beat, using that beat's own
    # local interval. Extrapolating one grid from beats[0] across the whole
    # track accumulates any tempo drift into the deviation figure, which makes
    # a tight performance look freely played.
    idx = np.searchsorted(beats, onsets)
    idx = np.clip(idx, 1, beats.size - 1)
    prev_beat = beats[idx - 1]
    local_len = np.where(idx < beats.size, beats[idx] - beats[idx - 1], beat_len)
    local_len = np.where(local_len > 1e-6, local_len, beat_len)
    rel = onsets - prev_beat

    best = None
    for subdiv, label in ((4, "1/16"), (3, "1/8T"), (6, "1/16T"), (8, "1/32"), (2, "1/8")):
        step = local_len / subdiv
        k = np.round(rel / step)
        offs = rel - k * step
        # Normalised error makes subdivisions comparable to each other.
        err = float(np.mean(np.abs(offs) / step))
        if best is None or err < best[0]:
            best = (err, label, subdiv, offs)

    err, label, subdiv, offs = best
    dev_ms = offs * 1000
    mean_abs = float(np.mean(np.abs(dev_ms)))
    mean_signed = float(np.mean(dev_ms))

    if mean_abs < 3:
        qclass = "hard quantised"
        qnote = ("Onsets land almost exactly on the grid, which points to "
                 "programmed material with no humanisation applied.")
    elif mean_abs < 12:
        qclass = "lightly humanised"
        qnote = ("Small deliberate offsets, typical of a humanise setting or "
                 "light hand editing.")
    elif mean_abs < 28:
        qclass = "loosely played"
        qnote = "Natural timing spread, consistent with a played performance."
    else:
        qclass = "freely played"
        qnote = "Wide timing spread, either rubato or an unquantised take."

    pocket = ("laid back, behind the beat" if mean_signed > 4 else
              "pushed, ahead of the beat" if mean_signed < -4 else
              "centred on the grid")

    # Swing: where offbeat eighths sit within their own beat. 50% is straight,
    # higher means the second eighth is delayed.
    phase = rel / local_len
    offbeat = phase[(phase > 0.3) & (phase < 0.7)]
    swing_pct = float(np.mean(offbeat) * 100) if offbeat.size else 50.0
    swing_class = ("straight" if swing_pct < 54 else
                   "light swing" if swing_pct < 58 else
                   "medium swing" if swing_pct < 63 else "hard swing")

    # Rhythmic entropy: how evenly onsets spread across the beat.
    hist, _ = np.histogram(phase, bins=16, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    p = p[p > 0]
    entropy = float(-(p * np.log(p)).sum() / math.log(16)) if p.size else 0.0

    # Fraction of onsets that do not land close to a beat.
    onbeat = int(np.sum(np.minimum(phase, 1.0 - phase) < 0.12))
    offbeatness = _f(1.0 - onbeat / max(onsets.size, 1))

    return {
        "available": True,
        "grid": label,
        "grid_subdivisions_per_beat": subdiv,
        "grid_fit_error": _f(err, 4),
        "swing_pct": _f(swing_pct, 2),
        "swing_classification": swing_class,
        "quantization_class": qclass,
        "quantization_note": qnote,
        "mean_abs_deviation_ms": _f(mean_abs, 2),
        "mean_signed_deviation_ms": _f(mean_signed, 2),
        "deviation_sigma_ms": _f(float(np.std(dev_ms)), 2),
        "pocket": pocket,
        "offbeatness": offbeatness,
        "rhythmic_entropy": _f(entropy),
        "onset_count": int(onsets.size),
        "deviation_histogram": _histogram(dev_ms),
    }


def _histogram(dev_ms: np.ndarray, bins: int = 24) -> List[Dict]:
    lo, hi = -40.0, 40.0
    hist, edges = np.histogram(np.clip(dev_ms, lo, hi), bins=bins, range=(lo, hi))
    return [{"ms": _f((edges[i] + edges[i + 1]) / 2, 2), "count": int(hist[i])}
            for i in range(bins)]


# --------------------------------------------------------------------------
def drums(y: np.ndarray, sr: int, beat_times: List[float],
          y_percussive: Optional[np.ndarray] = None) -> Dict:
    """Approximate drum voices from band-limited onset detection."""
    import librosa
    import scipy.signal as ss

    src = y_percussive if y_percussive is not None else y
    beats = np.asarray(beat_times, dtype=float)
    if beats.size < 8:
        return {"available": False}
    beat_len = float(np.median(np.diff(beats)))
    if beat_len <= 0:
        return {"available": False}

    voices: Dict[str, Dict] = {}
    nyq = sr / 2
    for name, lo, hi in _DRUM_BANDS:
        try:
            sos = ss.butter(4, [max(lo, 20) / nyq, min(hi, nyq * 0.98) / nyq],
                            btype="band", output="sos")
            band = ss.sosfilt(sos, src)
            env = librosa.onset.onset_strength(y=band, sr=sr)
            hits = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="time")
            if hits.size == 0:
                continue

            # Velocity proxy from the local peak of the band envelope.
            frames = librosa.time_to_frames(hits, sr=sr)
            frames = np.clip(frames, 0, env.size - 1)
            strengths = env[frames]
            peak = float(strengths.max()) or 1.0
            velocities = np.clip(strengths / peak * 127, 1, 127)

            voices[name] = {
                "count": int(hits.size),
                "velocity_mean": _f(float(velocities.mean()), 1),
                "velocity_sigma": _f(float(velocities.std()), 2),
                "hits_per_bar": _f(hits.size / max(beats.size / 4, 1), 2),
                "onsets": [_f(t, 3) for t in _thin(hits, 300)],
            }
        except Exception:
            log.debug("Drum band %s failed", name, exc_info=True)

    if not voices:
        return {"available": False}

    # 16-step pattern grid for the first bars, the classic sequencer view.
    steps_per_bar = 16
    step = beat_len * 4 / steps_per_bar
    bars = min(8, int(beats.size // 4))
    grid: Dict[str, List[List[int]]] = {}
    for name, v in voices.items():
        rows = []
        onsets = np.asarray(v["onsets"], dtype=float)
        for b in range(bars):
            start = beats[0] + b * beat_len * 4
            row = [0] * steps_per_bar
            for t in onsets:
                if start <= t < start + beat_len * 4:
                    idx = int(round((t - start) / step)) % steps_per_bar
                    row[idx] = 1
            rows.append(row)
        grid[name] = rows

    kick_rows = grid.get("kick", [])
    kick_pattern = "unknown"
    if kick_rows:
        avg = np.mean(kick_rows, axis=0)
        downbeat_hits = avg[0] + avg[4] + avg[8] + avg[12]
        offbeat_hits = float(avg.sum()) - downbeat_hits
        kick_pattern = ("four to the floor" if downbeat_hits > 3.2 else
                        "syncopated" if offbeat_hits > downbeat_hits else
                        "sparse, downbeat led")

    total = sum(v["count"] for v in voices.values())
    vel_sigmas = [v["velocity_sigma"] for v in voices.values()]
    humanisation = _f(float(np.mean(vel_sigmas)) / 127 if vel_sigmas else 0.0)

    return {
        "available": True,
        "approximate": True,
        "method": "band-limited onset detection, not source separation",
        "total_hits": int(total),
        "voices": voices,
        "pattern_grid": {"steps_per_bar": steps_per_bar, "bars": bars, "voices": grid},
        "kick_pattern": kick_pattern,
        "velocity_humanization": humanisation,
        "humanization_note": (
            "Velocities are near identical, which reads as programmed"
            if humanisation < 0.08 else
            "Meaningful velocity variation across hits"),
    }


# --------------------------------------------------------------------------
def harmony(y: np.ndarray, sr: int, beat_times: List[float]) -> Dict:
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    profile = profile / (profile.sum() or 1.0)

    best, best_r, scores = None, -2.0, []
    for mode, ref in (("major", _MAJOR), ("minor", _MINOR)):
        for shift in range(12):
            r = float(np.corrcoef(profile, np.roll(ref, shift))[0, 1])
            scores.append(r)
            if r > best_r:
                best_r, best = r, (PITCHES[shift], mode)
    scores.sort(reverse=True)
    clarity = float(scores[0] - scores[1]) if len(scores) > 1 else 0.0

    root, mode = best if best else ("C", "major")
    camelot = (_CAMELOT_MAJOR if mode == "major" else _CAMELOT_MINOR).get(root, "")

    # Beat-synchronous chord estimate via template matching.
    beats = np.asarray(beat_times, dtype=float)
    progression: List[str] = []
    if beats.size > 4:
        frames = librosa.time_to_frames(beats, sr=sr)
        frames = np.clip(frames, 0, chroma.shape[1] - 1)
        sync = librosa.util.sync(chroma, frames, aggregate=np.median)
        for i in range(sync.shape[1]):
            v = sync[:, i]
            if v.sum() <= 0:
                continue
            v = v / v.sum()
            top_name, top_score = None, -1.0
            for shift in range(12):
                for suffix, tones in _CHORD_SHAPES.items():
                    tpl = np.zeros(12)
                    for t in tones:
                        tpl[(t + shift) % 12] = 1.0
                    tpl /= tpl.sum()
                    s = float(np.dot(v, tpl))
                    if s > top_score:
                        top_score, top_name = s, f"{PITCHES[shift]}{suffix}"
            if top_name:
                progression.append(top_name)

    # Collapse consecutive repeats into the readable progression.
    collapsed: List[str] = []
    for c in progression:
        if not collapsed or collapsed[-1] != c:
            collapsed.append(c)

    scale_tones = {(_pitch_index(root) + i) % 12
                   for i in ([0, 2, 4, 5, 7, 9, 11] if mode == "major"
                             else [0, 2, 3, 5, 7, 8, 10])}
    conformance = _f(float(sum(profile[t] for t in scale_tones)))

    harmonic_rhythm = _f(len(collapsed) / max(beats.size / 4, 1), 3) if beats.size else 0.0

    return {
        "key": f"{root} {'major' if mode == 'major' else 'natural minor'}",
        "root": root,
        "mode": mode,
        "camelot": camelot,
        "key_correlation": _f(best_r),
        "key_clarity": _f(clarity),
        "scale_conformance": conformance,
        "chroma": [{"pitch": PITCHES[i], "weight": _f(float(profile[i]))}
                   for i in range(12)],
        "chord_count": len(collapsed),
        "progression": collapsed[:24],
        "harmonic_rhythm_per_bar": harmonic_rhythm,
        "harmonic_complexity": _f(len(set(collapsed)) / max(len(collapsed), 1)),
    }


def _pitch_index(name: str) -> int:
    try:
        return PITCHES.index(name)
    except ValueError:
        return 0


# --------------------------------------------------------------------------
def arrangement(y: np.ndarray, sr: int, duration: float) -> Dict:
    """Section boundaries with an energy and density label for each."""
    import librosa

    try:
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        bounds = librosa.segment.agglomerative(mfcc, k=min(8, max(3, int(duration // 20))))
        times = librosa.frames_to_time(bounds, sr=sr)
    except Exception:
        log.debug("Segmentation failed", exc_info=True)
        return {"available": False}

    rms = librosa.feature.rms(y=y)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    rms_t = librosa.times_like(rms, sr=sr)
    env_t = librosa.times_like(onset_env, sr=sr)

    edges = list(times) + [duration]
    mean_rms = float(rms.mean()) or 1e-9
    mean_env = float(onset_env.mean()) or 1e-9

    sections = []
    for i in range(len(edges) - 1):
        s, e = float(edges[i]), float(edges[i + 1])
        if e - s < 2.0:
            continue
        rs = rms[(rms_t >= s) & (rms_t < e)]
        es = onset_env[(env_t >= s) & (env_t < e)]
        energy = float(rs.mean() / mean_rms) if rs.size else 0.0
        density = float(es.mean() / mean_env) if es.size else 0.0

        if energy > 1.15:
            label = "drop or main section"
        elif energy > 0.85:
            label = "verse or main loop"
        elif energy > 0.5:
            label = "breakdown"
        else:
            label = "intro, outro or ambient passage"

        sections.append({
            "label": label, "start": _f(s, 2), "end": _f(e, 2),
            "duration": _f(e - s, 2), "energy": _f(energy),
            "density": _f(density),
        })

    lengths = [s["duration"] for s in sections]
    regular = bool(lengths and float(np.std(lengths)) / (np.mean(lengths) or 1) < 0.35)

    return {
        "available": True,
        "section_count": len(sections),
        "sections": sections,
        "regular_phrasing": regular,
        "phrasing_note": ("Section lengths are consistent, which reads as "
                          "conventional phrasing."
                          if regular else
                          "Section lengths are irregular, which reads as "
                          "non-standard phrasing."),
    }


# --------------------------------------------------------------------------
def analyse(path: str, tracker_beats: Optional[List[float]] = None,
            cache: Optional["AudioCache"] = None) -> Dict:
    """Full musical pass. Returns {} if the file cannot be analysed.

    `tracker_beats` should be beat_this's beat grid when available; it is
    materially more reliable than re-tracking here.

    `cache` shares the decode and the harmonic/percussive split with the other
    passes of the same request.
    """
    from .decode import cache_for

    cache = cache_for(path, cache)
    try:
        y, sr = cache.audio(ANALYSIS_SR, mono=True, duration=MAX_SECONDS)
        duration = y.size / sr
        if duration < 2:
            return {}

        # One HPSS pass, shared by groove and drums - and now also by the
        # `features` pass, which needs only the energy ratio out of it. See
        # decode.AudioCache.split.
        try:
            y_perc, _, _ = cache.split(ANALYSIS_SR, MAX_SECONDS)
        except Exception:
            log.debug("HPSS failed, falling back to the full mix", exc_info=True)
            y_perc = None

        r = rhythm(y, sr, duration, tracker_beats=tracker_beats)
        beat_times = r.get("beat_times", [])
        return {
            "rhythm": r,
            "groove": groove(y, sr, beat_times, y_percussive=y_perc),
            "drums": drums(y, sr, beat_times, y_percussive=y_perc),
            "harmony": harmony(y, sr, beat_times),
            "arrangement": arrangement(y, sr, duration),
            "analysed_seconds": _f(duration, 2),
        }
    except Exception:
        log.exception("Musical analysis failed for %s", path)
        return {}
