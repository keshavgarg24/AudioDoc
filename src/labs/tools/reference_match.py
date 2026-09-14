"""Reference Match: how your track differs from a track you want to sound like.

Both files are normalised to the same integrated loudness before any spectral
comparison. Without that step the whole report degenerates into "the commercial
master is louder", which is true of every commercial master and tells the user
nothing they can act on.

What this is: a tonal-balance and mastering comparison. Every number is a
measured difference between two signals.

What this is not: a production tutorial. A band delta cannot distinguish "your
mix needs more presence" from "the reference has a bright lead vocal and your
track is instrumental". That limit is stated in the response rather than left
for the user to discover, because acting on the wrong reading wastes real work.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..analysis import musical
from ..analysis import production as prod
from .base import (
    Decoded,
    ToolSpec,
    _f,
    _i,
    band_delta,
    band_energy,
    loudness_normalise,
)

SPEC = ToolSpec(
    slug="reference-match",
    name="Reference Match",
    summary="Compare your track against a reference and get the differences "
            "that matter, band by band.",
    inputs=("file", "reference"),
    typical_seconds=(10, 20),
    accuracy="The differences are measurement-grade: both files are level "
             "matched, then compared band by band. Interpretation of a "
             "difference depends on the material.",
    basis="Level-matched spectral band energy, ITU-R BS.1770-4 loudness, "
          "stereo correlation and width, and dynamic range.",
    limitations=(
        "A spectral difference conflates mixing choices with instrumentation. "
        "If the reference has a vocal and your track does not, the difference "
        "in that band is not a mixing problem.",
        "Comparison is most reliable between tracks in a similar genre and "
        "arrangement density.",
    ),
)


def run(audio: Decoded, reference: Decoded, **_) -> Dict:
    # Level match first. Everything spectral below depends on this.
    a_norm = loudness_normalise(audio.mono, audio.sr)
    b_norm = loudness_normalise(reference.mono, reference.sr)

    a_bands = band_energy(a_norm, audio.sr)
    b_bands = band_energy(b_norm, reference.sr)
    deltas = band_delta(a_bands, b_bands)

    a_loud = prod.loudness(audio.stereo, audio.sr)
    b_loud = prod.loudness(reference.stereo, reference.sr)
    a_stereo = prod.stereo_field(audio.stereo, audio.sr)
    b_stereo = prod.stereo_field(reference.stereo, reference.sr)

    a_rhythm = musical.rhythm(audio.musical, audio.sr_musical, audio.duration,
                              tracker_beats=audio.beats() or None)
    b_rhythm = musical.rhythm(reference.musical, reference.sr_musical,
                              reference.duration,
                              tracker_beats=reference.beats() or None)

    a_harm = musical.harmony(audio.musical, audio.sr_musical, audio.beats())
    b_harm = musical.harmony(reference.musical, reference.sr_musical,
                             reference.beats())

    actions = _actions(deltas, a_loud, b_loud, a_stereo, b_stereo)

    return {
        "method": {
            "level_matched": True,
            "match_target_lufs": -18.0,
            "note": "Both files were normalised to the same integrated "
                    "loudness before spectral comparison, so the band "
                    "differences describe tone rather than level.",
        },
        "tonal_balance": {
            "bands": deltas,
            "subject": list(a_bands.values()),
            "reference": list(b_bands.values()),
        },
        "loudness": _compare_loudness(a_loud, b_loud),
        "stereo": _compare_stereo(a_stereo, b_stereo),
        "musical": {
            "tempo": {
                "subject_bpm": a_rhythm.get("bpm"),
                "reference_bpm": b_rhythm.get("bpm"),
                "delta_bpm": _i((a_rhythm.get("bpm") or 0) - (b_rhythm.get("bpm") or 0)),
            },
            "key": {
                "subject": a_harm.get("key"),
                "subject_camelot": a_harm.get("camelot"),
                "reference": b_harm.get("key"),
                "reference_camelot": b_harm.get("camelot"),
                "same_key": bool(a_harm.get("camelot")
                                 and a_harm.get("camelot") == b_harm.get("camelot")),
            },
        },
        "actions": actions,
        "headline": {
            "match_score": _match_score(deltas, a_loud, b_loud),
            "biggest_difference": actions[0]["title"] if actions else None,
            "action_count": len(actions),
        },
        "limitations": list(SPEC.limitations),
    }


def _compare_loudness(a: Dict, b: Dict) -> Dict:
    def delta(key: str) -> Optional[float]:
        av, bv = a.get(key), b.get(key)
        return _f(av - bv, 2) if av is not None and bv is not None else None

    return {
        "subject": {
            "integrated_lufs": a.get("integrated_lufs"),
            "loudness_range_lu": a.get("loudness_range_lu"),
            "true_peak_dbtp": a.get("true_peak_dbtp"),
            "plr_db": a.get("plr_db"),
        },
        "reference": {
            "integrated_lufs": b.get("integrated_lufs"),
            "loudness_range_lu": b.get("loudness_range_lu"),
            "true_peak_dbtp": b.get("true_peak_dbtp"),
            "plr_db": b.get("plr_db"),
        },
        "delta": {
            "integrated_lu": delta("integrated_lufs"),
            "loudness_range_lu": delta("loudness_range_lu"),
            "plr_db": delta("plr_db"),
        },
    }


def _compare_stereo(a: Dict, b: Dict) -> Dict:
    return {
        "subject": {"width_pct": a.get("width_pct"),
                    "correlation": a.get("correlation"),
                    "low_end_mono": a.get("low_end_mono")},
        "reference": {"width_pct": b.get("width_pct"),
                      "correlation": b.get("correlation"),
                      "low_end_mono": b.get("low_end_mono")},
        "delta": {
            "width_pct": _f((a.get("width_pct") or 0) - (b.get("width_pct") or 0), 1),
        },
    }


def _actions(deltas: List[Dict], a_loud: Dict, b_loud: Dict,
             a_stereo: Dict, b_stereo: Dict) -> List[Dict]:
    """Ordered, concrete changes. Biggest spectral gap first."""
    out: List[Dict] = []

    for row in sorted(deltas, key=lambda d: abs(d.get("delta_db") or 0),
                      reverse=True):
        d = row.get("delta_db")
        if d is None or abs(d) < 1.0:
            continue
        out.append({
            "kind": "eq",
            "priority": "high" if abs(d) >= 2.5 else "medium",
            "title": f"{row['label']} is {abs(d):.1f} dB "
                     f"{'hotter' if d > 0 else 'lighter'} than the reference",
            "detail": row.get("action"),
            "band": row["band"],
            "range_hz": row["range_hz"],
            "delta_db": d,
        })

    lra_a, lra_b = a_loud.get("loudness_range_lu"), b_loud.get("loudness_range_lu")
    if lra_a is not None and lra_b is not None and abs(lra_a - lra_b) >= 2.0:
        tighter = lra_a < lra_b
        out.append({
            "kind": "dynamics",
            "priority": "medium",
            "title": f"Dynamic range is {'tighter' if tighter else 'wider'} "
                     f"than the reference by {abs(lra_a - lra_b):.1f} LU",
            "detail": ("Ease off the master bus compression or limiting to let "
                       "the arrangement breathe."
                       if tighter else
                       "The reference is more consistently loud. More gentle "
                       "bus compression would close the gap."),
            "delta_lu": _f(lra_a - lra_b, 2),
        })

    wa, wb = a_stereo.get("width_pct"), b_stereo.get("width_pct")
    if wa is not None and wb is not None and abs(wa - wb) >= 12.0:
        out.append({
            "kind": "stereo",
            "priority": "medium",
            "title": f"Stereo image is {'wider' if wa > wb else 'narrower'} "
                     f"than the reference",
            "detail": (f"Your width reads {wa:.0f}% against the reference's "
                       f"{wb:.0f}%."),
            "delta_pct": _f(wa - wb, 1),
        })

    if a_stereo.get("low_end_mono") is False and b_stereo.get("low_end_mono") is True:
        out.append({
            "kind": "stereo",
            "priority": "high",
            "title": "Low end is wide where the reference keeps it mono",
            "detail": "Collapse everything below roughly 120 Hz to mono. Wide "
                      "sub energy is unstable on club systems and vinyl.",
        })

    return out


def _match_score(deltas: List[Dict], a_loud: Dict, b_loud: Dict) -> Optional[float]:
    """A single 0-100 closeness figure, driven by the spectral gaps.

    Deliberately dominated by tonal balance: it is the part a user can act on
    and the part that is genuinely comparable between two arbitrary tracks.
    """
    vals = [abs(r["delta_db"]) for r in deltas if r.get("delta_db") is not None]
    if not vals:
        return None
    mean_gap = sum(vals) / len(vals)
    # 0 dB mean gap -> 100; 6 dB mean gap -> 0.
    score = max(0.0, min(100.0, 100.0 * (1.0 - mean_gap / 6.0)))
    return _f(score, 1)
