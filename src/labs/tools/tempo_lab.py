"""Tempo Lab: BPM, the metrical alternatives, and how tight the timing is.

Two things are reported that a single-number BPM readout cannot express, and
both matter more than the headline figure:

Octave ambiguity. Whether a track is 70 or 140 BPM is often genuinely
undecidable - it depends which pulse a listener counts - so the half and double
readings are returned alongside the primary rather than silently discarded.

Tempo drift. A programmed track holds one tempo; a live or older recording does
not. Averaging that away hides the useful part, so the tempo curve and its
spread are both reported.

Grid tightness is measured relative to the detected beats, which means the
octave ambiguity cancels out of it entirely: a swing or push measurement is
correct whether the pulse was counted at 70 or 140.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..analysis import musical
from .base import Decoded, ToolSpec, _i

SPEC = ToolSpec(
    slug="tempo-lab",
    name="Tempo Lab",
    summary="BPM with metrical alternatives, tempo drift, and grid tightness.",
    inputs=("file",),
    typical_seconds=(1, 5),
    accuracy="Around 90% on steady 4/4 electronic, pop and hip-hop. Lower on "
             "live, rubato, classical and ambient material, where a stable "
             "pulse may not exist.",
    basis="Onset-strength autocorrelation and beat tracking, with deviation "
          "measured against the detected grid.",
    limitations=(
        "Half and double time are frequently ambiguous; every plausible "
        "reading is returned rather than one being guessed.",
        "Tracks without a steady pulse produce a low-confidence result, which "
        "is reported rather than hidden.",
    ),
)


def run(audio: Decoded, **_) -> Dict:
    y, sr = audio.musical, audio.sr_musical
    beats = audio.beats()

    rhythm = musical.rhythm(y, sr, audio.duration, tracker_beats=beats or None)
    groove = musical.groove(y, sr, beats) if beats else {}

    bpm = rhythm.get("bpm")

    return {
        "tempo": {
            "bpm": bpm,
            "bpm_tracked": rhythm.get("bpm_tracked"),
            "metrical_level": rhythm.get("metrical_level"),
            "metrical_reason": rhythm.get("metrical_reason"),
            "pulse_clarity": rhythm.get("pulse_clarity"),
            "confidence": _confidence(rhythm),
            "alternatives": _alternatives(bpm),
            "stability": _stability(rhythm),
            "curve": rhythm.get("bpm_curve") or [],
            "changes": _tempo_changes(rhythm.get("bpm_curve") or []),
        },
        "meter": {
            "time_signature": rhythm.get("time_signature"),
            "beats_per_bar": rhythm.get("beats_per_bar"),
            "beat_count": rhythm.get("beat_count"),
            "bar_count": rhythm.get("bar_count"),
        },
        "timing": _timing(groove),
        "grid": {
            "beat_times": rhythm.get("beat_times") or [],
            "beat_jitter_ms": rhythm.get("beat_jitter_ms"),
        },
        "headline": {
            "bpm": bpm,
            "alternatives": [a["bpm"] for a in _alternatives(bpm)],
            "feel": groove.get("quantization_class"),
            "swing_pct": groove.get("swing_pct"),
            "stability": _stability(rhythm).get("label"),
        },
    }


def _tempo_changes(curve: List[Dict], min_jump: float = 12.0) -> Dict:
    """Sustained tempo switches, as distinct from gradual drift.

    A half-time section or a beat switch is a step change that persists, not
    the wander a live performance produces. Requiring the new tempo to hold
    for several beats separates the two and keeps single-beat tracking slips
    from being reported as musical events.
    """
    if len(curve) < 12:
        return {"available": False,
                "note": "Too few beats to look for a tempo switch."}

    points = [(c.get("time"), c.get("bpm")) for c in curve
              if c.get("time") is not None and c.get("bpm")]
    if len(points) < 12:
        return {"available": False}

    hold = 4
    switches = []
    for i in range(hold, len(points) - hold):
        before = sorted(p[1] for p in points[i - hold:i])[hold // 2]
        after = sorted(p[1] for p in points[i:i + hold])[hold // 2]
        if abs(after - before) < min_jump:
            continue
        # Do not re-report the same switch on consecutive beats.
        if switches and points[i][0] - switches[-1]["at_s"] < 4.0:
            continue
        ratio = after / before if before else 1.0
        relation = ("double time" if 1.8 < ratio < 2.2 else
                    "half time" if 0.45 < ratio < 0.55 else
                    "tempo change")
        switches.append({
            "at_s": round(points[i][0], 1),
            "from_bpm": _i(before), "to_bpm": _i(after),
            "relationship": relation,
        })

    if not switches:
        return {"available": True, "count": 0,
                "note": "Tempo holds throughout; no switch detected."}

    return {
        "available": True,
        "count": len(switches),
        "switches": switches[:8],
        "note": f"{len(switches)} tempo switch(es) detected. Beat switches and "
                f"half-time sections show up here rather than as drift.",
    }


def _confidence(rhythm: Dict) -> Dict:
    """Pulse clarity restated as a label, so a weak result reads as weak.

    A confident number on ambient or rubato material is worse than an honest
    "no stable pulse", because the user cannot tell the two apart otherwise.
    """
    clarity = rhythm.get("pulse_clarity")
    if clarity is None:
        return {"label": "unknown", "score": None,
                "note": "Pulse clarity could not be measured."}
    if clarity >= 0.6:
        label, note = "high", "A strong, regular pulse is present."
    elif clarity >= 0.35:
        label, note = "moderate", ("A pulse is present but not dominant. Check "
                                   "the alternatives below.")
    else:
        label, note = "low", ("No strong periodic pulse was found. Treat the "
                              "BPM as approximate.")
    return {"label": label, "score": clarity, "note": note}


def _stability(rhythm: Dict) -> Dict:
    sigma = rhythm.get("bpm_stability_sigma")
    if sigma is None:
        return {}
    if sigma < 1.0:
        label, note = "locked", "Tempo is machine-steady across the track."
    elif sigma < 3.0:
        label, note = "steady", "Small tempo movement, typical of a tight performance."
    else:
        label, note = "drifting", ("Tempo moves noticeably across the track. "
                                   "Expect a live or hand-played source.")
    return {"label": label, "spread_bpm": sigma,
            "is_constant": rhythm.get("is_constant_tempo"), "note": note}


def _alternatives(bpm: Optional[float]) -> List[Dict]:
    """Half and double time, which are the dominant failure mode.

    Only readings inside a plausible musical range are offered, so a 170 BPM
    track does not suggest 340.
    """
    if not bpm:
        return []
    out: List[Dict] = []
    for factor, name in ((0.5, "half time"), (1.0, "as detected"),
                         (2.0, "double time")):
        v = bpm * factor
        if 40.0 <= v <= 220.0:
            out.append({"bpm": _i(v), "relationship": name,
                        "primary": factor == 1.0})
    return out


def _timing(groove: Dict) -> Dict:
    """Grid tightness. Independent of the octave question."""
    if not groove or not groove.get("available"):
        return {"available": False,
                "note": "No stable beat grid was found, so timing could not be "
                        "measured against one."}
    return {
        "available": True,
        "grid": groove.get("grid"),
        "grid_fit_error": groove.get("grid_fit_error"),
        "swing_pct": groove.get("swing_pct"),
        "swing_classification": groove.get("swing_classification"),
        "quantization_class": groove.get("quantization_class"),
        "quantization_note": groove.get("quantization_note"),
        "mean_abs_deviation_ms": groove.get("mean_abs_deviation_ms"),
        "mean_signed_deviation_ms": groove.get("mean_signed_deviation_ms"),
        "deviation_sigma_ms": groove.get("deviation_sigma_ms"),
        "pocket": groove.get("pocket"),
        "onset_count": groove.get("onset_count"),
        "deviation_histogram": groove.get("deviation_histogram") or [],
        "note": "Measured against the detected beat grid, so it is unaffected "
                "by half or double time ambiguity.",
    }
