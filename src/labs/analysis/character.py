"""Perceptual character profile: how the track reads to a listener.

Derived entirely from measurements already taken elsewhere, so this module is
cheap. It exists to give the report a human-facing summary layer: a radar of
perceptual axes, mood tags, how much room a vocal would have, and a one line
description in the language a brief would use.
"""
from __future__ import annotations

import math
from typing import Dict, List


def _f(x, nd: int = 3) -> float:
    v = float(x)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(v, nd)


def _clip01(x) -> float:
    return _f(max(0.0, min(1.0, float(x))))


def _norm(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clip01((value - lo) / (hi - lo))


def radar(features: Dict, musical: Dict, loud: Dict) -> List[Dict]:
    """Perceptual axes, each normalised to 0 to 1 with its source named."""
    sp = features.get("spectral", {})
    dy = features.get("dynamics", {})
    tn = features.get("tonal", {})
    on = features.get("onsets", {})
    gr = musical.get("groove", {})

    bands = {b["name"]: b["share"] for b in sp.get("bands", [])}
    low = bands.get("Sub", 0) + bands.get("Bass", 0)
    high = bands.get("Presence", 0) + bands.get("Air", 0)

    axes = [
        {"axis": "Energy",
         "value": _norm(loud.get("integrated_lufs", -20), -24, -6),
         "basis": "integrated loudness"},
        {"axis": "Brightness",
         "value": _norm(sp.get("centroid_hz", 0), 500, 5000),
         "basis": "spectral centroid"},
        {"axis": "Low end weight",
         "value": _clip01(low),
         "basis": "sub and bass energy share"},
        {"axis": "Air",
         "value": _clip01(high * 6),
         "basis": "presence and air energy share"},
        {"axis": "Dynamics",
         "value": _norm(dy.get("crest_factor_db", 0), 5, 20),
         "basis": "crest factor"},
        {"axis": "Rhythmic drive",
         "value": _norm(on.get("onset_density", 0), 0.5, 6),
         "basis": "onset density"},
        {"axis": "Tonal clarity",
         "value": _clip01(tn.get("key_clarity", 0) * 5),
         "basis": "key detection margin"},
        {"axis": "Groove looseness",
         "value": _norm(gr.get("mean_abs_deviation_ms", 0), 0, 40),
         "basis": "timing deviation from grid"},
    ]
    return axes


def moods(features: Dict, musical: Dict, loud: Dict) -> List[Dict]:
    """Mood tags with a weight, derived from measured quantities."""
    sp = features.get("spectral", {})
    dy = features.get("dynamics", {})
    on = features.get("onsets", {})
    hm = musical.get("harmony", {})
    rh = musical.get("rhythm", {})

    centroid = sp.get("centroid_hz", 2000)
    bands = {b["name"]: b["share"] for b in sp.get("bands", [])}
    low = bands.get("Sub", 0) + bands.get("Bass", 0)
    density = on.get("onset_density", 2)
    bpm = rh.get("bpm", 120) or 120
    minor = hm.get("mode") == "minor"
    crest = dy.get("crest_factor_db", 12)

    raw = {
        "dark": _clip01((1 - _norm(centroid, 500, 4000)) * 0.6 + low * 0.5
                        + (0.2 if minor else 0)),
        "bright": _clip01(_norm(centroid, 1000, 5000)),
        "energetic": _clip01(_norm(bpm, 80, 170) * 0.5 + _norm(density, 1, 6) * 0.5),
        "chill": _clip01((1 - _norm(bpm, 70, 160)) * 0.5
                         + (1 - _norm(density, 0.5, 5)) * 0.5),
        "melancholy": _clip01((0.55 if minor else 0.1)
                              + (1 - _norm(centroid, 800, 4000)) * 0.35),
        "aggressive": _clip01((1 - _norm(crest, 6, 18)) * 0.6
                              + _norm(density, 2, 7) * 0.4),
        "spacious": _clip01(_norm(crest, 8, 20) * 0.7),
    }
    ranked = sorted(raw.items(), key=lambda kv: -kv[1])
    return [{"mood": k, "weight": _f(v)} for k, v in ranked[:5] if v > 0.35]


def vocal_space(features: Dict) -> Dict:
    """How much room a vocal would have in the midrange."""
    sp = features.get("spectral", {})
    bands = {b["name"]: b["share"] for b in sp.get("bands", [])}
    # A lead vocal occupies roughly 300 Hz to 4 kHz.
    mid = bands.get("Mid", 0) + bands.get("Low mid", 0) * 0.5 + bands.get("High mid", 0) * 0.5

    if mid < 0.12:
        verdict = "wide open, plenty of room for a vocal"
        eq = "no carving needed"
    elif mid < 0.28:
        verdict = "workable, a vocal would sit with light carving"
        eq = "a gentle 2 to 3 dB dip around 1 to 3 kHz would seat a lead"
    else:
        verdict = "crowded, the midrange is already busy"
        eq = "needs dynamic EQ or sidechain to make space for a lead"

    return {
        "midrange_occupancy": _f(mid),
        "midrange_occupancy_pct": _f(mid * 100, 1),
        "verdict": verdict,
        "eq_suggestion": eq,
    }


def suitability(features: Dict, musical: Dict, vocal: Dict) -> List[str]:
    """Practical placements this track would work for."""
    out: List[str] = []
    on = features.get("onsets", {})
    rh = musical.get("rhythm", {})
    dy = features.get("dynamics", {})

    bpm = rh.get("bpm", 120) or 120
    density = on.get("onset_density", 2)
    crest = dy.get("crest_factor_db", 12)

    if vocal.get("midrange_occupancy", 1) < 0.28:
        out.append("rap or vocal topline, the midrange is open")
    if density < 3 and bpm < 130:
        out.append("background, study or lo-fi playlist placement")
    if crest > 12:
        out.append("film, trailer or game underscore, the dynamics carry")
    if 115 <= bpm <= 140 and density > 2.5:
        out.append("club or DJ set, the tempo and drive fit a floor")
    if bpm < 100 and density < 3:
        out.append("podcast or video bed")
    if not out:
        out.append("general production use")
    return out


def one_liner(features: Dict, musical: Dict, mood_list: List[Dict]) -> str:
    """A single sentence in the language a brief would use."""
    rh = musical.get("rhythm", {})
    hm = musical.get("harmony", {})
    dr = musical.get("drums", {})
    sd = features.get("sound_design", {})

    bpm = rh.get("bpm")
    key = hm.get("key")
    mood = mood_list[0]["mood"] if mood_list else "neutral"
    kick = dr.get("kick_pattern")

    # Prepositional phrases run on without commas; descriptive clauses take
    # them. Joining everything with commas reads like a list, not a sentence.
    head = f"A {mood} instrumental"
    if bpm:
        head += f" at {bpm:.0f} BPM"
    if key:
        head += f" in {key}"

    clauses: List[str] = []
    if kick and kick != "unknown":
        clauses.append(f"built on a {kick} kick pattern")
    reverb = sd.get("reverb_character")
    if reverb:
        clauses.append(f"sitting in a {reverb}")

    return head + (", " + ", ".join(clauses) if clauses else "") + "."


def build(features: Dict, musical_data: Dict, loud: Dict) -> Dict:
    """Assemble the character layer. Safe against any section being absent."""
    features = features or {}
    musical_data = musical_data or {}
    loud = loud or {}

    mood_list = moods(features, musical_data, loud)
    vocal = vocal_space(features)
    return {
        "radar": radar(features, musical_data, loud),
        "moods": mood_list,
        "vocal_space": vocal,
        "suitable_for": suitability(features, musical_data, vocal),
        "summary": one_liner(features, musical_data, mood_list),
    }
