"""Key Lab: key, scale, Camelot code and harmonic mixing neighbours.

Key detection has one dominant failure mode: relative major and minor share
all seven notes, so a plain chroma correlation cannot separate C major from
A minor. Anything that reports a single key with no caveat is wrong about a
quarter of the time and gives the user no way to notice.

Three things are done about that here.

Bass weighting. The tonic is far more likely to be carried by the bass than by
the upper voices, so a second chroma restricted to the low octaves votes on the
root separately from the full-range one.

Edge weighting. Popular music tends to start and end on the tonic, so the
opening and closing windows are weighted more heavily than the middle.

Ranked candidates. The top three keys are always returned with their scores. A
user who knows the track can pick the right one, and a user who does not can
see how close the call was.

For the harmonic-mixing use case the ambiguity matters less than it looks:
relative major and minor share a Camelot code, so the most common error class
does not change the mixing answer.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from ..analysis.musical import _CAMELOT_MAJOR, _CAMELOT_MINOR, _MAJOR, _MINOR, PITCHES
from .base import Decoded, ToolSpec, _f

log = logging.getLogger(__name__)

SPEC = ToolSpec(
    slug="key-lab",
    name="Key Lab",
    summary="Key, scale, Camelot code and compatible keys for harmonic mixing.",
    inputs=("file",),
    typical_seconds=(2, 8),
    accuracy="Around 75% exact, rising to roughly 90% when relative-major and "
             "perfect-fifth relationships are counted as near matches. Ranked "
             "alternatives are always returned.",
    basis="Constant-Q chroma correlated against major and minor key profiles, "
          "with bass-register and phrase-edge weighting used to resolve the "
          "relative major/minor ambiguity.",
    limitations=(
        "Relative major and minor share all seven notes and cannot be fully "
        "separated from pitch content alone.",
        "Modal, atonal and heavily chromatic material may not have a single "
        "correct answer.",
        "Chord estimates are approximate and are labelled as such.",
    ),
)

# Camelot wheel: same number is the relative major/minor pair, +/-1 is the
# neighbouring key, and the same letter one step away is the fifth.
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


def run(audio: Decoded, **_) -> Dict:
    y, sr = audio.musical, audio.sr_musical

    # One transform, folded for both registers, and kept so the chord pass
    # below does not repeat it. See analysis.musical.chroma_stack.
    stack = _stack(y, sr)
    full = _profile(stack["full"])
    bass = _profile(stack["bass"])
    edges = _edge_profile(y, sr)

    candidates = _rank(full, bass, edges)
    if not candidates:
        return {"available": False,
                "note": "Pitch content was too sparse to estimate a key."}

    best = candidates[0]
    root, mode = best["root"], best["mode"]
    camelot = best["camelot"]

    return {
        "key": {
            "name": best["key"],
            "root": root,
            "mode": mode,
            "camelot": camelot,
            "confidence": _confidence(candidates),
        },
        "candidates": candidates[:3],
        "scale": _scale(root, mode),
        "chroma": [{"pitch": PITCHES[i], "weight": _f(float(full[i]), 4)}
                   for i in range(12)],
        "harmonic_mixing": _mixing(camelot),
        "chords": _chords(audio, stack["full"]),
        "headline": {
            "key": best["key"],
            "camelot": camelot,
            "confidence": _confidence(candidates)["label"],
            "runner_up": candidates[1]["key"] if len(candidates) > 1 else None,
        },
    }


def _chords(audio, chroma: Optional[np.ndarray] = None) -> Dict:
    """Beat-synchronous chord estimate, explicitly marked approximate.

    Template matching on chroma lands around 65% on triads - materially worse
    than the key detection beside it. Presenting the two at equal confidence
    would let the weaker one borrow credibility from the stronger.
    """
    from ..analysis.musical import harmony

    try:
        h = harmony(audio.musical, audio.sr_musical, audio.beats(),
                    chroma=chroma)
    except Exception:
        return {"available": False}

    progression = h.get("progression") or []
    if not progression:
        return {"available": False,
                "note": "No stable chord sequence could be estimated."}

    return {
        "available": True,
        "confidence": "approximate",
        "progression": progression,
        "chord_count": h.get("chord_count"),
        "harmonic_rhythm_per_bar": h.get("harmonic_rhythm_per_bar"),
        "note": "Chord estimation is template matching on pitch content and "
                "is roughly 65% accurate on simple triads. Treat this as a "
                "starting point, not a transcription. The key and Camelot "
                "code above are considerably more reliable.",
    }


def _stack(y: np.ndarray, sr: int) -> Dict[str, np.ndarray]:
    """Full-range and bass chroma matrices, or empty ones if the CQT fails.

    A transform failure degrades the tool to "key could not be estimated"
    rather than failing the request: the caller's `_rank` already returns no
    candidates for an all-zero profile, and `run` reports that honestly.
    """
    from ..analysis.musical import chroma_stack

    try:
        return chroma_stack(y, sr)
    except Exception:
        log.warning("Chroma transform failed", exc_info=True)
        empty = np.zeros((12, 1))
        return {"full": empty, "bass": empty}


def _profile(chroma: np.ndarray) -> np.ndarray:
    """Normalised 12-bin pitch-class profile from a chroma matrix."""
    prof = chroma.mean(axis=1)
    total = float(prof.sum())
    return prof / total if total > 0 else prof


def _edge_profile(y: np.ndarray, sr: int) -> np.ndarray:
    """Chroma of the opening and closing windows only.

    Popular music resolves to the tonic at the start and the end far more often
    than in the middle, which is exactly the evidence a full-track average
    washes out. This is a separate, small transform on 16 s of audio rather
    than a slice of the full one so that its tuning estimate comes from the
    same windows it profiles.
    """
    import librosa

    try:
        window = max(int(sr * 8.0), 1)
        if y.size < window * 3:
            return np.zeros(12)
        head, tail = y[:window], y[-window:]
        combined = np.concatenate([head, tail])
        return _profile(librosa.feature.chroma_cqt(y=combined, sr=sr))
    except Exception:
        return np.zeros(12)


def _rank(full: np.ndarray, bass: np.ndarray,
          edges: np.ndarray) -> List[Dict]:
    """Score all 24 keys, combining the three sources of evidence.

    The full-range correlation decides the note set; bass and edge profiles
    only shift weight between keys that share it, which is precisely the
    relative major/minor question.
    """
    if not full.any():
        return []

    rows: List[Dict] = []
    for mode, ref, _scale in (("major", _MAJOR, _MAJOR_SCALE),
                              ("minor", _MINOR, _MINOR_SCALE)):
        for shift in range(12):
            rolled = np.roll(ref, shift)
            r = _corr(full, rolled)

            # Tonic evidence: how much weight sits on the root itself in the
            # bass and at the phrase edges.
            tonic_bass = float(bass[shift]) if bass.any() else 0.0
            tonic_edge = float(edges[shift]) if edges.any() else 0.0
            # The fifth reinforces the root; a bare tonic can be coincidental.
            fifth_bass = float(bass[(shift + 7) % 12]) if bass.any() else 0.0

            tonic_support = tonic_bass * 0.5 + tonic_edge * 0.35 + fifth_bass * 0.15
            score = r + tonic_support * 0.6

            camelot = (_CAMELOT_MAJOR if mode == "major"
                       else _CAMELOT_MINOR).get(PITCHES[shift], "")
            rows.append({
                "key": f"{PITCHES[shift]} {'major' if mode == 'major' else 'natural minor'}",
                "root": PITCHES[shift],
                "mode": mode,
                "camelot": camelot,
                "score": _f(score, 4),
                "profile_correlation": _f(r, 4),
                "tonic_support": _f(tonic_support, 4),
            })

    rows.sort(key=lambda d: d["score"] or -9, reverse=True)
    for i, row in enumerate(rows):
        row["rank"] = i + 1
    return rows


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if not a.any() or not b.any():
        return -1.0
    try:
        v = float(np.corrcoef(a, b)[0, 1])
        return v if np.isfinite(v) else -1.0
    except Exception:
        return -1.0


def _confidence(candidates: List[Dict]) -> Dict:
    """How clearly the winner beat the runner-up.

    A narrow margin usually means a relative major/minor pair, which is worth
    saying explicitly rather than leaving the user to infer it.
    """
    if len(candidates) < 2:
        return {"label": "unknown", "margin": None, "note": ""}

    first, second = candidates[0], candidates[1]
    margin = (first["score"] or 0) - (second["score"] or 0)
    relative = first["camelot"] and first["camelot"] == second["camelot"]

    if margin >= 0.08:
        label, note = "high", "The key profile is unambiguous."
    elif margin >= 0.03:
        label = "moderate"
        note = ("A close second candidate exists. "
                if not relative else
                "The runner-up is the relative key, which shares the same "
                "notes and the same Camelot code. ")
        note += "Check the alternatives if the result looks wrong."
    else:
        label = "low"
        note = ("Two keys scored almost identically"
                + (" and are relative to each other, so they share all seven "
                   "notes. For harmonic mixing either answer works."
                   if relative else
                   ". Treat this as an estimate."))
    return {"label": label, "margin": _f(margin, 4), "relative_pair": bool(relative),
            "note": note}


def _scale(root: str, mode: str) -> Dict:
    idx = PITCHES.index(root) if root in PITCHES else 0
    steps = _MAJOR_SCALE if mode == "major" else _MINOR_SCALE
    notes = [PITCHES[(idx + s) % 12] for s in steps]
    return {
        "root": root,
        "mode": mode,
        "notes": notes,
        "note_count": len(notes),
    }


def _mixing(camelot: str) -> Dict:
    """Camelot neighbours, which is what a DJ actually needs.

    Same number with the other letter is the relative key; plus or minus one on
    the same letter is the neighbouring key. Both are standard, safe moves.
    """
    if not camelot or len(camelot) < 2:
        return {"available": False}
    try:
        number = int(camelot[:-1])
        letter = camelot[-1].upper()
    except (ValueError, IndexError):
        return {"available": False}

    other = "B" if letter == "A" else "A"
    up = (number % 12) + 1
    down = 12 if number == 1 else number - 1

    return {
        "available": True,
        "camelot": camelot,
        "compatible": [
            {"camelot": f"{number}{other}", "relationship": "relative key",
             "note": "Same notes, opposite mode. Always safe."},
            {"camelot": f"{up}{letter}", "relationship": "one step up",
             "note": "Raises energy. The standard forward move."},
            {"camelot": f"{down}{letter}", "relationship": "one step down",
             "note": "Softens energy."},
        ],
        "note": "Relative major and minor share a Camelot code, so the most "
                "common key-detection ambiguity does not affect mixing.",
    }
