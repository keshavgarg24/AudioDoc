"""Master Check: loudness, true peak, dynamics and platform readiness.

Everything reported here is measurement against a published standard
(ITU-R BS.1770-4 for loudness, the streaming services' own published
normalisation targets), not estimation. The numbers should agree with any
other compliant meter to within a tenth of a unit.

The one part that is opinion rather than measurement is the tonal-balance
commentary, which is scored against this platform's genre profiles. It is
returned under `guidance` and labelled as such, so a user can tell which
numbers are facts and which are advice.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..analysis import production as prod
from .base import Decoded, ToolSpec, band_energy

SPEC = ToolSpec(
    slug="master-check",
    name="Master Check",
    summary="Loudness, true peak, dynamics and per-platform delivery readiness.",
    inputs=("file",),
    typical_seconds=(3, 10),
    accuracy="Measurement-grade. Implements ITU-R BS.1770-4, so results match "
             "any compliant meter to within 0.1 LU.",
    basis="ITU-R BS.1770-4 integrated loudness, 4x-oversampled true peak, and "
          "each streaming platform's published normalisation target.",
    limitations=(
        "Tonal-balance commentary is guidance benchmarked against genre "
        "profiles, not a measured standard.",
        "Platform targets change occasionally; they are versioned with the API.",
    ),
)


def run(audio: Decoded, genre: Optional[str] = None, **_) -> Dict:
    loud = prod.loudness(audio.stereo, audio.sr)
    stereo = prod.stereo_field(audio.stereo, audio.sr)
    design = prod.sound_design(audio.mono, audio.sr)
    encoding = prod.encoding(audio.path, audio.sr, audio.mono)
    readiness = prod.release_readiness(loud, stereo)
    bands = band_energy(audio.mono, audio.sr)

    instrumental = _instrumental_note(audio)

    return {
        "instrumental_mode": instrumental,
        "loudness": loud,
        "stereo": stereo,
        "sound_design": design,
        "encoding": encoding,
        "release_readiness": readiness,
        "tonal_balance": {
            "bands": list(bands.values()),
            "note": "Energy share per band after level normalisation.",
        },
        "guidance": _guidance(loud, stereo, bands, genre),
        "headline": _headline(loud, readiness),
    }


def _instrumental_note(audio: Decoded) -> Dict:
    """Whether to judge this as a finished master or as a beat.

    A type beat mastered to -8 LUFS leaves nothing for a vocal to sit in. The
    platform targets below still apply to the eventual release, but a producer
    delivering an instrumental should not be told to push into them yet.
    """
    from ..analysis import features as feat
    from ..analysis.industry import vocal_presence

    try:
        spectral = feat._spectral(audio.musical, audio.sr_musical)
        tonal = feat._tonal(audio.musical, audio.sr_musical)
        presence = vocal_presence({"spectral": spectral, "tonal": tonal}, {})
    except Exception:
        return {"detected": None}

    if presence.get("verdict") != "likely instrumental":
        return {"detected": False, "verdict": presence.get("verdict")}

    return {
        "detected": True,
        "verdict": presence.get("verdict"),
        "recommended_lufs": -14.0,
        "note": "This looks like an instrumental. Leave 3 to 6 LU more "
                "headroom than a finished master needs: a lead vocal has to "
                "fit on top, and a beat already pushed to a streaming target "
                "leaves nowhere for it to go. Aim near -14 LUFS on the "
                "instrumental and let the final master do the rest.",
        "caveat": presence.get("caveat"),
    }


def _headline(loud: Dict, readiness: Dict) -> Dict:
    # release_readiness reports "ready" with a single reassuring line, so an
    # issue count is only meaningful once the status says otherwise.
    status = readiness.get("status", "unknown")
    issues = readiness.get("issues") or []
    return {
        "integrated_lufs": loud.get("integrated_lufs"),
        "true_peak_dbtp": loud.get("true_peak_dbtp"),
        "loudness_range_lu": loud.get("loudness_range_lu"),
        "status": status,
        "issue_count": len(issues) if status != "ready" else 0,
    }


def _guidance(loud: Dict, stereo: Dict, bands: Dict, genre: Optional[str]) -> Dict:
    """Opinion layer. Explicitly separated from the measurements above."""
    from ..artist import genres as G

    key = G.resolve(genre) if genre else None
    profile = G.GENRES.get(key) if key else None

    notes: List[Dict] = []
    lufs = loud.get("integrated_lufs")

    if profile and lufs is not None:
        target = profile["lufs"]
        delta = lufs - target
        if abs(delta) > 1.5:
            notes.append({
                "topic": "loudness",
                "severity": "minor" if abs(delta) < 3 else "major",
                "message": (
                    f"Integrated loudness is {lufs} LUFS against a "
                    f"{profile['label']} norm of {target} LUFS "
                    f"({'louder' if delta > 0 else 'quieter'} by "
                    f"{abs(delta):.1f} LU)."),
            })

    tp = loud.get("true_peak_dbtp")
    if tp is not None and tp > -1.0:
        notes.append({
            "topic": "true_peak",
            "severity": "major",
            "message": (
                f"True peak reaches {tp} dBTP. Lossy encoders overshoot, so "
                f"deliver at -1.0 dBTP or lower to avoid distortion after "
                f"transcoding."),
        })

    lra = loud.get("loudness_range_lu")
    if lra is not None and lra < 3.0:
        notes.append({
            "topic": "dynamics",
            "severity": "minor",
            "message": (
                f"Loudness range is {lra} LU, which is very compressed. "
                f"Platform normalisation will turn this down without giving "
                f"back any punch."),
        })

    corr = stereo.get("correlation")
    if corr is not None and corr < 0.0:
        notes.append({
            "topic": "phase",
            "severity": "major",
            "message": (
                f"Stereo correlation is {corr}, meaning parts of the mix "
                f"cancel in mono. Club and phone playback will lose level."),
        })

    sub = bands.get("sub", {}).get("share_pct")
    if sub is not None and sub > 45:
        notes.append({
            "topic": "low_end",
            "severity": "minor",
            "message": (
                f"The sub band holds {sub}% of total energy, which will eat "
                f"headroom that the rest of the mix needs."),
        })

    return {
        "kind": "guidance",
        "disclaimer": "Guidance is benchmarked against genre profiles and is "
                      "editorial. The measurements above are not.",
        "genre": profile["label"] if profile else None,
        "notes": notes,
    }
