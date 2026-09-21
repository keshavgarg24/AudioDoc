"""Beat and Vocal Fit: what to change so a vocal and an instrumental sit together.

Takes the two files separately, which is the natural way a producer already has
them. No stem separation is performed: separating a mix would add a minute of
compute and introduce artefacts, and then every number below would describe the
artefacts rather than the music.

Four questions are answered, in the order a mix engineer would ask them:

  1. Are they in the same key, and if not, how far apart?
  2. Are they at the same tempo, and is closing the gap realistic?
  3. Does the vocal sit early or late against the beat?
  4. Where do the two compete for the same frequencies?

The fourth is the one that is hard to do by ear and easy to do by measurement.
Masking is a real, computable overlap of two spectra, and the intelligibility
band between roughly 1 and 4 kHz is where competition actually costs the
listener words.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from ..analysis import musical
from ..analysis import production as prod
from .base import BANDS, Decoded, ToolSpec, _f, band_energy, loudness_normalise

SPEC = ToolSpec(
    slug="beat-vocal-fit",
    name="Beat and Vocal Fit",
    summary="Upload a beat and a vocal separately and get the key, tempo, "
            "timing and frequency changes needed to make them sit together.",
    inputs=("beat", "vocal"),
    typical_seconds=(4, 15),
    accuracy="Key, tempo and timing relationships are near-exact. Masking is "
             "an exact measurement of spectral overlap; the recommended move "
             "is standard mixing practice rather than a measured fact.",
    basis="Chroma key estimation on both sources, beat tracking, onset "
          "cross-correlation, and band-by-band spectral overlap weighted "
          "toward the vocal intelligibility range.",
    limitations=(
        "Both files must be supplied separately. A vocal already mixed into "
        "the beat cannot be analysed this way.",
        "Recommendations follow standard mixing practice. They are not a "
        "judgement of taste or arrangement.",
    ),
)

# Where losing the vocal actually costs intelligibility. Competition here is
# weighted far more heavily than competition in the sub or air bands.
_INTELLIGIBILITY_HZ = (1000.0, 4000.0)


def run(beat: Decoded, vocal: Decoded, **_) -> Dict:
    key = _key_fit(beat, vocal)
    tempo = _tempo_fit(beat, vocal)
    timing = _timing_fit(beat, vocal)
    masking = _masking(beat, vocal)
    balance = _balance(beat, vocal)

    actions = _actions(key, tempo, timing, masking, balance)
    eq_curve = _eq_curve(masking)

    return {
        "key": key,
        "tempo": tempo,
        "timing": timing,
        "masking": masking,
        "eq_curve": eq_curve,
        "balance": balance,
        "actions": actions,
        "headline": {
            "fit_score": _fit_score(key, tempo, masking),
            "key_compatible": key.get("compatible"),
            "tempo_compatible": tempo.get("compatible"),
            "worst_masking_band": masking.get("worst", {}).get("label"),
            "action_count": len(actions),
        },
        "limitations": list(SPEC.limitations),
    }


# ------------------------------------------------------------------ key ----
def _key_fit(beat: Decoded, vocal: Decoded) -> Dict:
    b = musical.harmony(beat.musical, beat.sr_musical, beat.beats())
    v = musical.harmony(vocal.musical, vocal.sr_musical, vocal.beats())

    b_root, v_root = b.get("root"), v.get("root")
    if not b_root or not v_root:
        return {"available": False,
                "note": "Key could not be estimated for one of the files."}

    from ..analysis.musical import PITCHES
    try:
        shift = (PITCHES.index(v_root) - PITCHES.index(b_root)) % 12
    except ValueError:
        return {"available": False}

    # Present the smaller move; +7 semitones up is -5 down.
    signed = shift if shift <= 6 else shift - 12
    same_camelot = bool(b.get("camelot") and b.get("camelot") == v.get("camelot"))
    compatible = signed == 0 or same_camelot

    if compatible:
        note = ("Both files sit in the same key. Nothing to change."
                if signed == 0 else
                "Both share a Camelot code, so they are harmonically "
                "compatible as they are.")
    else:
        note = (f"Pitch the beat {abs(signed)} semitone"
                f"{'s' if abs(signed) != 1 else ''} "
                f"{'up' if signed > 0 else 'down'} to match the vocal, or "
                f"pitch the vocal the other way.")

    return {
        "available": True,
        "beat": {"key": b.get("key"), "camelot": b.get("camelot"),
                 "clarity": b.get("key_clarity")},
        "vocal": {"key": v.get("key"), "camelot": v.get("camelot"),
                  "clarity": v.get("key_clarity")},
        "semitone_shift": signed,
        "compatible": compatible,
        "note": note,
    }


# ---------------------------------------------------------------- tempo ----
def _tempo_fit(beat: Decoded, vocal: Decoded) -> Dict:
    b = musical.rhythm(beat.musical, beat.sr_musical, beat.duration,
                       tracker_beats=beat.beats() or None)
    v = musical.rhythm(vocal.musical, vocal.sr_musical, vocal.duration,
                       tracker_beats=vocal.beats() or None)

    b_bpm, v_bpm = b.get("bpm"), v.get("bpm")
    if not b_bpm or not v_bpm:
        return {"available": False,
                "note": "Tempo could not be estimated for one of the files. "
                        "A vocal with no strong rhythmic content is common; "
                        "check the timing section instead."}

    # Compare at the same metrical level before judging the gap.
    ratio = v_bpm / b_bpm
    folded = ratio
    while folded > 1.5:
        folded /= 2.0
    while folded < 0.67:
        folded *= 2.0

    stretch_pct = (folded - 1.0) * 100.0
    if abs(stretch_pct) < 1.0:
        compatible, note = True, "Tempos already match."
    elif abs(stretch_pct) <= 6.0:
        compatible = True
        note = (f"A {abs(stretch_pct):.1f}% time stretch closes the gap. That "
                f"is small enough to stay transparent.")
    else:
        compatible = False
        note = (f"A {abs(stretch_pct):.1f}% stretch would be needed, which "
                f"starts to sound artificial on vocals. Re-recording to the "
                f"beat is the better option.")

    return {
        "available": True,
        "beat_bpm": b_bpm,
        "vocal_bpm": v_bpm,
        "stretch_pct": _f(stretch_pct, 2),
        "compatible": compatible,
        "note": note,
    }


# --------------------------------------------------------------- timing ----
def _timing_fit(beat: Decoded, vocal: Decoded) -> Dict:
    """Global offset between vocal onsets and the beat grid."""
    import librosa

    beats = beat.beats()
    if len(beats) < 4:
        return {"available": False,
                "note": "No steady grid in the beat, so offset could not be "
                        "measured."}
    try:
        onsets = librosa.onset.onset_detect(
            y=vocal.musical, sr=vocal.sr_musical, units="time")
    except Exception:
        onsets = np.array([])

    if len(onsets) < 4:
        return {"available": False,
                "note": "Not enough vocal onsets to measure timing."}

    b = np.asarray(beats, dtype=float)
    idx = np.clip(np.searchsorted(b, onsets), 1, b.size - 1)
    prev_beat = b[idx - 1]
    interval = np.where(b[idx] - b[idx - 1] > 1e-6, b[idx] - b[idx - 1], 0.5)
    rel = (onsets - prev_beat) / interval
    offset_ms = (rel - np.round(rel)) * interval * 1000.0

    mean_signed = float(np.mean(offset_ms))
    spread = float(np.std(offset_ms))

    if abs(mean_signed) < 12:
        note = "The vocal already lines up with the beat."
        nudge = 0
    else:
        nudge = int(round(mean_signed))
        note = (f"The vocal sits {abs(nudge)} ms "
                f"{'late' if nudge > 0 else 'early'} on average. Nudge the "
                f"whole track {abs(nudge)} ms "
                f"{'earlier' if nudge > 0 else 'later'} to lock it in.")

    return {
        "available": True,
        "mean_offset_ms": _f(mean_signed, 1),
        "spread_ms": _f(spread, 1),
        "suggested_nudge_ms": -nudge if nudge else 0,
        "consistency": ("tight" if spread < 25 else
                        "variable" if spread < 60 else "loose"),
        "note": note,
    }


# -------------------------------------------------------------- masking ----
def _masking(beat: Decoded, vocal: Decoded) -> Dict:
    """Where the two sources compete for the same frequencies.

    Both are level matched first, then compared band by band. A band is a
    problem when the beat holds a large share of the energy in a region the
    vocal also needs; the intelligibility range is weighted most heavily
    because that is where overlap actually costs the listener words.
    """
    b_norm = loudness_normalise(beat.mono, beat.sr)
    v_norm = loudness_normalise(vocal.mono, vocal.sr)

    b_bands = band_energy(b_norm, beat.sr)
    v_bands = band_energy(v_norm, vocal.sr)

    rows: List[Dict] = []
    for key, label, lo, hi in BANDS:
        b_share = (b_bands.get(key, {}).get("share_pct") or 0.0) / 100.0
        v_share = (v_bands.get(key, {}).get("share_pct") or 0.0) / 100.0
        if v_share <= 0.001:
            continue

        # Overlap: both present and the beat at least as loud as the vocal.
        contention = min(b_share, v_share) * (b_share / (v_share + 1e-9))
        in_speech = not (hi <= _INTELLIGIBILITY_HZ[0]
                         or lo >= _INTELLIGIBILITY_HZ[1])
        weight = 1.0 if in_speech else 0.35
        severity_score = contention * weight

        if severity_score < 0.02:
            severity, action = "clear", None
        elif severity_score < 0.06:
            severity = "some"
            action = (f"Mild competition around {label.lower()}. A 1-2 dB dip "
                      f"in the beat here would open space.")
        else:
            severity = "heavy"
            action = (f"The beat and vocal are fighting in the "
                      f"{label.lower()} band. Cut 2-3 dB in the beat between "
                      f"{lo:.0f} and {hi:.0f} Hz, or duck it against the "
                      f"vocal with a sidechain.")

        rows.append({
            "band": key, "label": label, "range_hz": [lo, hi],
            "beat_share_pct": _f(b_share * 100, 1),
            "vocal_share_pct": _f(v_share * 100, 1),
            "contention": _f(severity_score, 4),
            "severity": severity,
            "intelligibility_band": in_speech,
            "action": action,
        })

    worst = max(rows, key=lambda r: r["contention"] or 0) if rows else {}
    return {
        "bands": rows,
        "worst": worst,
        "intelligibility_range_hz": list(_INTELLIGIBILITY_HZ),
        "note": "Competition inside the intelligibility range costs the "
                "listener words, so it is weighted more heavily than overlap "
                "in the sub or air bands.",
    }


# -------------------------------------------------------------- balance ----
def _balance(beat: Decoded, vocal: Decoded) -> Dict:
    """Vocal-to-instrumental level relationship."""
    b_loud = prod.loudness(beat.stereo, beat.sr)
    v_loud = prod.loudness(vocal.stereo, vocal.sr)

    b_lufs, v_lufs = b_loud.get("integrated_lufs"), v_loud.get("integrated_lufs")
    if b_lufs is None or v_lufs is None:
        return {"available": False}

    delta = v_lufs - b_lufs
    return {
        "available": True,
        "beat_lufs": b_lufs,
        "vocal_lufs": v_lufs,
        "delta_lu": _f(delta, 2),
        "note": ("These are the levels of the two files as supplied, not of a "
                 "finished mix. Use them as a starting point for the vocal "
                 "fader, not as a target."),
    }


def _eq_curve(masking: Dict) -> Dict:
    """A concrete EQ move per contested band, for the beat.

    The cut goes on the beat rather than a boost on the vocal: raising the
    vocal into a crowded band makes both sources louder in the same place,
    which is what made it crowded. Values are deliberately conservative - a
    starting point to audition, not a correction to apply blind.
    """
    bands = [b for b in (masking.get("bands") or [])
             if b.get("severity") in ("some", "heavy")]
    if not bands:
        return {"available": False,
                "note": "No band is contested enough to need an EQ move."}

    moves = []
    for b in sorted(bands, key=lambda r: r.get("contention") or 0, reverse=True):
        heavy = b["severity"] == "heavy"
        lo, hi = b["range_hz"]
        centre = round((lo * hi) ** 0.5)       # geometric centre reads musically
        moves.append({
            "band": b["band"],
            "label": b["label"],
            "centre_hz": centre,
            "range_hz": [lo, hi],
            "gain_db": -3.0 if heavy else -1.5,
            "q": 1.0 if heavy else 0.7,
            "apply_to": "beat",
            "alternative": ("Sidechain the beat to the vocal in this band "
                            "instead, which keeps the energy when the vocal "
                            "is not there."),
        })

    return {
        "available": True,
        "target": "beat",
        "moves": moves[:4],
        "note": "Cut the beat rather than boosting the vocal: raising the "
                "vocal into a crowded band puts more energy where the "
                "problem already is. Audition these, do not apply blind.",
    }


# -------------------------------------------------------------- actions ----
def _actions(key: Dict, tempo: Dict, timing: Dict, masking: Dict,
             balance: Dict) -> List[Dict]:
    out: List[Dict] = []

    if key.get("available") and not key.get("compatible"):
        out.append({
            "kind": "key", "priority": "high",
            "title": "Keys do not match",
            "detail": key.get("note"),
            "semitone_shift": key.get("semitone_shift"),
        })

    if tempo.get("available") and not tempo.get("compatible"):
        out.append({
            "kind": "tempo", "priority": "high",
            "title": "Tempo gap is too wide to stretch cleanly",
            "detail": tempo.get("note"),
            "stretch_pct": tempo.get("stretch_pct"),
        })
    elif tempo.get("available") and abs(tempo.get("stretch_pct") or 0) >= 1.0:
        out.append({
            "kind": "tempo", "priority": "medium",
            "title": "Small tempo adjustment needed",
            "detail": tempo.get("note"),
            "stretch_pct": tempo.get("stretch_pct"),
        })

    if timing.get("available") and timing.get("suggested_nudge_ms"):
        out.append({
            "kind": "timing", "priority": "medium",
            "title": "Vocal is off the grid",
            "detail": timing.get("note"),
            "nudge_ms": timing.get("suggested_nudge_ms"),
        })

    for row in sorted(masking.get("bands", []),
                      key=lambda r: r.get("contention") or 0, reverse=True):
        if row.get("severity") == "clear" or not row.get("action"):
            continue
        out.append({
            "kind": "masking",
            "priority": "high" if row["severity"] == "heavy" else "medium",
            "title": f"Frequency clash in the {row['label'].lower()} band",
            "detail": row["action"],
            "band": row["band"],
            "range_hz": row["range_hz"],
        })

    return out


def _fit_score(key: Dict, tempo: Dict, masking: Dict) -> Optional[float]:
    """Single 0-100 figure for how well the two sit together."""
    score, counted = 0.0, 0

    if key.get("available"):
        score += 100.0 if key.get("compatible") else 40.0
        counted += 1
    if tempo.get("available"):
        stretch = abs(tempo.get("stretch_pct") or 0)
        score += max(0.0, 100.0 - stretch * 8.0)
        counted += 1

    bands = masking.get("bands") or []
    if bands:
        heavy = sum(1 for b in bands if b.get("severity") == "heavy")
        some = sum(1 for b in bands if b.get("severity") == "some")
        score += max(0.0, 100.0 - heavy * 25.0 - some * 8.0)
        counted += 1

    return _f(score / counted, 1) if counted else None
