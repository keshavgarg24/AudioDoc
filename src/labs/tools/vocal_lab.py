"""Vocal Lab: pitch accuracy, range, timing and recording hygiene.

Everything reported is measurable from the signal: fundamental frequency,
deviation from equal temperament, vibrato, sibilant energy, plosive energy,
noise floor and dynamic range.

Nothing here scores tone, emotion, character or commercial appeal. Those are
not measurable, and a fabricated number for them would undermine every honest
figure on the page. If a caller wants that, the correct answer is that this
tool does not provide it.

Pitch tracking uses pYIN, which is reliable on a solo vocal. On a full mix it
will lock onto whatever is loudest, so the response says plainly when the input
does not look like an isolated vocal.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..analysis import production as prod
from .base import Decoded, ToolSpec, _db, _f

SPEC = ToolSpec(
    slug="vocal-lab",
    name="Vocal Lab",
    summary="Pitch accuracy, range, vibrato, timing and recording hygiene for "
            "an isolated vocal.",
    inputs=("file",),
    typical_seconds=(12, 25),
    accuracy="Pitch, range, timing, sibilance and plosive measurements are "
             "measurement-grade on a clean isolated vocal.",
    basis="pYIN fundamental-frequency tracking, deviation from equal "
          "temperament in cents, and band-limited energy analysis.",
    limitations=(
        "Requires an isolated vocal. On a full mix the tracker follows the "
        "loudest pitched source, which may not be the voice.",
        "This tool does not score tone, emotion or commercial appeal. Those "
        "are not measurable from audio.",
        "Heavy pitch correction flattens the deviation measurements, which is "
        "reported rather than hidden.",
    ),
)

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def run(audio: Decoded, **_) -> Dict:
    import librosa

    y, sr = audio.musical, audio.sr_musical

    f0, voiced_flag, _ = _track_pitch(librosa, y, sr)
    voiced = f0[voiced_flag] if f0 is not None and voiced_flag is not None else np.array([])
    voiced = voiced[np.isfinite(voiced)] if voiced.size else voiced

    isolation = _isolation_check(audio, voiced_flag)
    pitch = _pitch(voiced, librosa)
    timing = _timing(librosa, y, sr, audio.beats())
    hygiene = _hygiene(audio)
    dynamics = _dynamics(audio)

    return {
        "input_check": isolation,
        "pitch": pitch,
        "timing": timing,
        "hygiene": hygiene,
        "dynamics": dynamics,
        "headline": {
            "range": pitch.get("range", {}).get("label"),
            "tuning_cents": pitch.get("tuning", {}).get("mean_abs_cents"),
            "tuning_label": pitch.get("tuning", {}).get("label"),
            "sibilance": hygiene.get("sibilance", {}).get("label"),
            "looks_isolated": isolation.get("looks_isolated"),
        },
        "not_measured": [
            "Tone quality, emotion and commercial appeal are not scored. "
            "They cannot be measured from audio.",
        ],
    }


def _track_pitch(librosa, y: np.ndarray, sr: int) -> Tuple:
    """pYIN over the singing range. Returns (f0, voiced_flag, probability)."""
    try:
        return librosa.pyin(
            y, sr=sr,
            fmin=float(librosa.note_to_hz("E2")),   # ~82 Hz, low male
            fmax=float(librosa.note_to_hz("C6")),   # ~1047 Hz, high female
            frame_length=2048)
    except Exception:
        return None, None, None


def _isolation_check(audio: Decoded, voiced_flag) -> Dict:
    """Whether the input plausibly is a solo vocal.

    A full mix is the most likely user error, and every pitch number below is
    meaningless if that is what arrived. Voiced ratio plus low-frequency energy
    separates the two well enough to warn on.
    """
    voiced_ratio = None
    if voiced_flag is not None and len(voiced_flag):
        voiced_ratio = float(np.mean(voiced_flag))

    bass_share = _low_share(audio)

    looks_isolated = True
    reasons: List[str] = []
    if bass_share is not None and bass_share > 0.30:
        looks_isolated = False
        reasons.append(
            f"{bass_share * 100:.0f}% of the energy sits below 150 Hz, which is "
            f"more low end than a solo vocal carries. This may be a full mix.")
    if voiced_ratio is not None and voiced_ratio > 0.95:
        looks_isolated = False
        reasons.append(
            "Pitch was detected almost continuously, which is unusual for a "
            "vocal and typical of a sustained instrumental bed.")

    return {
        "looks_isolated": looks_isolated,
        "voiced_ratio": _f(voiced_ratio, 3) if voiced_ratio is not None else None,
        "low_frequency_share": _f(bass_share, 3) if bass_share is not None else None,
        "warnings": reasons,
        "note": ("Input looks like an isolated vocal."
                 if looks_isolated else
                 "Results below assume an isolated vocal and may be unreliable "
                 "for this input."),
    }


def _low_share(audio: Decoded) -> Optional[float]:
    try:
        import librosa
        spec = np.abs(librosa.stft(audio.mono, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=audio.sr, n_fft=2048)
        power = (spec ** 2).mean(axis=1)
        total = float(power.sum()) or 1e-12
        return float(power[freqs < 150.0].sum()) / total
    except Exception:
        return None


def _pitch(voiced: np.ndarray, librosa) -> Dict:
    if voiced.size < 10:
        return {"available": False,
                "note": "Not enough voiced material to measure pitch."}

    midi = librosa.hz_to_midi(voiced)
    lo, hi = float(np.percentile(midi, 2)), float(np.percentile(midi, 98))
    # Tessitura: where the voice actually lives, not the extremes it touched.
    t_lo, t_hi = float(np.percentile(midi, 25)), float(np.percentile(midi, 75))

    # Cents from the nearest equal-tempered semitone.
    cents = (midi - np.round(midi)) * 100.0
    mean_abs = float(np.mean(np.abs(cents)))
    median_signed = float(np.median(cents))

    if mean_abs < 12:
        label = "tight"
        note = "Intonation is tight. This is at or beyond the level of a "\
               "polished commercial vocal."
    elif mean_abs < 22:
        label = "good"
        note = "Intonation is good. Occasional notes drift but nothing that "\
               "reads as out of tune."
    elif mean_abs < 35:
        label = "loose"
        note = "Intonation drifts audibly. Targeted tuning on the weakest "\
               "phrases would lift the take."
    else:
        label = "unstable"
        note = "Pitch wanders well beyond typical tolerance. Consider a "\
               "retake rather than correction."

    direction = ""
    if abs(median_signed) > 8:
        direction = (f"Overall the take sits {abs(median_signed):.0f} cents "
                     f"{'sharp' if median_signed > 0 else 'flat'}.")

    return {
        "available": True,
        "range": _range(midi, lo, hi, librosa),
        "tessitura": {
            "low_note": _note_name(t_lo, librosa),
            "high_note": _note_name(t_hi, librosa),
            "semitones": _f(t_hi - t_lo, 1),
            "note": "Where the voice spends most of its time.",
        },
        "tuning": {
            "mean_abs_cents": _f(mean_abs, 1),
            "median_signed_cents": _f(median_signed, 1),
            "label": label,
            "note": (note + " " + direction).strip(),
        },
        "vibrato": _vibrato(midi),
    }


def _range(midi: np.ndarray, lo: float, hi: float, librosa) -> Dict:
    semitones = hi - lo
    return {
        "low_note": _note_name(lo, librosa),
        "high_note": _note_name(hi, librosa),
        "semitones": _f(semitones, 1),
        "octaves": _f(semitones / 12.0, 2),
        "label": f"{_note_name(lo, librosa)} to {_note_name(hi, librosa)}",
        "note": "Measured between the 2nd and 98th percentile, so a single "
                "stray note does not widen the reported range.",
    }


def _note_name(midi_value: float, librosa) -> Optional[str]:
    try:
        m = int(round(midi_value))
        return f"{_NOTE_NAMES[m % 12]}{m // 12 - 1}"
    except Exception:
        return None


def _vibrato(midi: np.ndarray) -> Dict:
    """Rate and extent of periodic pitch modulation on sustained notes."""
    if midi.size < 64:
        return {"available": False}
    try:
        # Detrend so the melody itself does not read as modulation.
        detrended = midi - np.convolve(midi, np.ones(16) / 16, mode="same")
        extent_cents = float(np.std(detrended) * 100.0)
        # Zero crossings give the modulation rate; frame rate is ~86 Hz at
        # hop 256 on 22050, which pYIN uses by default.
        crossings = int(np.sum(np.diff(np.sign(detrended)) != 0))
        seconds = midi.size / 86.0
        rate = (crossings / 2.0) / seconds if seconds > 0 else 0.0

        if extent_cents < 15:
            label = "minimal"
        elif extent_cents < 45:
            label = "natural"
        else:
            label = "wide"
        return {"available": True, "rate_hz": _f(rate, 2),
                "extent_cents": _f(extent_cents, 1), "label": label,
                "note": "Typical sung vibrato sits near 5-7 Hz."}
    except Exception:
        return {"available": False}


def _timing(librosa, y: np.ndarray, sr: int, beats: List[float]) -> Dict:
    """Where syllable onsets land relative to the grid, when there is one."""
    try:
        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    except Exception:
        onsets = np.array([])

    if len(onsets) == 0:
        return {"available": False, "note": "No clear syllable onsets found."}

    result: Dict = {
        "available": True,
        "onset_count": int(len(onsets)),
        "syllables_per_second": _f(len(onsets) / max(len(y) / sr, 1e-6), 2),
    }

    if len(beats) < 4:
        result["grid"] = {
            "available": False,
            "note": "No steady beat grid in this file, so onsets could not be "
                    "measured against one. Upload the vocal with its beat, or "
                    "use Beat and Vocal Fit.",
        }
        return result

    b = np.asarray(beats, dtype=float)
    idx = np.clip(np.searchsorted(b, onsets), 1, b.size - 1)
    prev_beat = b[idx - 1]
    interval = np.where(b[idx] - b[idx - 1] > 1e-6, b[idx] - b[idx - 1], 0.5)
    rel = (onsets - prev_beat) / interval
    # Distance to the nearest grid position, signed.
    offset = (rel - np.round(rel)) * interval * 1000.0

    mean_signed = float(np.mean(offset))
    mean_abs = float(np.mean(np.abs(offset)))

    if abs(mean_signed) < 10:
        feel = "on the grid"
    elif mean_signed > 0:
        feel = "behind the beat"
    else:
        feel = "ahead of the beat"

    result["grid"] = {
        "available": True,
        "mean_signed_ms": _f(mean_signed, 1),
        "mean_abs_ms": _f(mean_abs, 1),
        "feel": feel,
        "note": f"On average the delivery sits {abs(mean_signed):.0f} ms "
                f"{'late' if mean_signed > 0 else 'early'}.",
    }
    return result


def _hygiene(audio: Decoded) -> Dict:
    """Sibilance, plosives and noise floor: the fixable recording problems."""
    import librosa

    spec = np.abs(librosa.stft(audio.mono, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=audio.sr, n_fft=2048)
    power = (spec ** 2)
    total = float(power.sum()) or 1e-12

    sib = float(power[(freqs >= 5000) & (freqs < 9000)].sum()) / total
    plosive = float(power[freqs < 100].sum()) / total

    if sib < 0.06:
        sib_label, sib_note = "controlled", "Sibilance is under control."
    elif sib < 0.12:
        sib_label, sib_note = "present", ("Sibilance is noticeable. A gentle "
                                          "de-esser around 6-8 kHz would help.")
    else:
        sib_label, sib_note = "harsh", ("Sibilance is heavy and will fatigue "
                                        "listeners. De-ess around 6-8 kHz.")

    if plosive < 0.02:
        plo_label, plo_note = "clean", "No significant plosive energy."
    elif plosive < 0.06:
        plo_label, plo_note = "present", ("Some low-frequency plosive energy. "
                                          "A high-pass around 80 Hz would "
                                          "clean it up.")
    else:
        plo_label, plo_note = "heavy", ("Strong plosive energy below 100 Hz. "
                                        "High-pass and check microphone "
                                        "technique.")

    return {
        "sibilance": {"share": _f(sib, 4), "label": sib_label, "note": sib_note,
                      "band_hz": [5000, 9000]},
        "plosives": {"share": _f(plosive, 4), "label": plo_label,
                     "note": plo_note, "band_hz": [0, 100]},
        "noise_floor": _noise_floor(audio),
    }


def _noise_floor(audio: Decoded) -> Dict:
    """Quietest sustained level, as a proxy for room tone and preamp noise."""
    try:
        frame = 2048
        n = audio.mono.size // frame
        if n < 4:
            return {"available": False}
        frames = audio.mono[:n * frame].reshape(n, frame)
        rms = np.sqrt((frames ** 2).mean(axis=1))
        floor = float(np.percentile(rms, 5))
        peak = float(np.percentile(rms, 95))
        snr = 20.0 * math.log10(peak / floor) if floor > 1e-9 and peak > 0 else None

        if snr is None:
            label, note = "unknown", ""
        elif snr > 45:
            label, note = "clean", "Background noise is well below the vocal."
        elif snr > 30:
            label, note = "acceptable", ("Some background noise. Audible in "
                                         "quiet passages but usable.")
        else:
            label, note = "noisy", ("Background noise is high enough to be "
                                    "audible. Noise reduction or a quieter "
                                    "room would help.")
        return {"available": True, "floor_db": _db(floor),
                "snr_db": _f(snr, 1) if snr is not None else None,
                "label": label, "note": note}
    except Exception:
        return {"available": False}


def _dynamics(audio: Decoded) -> Dict:
    loud = prod.loudness(audio.stereo, audio.sr)
    lra = loud.get("loudness_range_lu")
    plr = loud.get("plr_db")

    note = ""
    if lra is not None:
        if lra > 12:
            note = ("Wide level swings between phrases. Compression or manual "
                    "gain riding will help it sit in a mix.")
        elif lra < 4:
            note = "Already heavily compressed. Little dynamic movement left."
        else:
            note = "Dynamic range is in a workable range for mixing."

    return {
        "integrated_lufs": loud.get("integrated_lufs"),
        "loudness_range_lu": lra,
        "peak_to_loudness_db": plr,
        "true_peak_dbtp": loud.get("true_peak_dbtp"),
        "note": note,
    }
