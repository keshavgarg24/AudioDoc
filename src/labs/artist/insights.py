"""Artist-facing analytics built on the existing measurement passes.

Nothing here runs new DSP. Every number comes from features.py, musical.py,
production.py or industry.py; this module reframes those measurements against
genre references so they become decisions an artist can act on.

Five products:

  genre_fit          which genres this track already sits in, ranked
  genre_transform    what to change to move it into a target genre
  hit_potential      a commercial readiness benchmark with per-factor actions
  release_readiness  concrete blockers between this file and a release
  mix_report         hook timing, tonal balance, low end and stereo diagnostics

On hit_potential: this is a *benchmark against genre norms*, not a prediction.
Chart performance is driven by marketing, playlisting and audience, none of
which are in the audio. Every factor below is measurable, explainable, and
paired with an action, which is the part that is actually useful.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from ..artist import genres as G

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _f(x, nd: int = 2) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return round(v, nd)


def _i(x) -> Optional[int]:
    """Whole number, for tempo. See `labs.tools.base._i` for the reasoning."""
    v = _f(x, 0)
    return None if v is None else int(v)


def _get(d: Optional[Dict], *path, default=None):
    """Safe nested lookup: _get(report, 'musical', 'rhythm', 'bpm')."""
    cur = d or {}
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _band_score(value: Optional[float], lo: float, hi: float,
                tolerance: float = 0.5) -> Optional[float]:
    """100 inside [lo, hi], falling off outside over `tolerance` × the width."""
    if value is None:
        return None
    if lo <= value <= hi:
        return 100.0
    width = max(hi - lo, 1e-6)
    slack = width * max(tolerance, 0.05)
    dist = (lo - value) if value < lo else (value - hi)
    return round(max(0.0, 100.0 * (1.0 - dist / slack)), 1)


def _target_score(value: Optional[float], target: float,
                  slack: float) -> Optional[float]:
    """100 at target, decaying linearly to 0 at target ± slack."""
    if value is None:
        return None
    dist = abs(value - target)
    return round(max(0.0, 100.0 * (1.0 - dist / max(slack, 1e-6))), 1)


def _grade(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 85:
        return "strong"
    if score >= 65:
        return "solid"
    if score >= 45:
        return "needs work"
    return "weak"


# --------------------------------------------------------------------------
# measurement extraction
# --------------------------------------------------------------------------
def _measure(report: Dict) -> Dict:
    """Pull the handful of scalars every product below needs."""
    mus, prod = report.get("musical") or {}, report.get("production") or {}
    feat, ind = report.get("features") or {}, report.get("industry") or {}
    cat = _get(ind, "catalogue_features", default={}) or {}
    struct = _get(ind, "structure", default={}) or {}

    bpm = _get(mus, "rhythm", "bpm")
    # A half-time genre reads at double the tracked tempo and vice versa; keep
    # both so genre matching can consider either reading.
    return {
        "bpm": _i(bpm),
        "bpm_double": _i(bpm * 2) if bpm else None,
        "bpm_half": _i(bpm / 2) if bpm else None,
        "time_signature": _get(mus, "rhythm", "time_signature"),
        "swing_pct": _f(_get(mus, "groove", "swing_pct"), 1),
        "quantization": _get(mus, "groove", "quantization_class"),
        "timing_deviation_ms": _f(_get(mus, "groove", "mean_abs_deviation_ms"), 1),
        "key": _get(mus, "harmony", "key"),
        "camelot": _get(mus, "harmony", "camelot"),
        "progression": _get(mus, "harmony", "progression"),
        "section_count": _get(mus, "arrangement", "section_count") or struct.get("section_count"),
        "sections": _get(mus, "arrangement", "sections") or [],

        "lufs": _f(_get(prod, "loudness", "integrated_lufs"), 1),
        "true_peak": _f(_get(prod, "loudness", "true_peak_dbtp"), 2),
        "lra": _f(_get(prod, "loudness", "loudness_range_lu"), 1),
        "plr": _f(_get(prod, "loudness", "plr_db"), 1),
        "clipped": _get(prod, "loudness", "clipped_samples") or 0,
        "width_pct": _f(_get(prod, "stereo", "width_pct"), 1),
        "mono_compatible": _get(prod, "stereo", "mono_compatible"),
        "low_end_mono": _get(prod, "stereo", "low_end_mono"),
        "correlation": _f(_get(prod, "stereo", "correlation"), 3),

        "centroid_hz": _f(_get(feat, "spectral", "centroid_hz"), 1),
        "rolloff85_hz": _f(_get(feat, "spectral", "rolloff85_hz"), 1),
        "crest_db": _f(_get(feat, "dynamics", "crest_factor_db"), 2),
        "bands": _get(feat, "spectral", "bands", default=[]) or [],

        "energy": _f(cat.get("energy"), 3),
        "danceability": _f(cat.get("danceability"), 3),
        "valence": _f(cat.get("valence"), 3),
        "instrumentalness": _f(cat.get("instrumentalness"), 3),
        "speechiness": _f(cat.get("speechiness"), 3),
        "acousticness": _f(cat.get("acousticness"), 3),

        "intro_s": _f(struct.get("intro_length_s"), 1),
        "first_peak_s": _f(struct.get("first_peak_s"), 1),
        "vocal_presence": _get(ind, "vocal_presence", "verdict"),
        "midrange_share": _f(_get(ind, "vocal_presence", "midrange_share"), 3),
        "duration": _f(_get(report, "source", "duration_seconds"), 1),
    }


# --------------------------------------------------------------------------
# 1. genre fit
# --------------------------------------------------------------------------
def _score_against(m: Dict, prof: Dict) -> Tuple[float, Dict]:
    """Weighted match of one track against one genre profile."""
    lo, hi = prof["bpm"]
    # Try the tracked tempo and both metrical re-readings; take the best.
    tempo_candidates = [m.get("bpm"), m.get("bpm_double"), m.get("bpm_half")]
    tempo_scores = [s for s in (_band_score(t, lo, hi, 0.6) for t in tempo_candidates)
                    if s is not None]
    tempo = max(tempo_scores) if tempo_scores else None

    parts = {
        "tempo": (tempo, 0.30),
        "energy": (_band_score(m.get("energy"), *prof["energy"], tolerance=0.8), 0.16),
        "danceability": (_band_score(m.get("danceability"), *prof["danceability"], tolerance=0.8), 0.14),
        "brightness": (_band_score(m.get("centroid_hz"), *prof["centroid_hz"], tolerance=0.7), 0.14),
        "loudness": (_target_score(m.get("lufs"), prof["lufs"], 6.0), 0.10),
        "swing": (_band_score(m.get("swing_pct"), *prof["swing_pct"], tolerance=1.0), 0.09),
        "width": (_band_score(m.get("width_pct"), *prof["width_pct"], tolerance=0.9), 0.07),
    }
    num = den = 0.0
    detail = {}
    for name, (score, weight) in parts.items():
        if score is None:
            continue
        num += score * weight
        den += weight
        detail[name] = score
    return (round(num / den, 1) if den else 0.0), detail


def genre_fit(report: Dict, top_n: int = 5) -> Dict:
    """Rank every known genre by how well this track already fits it."""
    m = _measure(report)
    ranked = []
    for key, prof in G.GENRES.items():
        score, detail = _score_against(m, prof)
        ranked.append({
            "genre": key,
            "label": prof["label"],
            "family": prof["family"],
            "match": score,
            "grade": _grade(score),
            "factors": detail,
        })
    ranked.sort(key=lambda r: -r["match"])
    best = ranked[0] if ranked else None
    return {
        "primary": best,
        "ranked": ranked[:top_n],
        "all_scores": {r["genre"]: r["match"] for r in ranked},
        "note": ("Match is measured against curated genre reference ranges for "
                 "tempo, energy, brightness, loudness, swing and width."),
    }


# --------------------------------------------------------------------------
# 2. genre transformation
# --------------------------------------------------------------------------
def _delta_step(label: str, current, target_lo, target_hi, unit: str,
                how: str, precision: int = 1) -> Optional[Dict]:
    """One concrete 'move X from A to B' instruction, or None if already in range."""
    if current is None:
        return None
    if target_lo <= current <= target_hi:
        return {
            "parameter": label, "current": round(current, precision),
            "target": f"{round(target_lo, precision)} to {round(target_hi, precision)}",
            "unit": unit, "status": "in_range", "change": 0.0, "action": None,
        }
    target = target_lo if current < target_lo else target_hi
    change = round(target - current, precision)
    return {
        "parameter": label,
        "current": round(current, precision),
        "target": f"{round(target_lo, precision)} to {round(target_hi, precision)}",
        "unit": unit,
        "status": "below" if current < target_lo else "above",
        "change": change,
        "action": how.format(
            change=abs(change), direction="up" if change > 0 else "down",
            target=round(target, precision)),
    }


def genre_transform(report: Dict, target_genre: str) -> Dict:
    """What to change to move this track into `target_genre`."""
    key = G.resolve(target_genre)
    if not key:
        return {
            "error": f"Unknown genre '{target_genre}'.",
            "available": G.all_keys(),
        }
    prof = G.GENRES[key]
    m = _measure(report)
    current_score, _ = _score_against(m, prof)

    # Pick the tempo reading closest to the target so we do not tell a 70 BPM
    # half-time track to "speed up by 70 BPM" when it is already correct.
    lo, hi = prof["bpm"]
    mid = (lo + hi) / 2
    cands = [c for c in (m.get("bpm"), m.get("bpm_double"), m.get("bpm_half")) if c]
    bpm_now = min(cands, key=lambda c: abs(c - mid)) if cands else None
    reading = ""
    if bpm_now and m.get("bpm") and abs(bpm_now - m["bpm"]) > 1:
        reading = (f" (reading the track at {bpm_now} BPM rather than the "
                   f"tracked {m['bpm']} BPM)")

    steps = [
        _delta_step("Tempo", bpm_now, lo, hi, "BPM",
                    "Move the tempo {direction} by {change} BPM to {target}."),
        _delta_step("Integrated loudness", m.get("lufs"),
                    prof["lufs"] - 1.0, prof["lufs"] + 1.0, "LUFS",
                    "Master {direction} by {change} LU to around {target} LUFS."),
        _delta_step("Loudness range", m.get("lra"), *prof["lra"], "LU",
                    "Bring the dynamic range {direction} by {change} LU; "
                    "target around {target} LU."),
        _delta_step("Brightness", m.get("centroid_hz"), *prof["centroid_hz"], "Hz",
                    "Shift the spectral balance {direction} by roughly {change} Hz "
                    "of centroid; adjust the shelf above 4 kHz."),
        _delta_step("Stereo width", m.get("width_pct"), *prof["width_pct"], "%",
                    "Take the width {direction} by {change} points to about {target}%."),
        _delta_step("Swing", m.get("swing_pct"), *prof["swing_pct"], "%",
                    "Move swing {direction} by {change} points to about {target}%."),
        _delta_step("Energy", m.get("energy"), *prof["energy"], "0..1",
                    "Push the energy {direction} by {change}; "
                    "adjust density and drum weight."),
        _delta_step("Intro length", m.get("intro_s"), *prof["intro_s"], "s",
                    "Take the intro {direction} by {change} seconds."),
    ]
    steps = [s for s in steps if s]
    needs = [s for s in steps if s["status"] != "in_range"]
    ok = [s for s in steps if s["status"] == "in_range"]

    # Quantization feel is categorical, so it is handled separately.
    feel = None
    want, have = prof["quantization"], (m.get("quantization") or "").lower()
    if want != "either" and have:
        tight_now = "tight" in have or "quantiz" in have or "machine" in have
        if want == "tight" and not tight_now:
            feel = ("Quantize the drums harder. This genre sits on the grid, and "
                    f"the track currently reads as '{m.get('quantization')}'.")
        elif want == "loose" and tight_now:
            feel = ("Loosen the drums. This genre depends on human drift, and the "
                    f"track currently reads as '{m.get('quantization')}'.")

    return {
        "target_genre": key,
        "target_label": prof["label"],
        "current_match": current_score,
        "current_grade": _grade(current_score),
        "tempo_reading_note": reading or None,
        "defining_traits": prof["signature"],
        "production_moves": prof["build"],
        "parameter_changes": needs,
        "already_correct": ok,
        "feel_change": feel,
        "effort": ("low" if len(needs) <= 2 else
                   "medium" if len(needs) <= 5 else "high"),
        "summary": (
            f"This track matches {prof['label']} at {current_score}%. "
            f"{len(needs)} parameter{'s' if len(needs) != 1 else ''} "
            f"{'need' if len(needs) != 1 else 'needs'} adjustment, "
            f"{len(ok)} already sit{'s' if len(ok) == 1 else ''} in range."),
    }


# --------------------------------------------------------------------------
# 3. hit potential (commercial readiness benchmark)
# --------------------------------------------------------------------------
def hit_potential(report: Dict, genre: Optional[str] = None) -> Dict:
    """Weighted benchmark against genre norms, with an action per weak factor."""
    m = _measure(report)

    key = G.resolve(genre) if genre else None
    if not key:
        fit = genre_fit(report, top_n=1)
        key = _get(fit, "primary", "genre") or "pop"
    prof = G.GENRES.get(key, G.GENRES["pop"])

    factors: List[Dict] = []

    def add(name: str, label: str, score: Optional[float], weight: float,
            good: str, action: str) -> None:
        if score is None:
            return
        factors.append({
            "factor": name, "label": label, "score": score, "weight": weight,
            "grade": _grade(score),
            "verdict": good if score >= 65 else action,
            "is_action": score < 65,
        })

    # -- hook timing: the single strongest streaming-era signal --------------
    peak = m.get("first_peak_s")
    intro = m.get("intro_s")
    hook_ref = peak if peak is not None else intro
    if hook_ref is not None:
        hook_score = _target_score(min(hook_ref, 60.0), 12.0, 40.0)
        add("hook_timing", "Time to the hook", hook_score, 0.18,
            f"The first peak lands at {hook_ref}s, which holds attention early.",
            f"The first peak lands at {hook_ref}s. Bring the hook forward; "
            f"under 20 seconds is where streaming retention holds up.")

    # -- loudness competitiveness -------------------------------------------
    add("loudness", "Loudness competitiveness",
        _target_score(m.get("lufs"), prof["lufs"], 7.0), 0.12,
        f"Integrated loudness sits near the {prof['label']} norm of {prof['lufs']} LUFS.",
        f"Integrated loudness is {m.get('lufs')} LUFS against a {prof['label']} "
        f"norm of {prof['lufs']}. Re-master toward that target.")

    # -- energy and danceability --------------------------------------------
    add("energy", "Energy",
        _band_score(m.get("energy"), *prof["energy"], tolerance=0.8), 0.12,
        "Energy sits inside the range for this genre.",
        f"Energy reads {m.get('energy')} against a {prof['label']} range of "
        f"{prof['energy'][0]} to {prof['energy'][1]}. Adjust arrangement density "
        f"and drum weight.")

    add("danceability", "Rhythmic pull",
        _band_score(m.get("danceability"), *prof["danceability"], tolerance=0.8), 0.10,
        "Rhythmic pull is in range for the genre.",
        f"Danceability reads {m.get('danceability')} against a range of "
        f"{prof['danceability'][0]} to {prof['danceability'][1]}. Tighten the "
        f"groove and strengthen the backbeat.")

    # -- arrangement variety -------------------------------------------------
    sc = m.get("section_count")
    if sc is not None:
        add("arrangement", "Arrangement variety",
            _band_score(float(sc), *prof["sections"], tolerance=0.9), 0.10,
            f"{sc} distinct sections gives the track shape.",
            f"{sc} sections against a {prof['label']} norm of {prof['sections'][0]} "
            f"to {prof['sections'][1]}. "
            + ("Add contrast: a bridge, a breakdown, or a stripped verse."
               if sc < prof["sections"][0]
               else "Consolidate; too many sections dilutes the hook."))

    # -- mix quality: hard technical gates -----------------------------------
    # Only scored when something was actually measured. Awarding 100 for an
    # unmeasured master would quietly inflate the composite.
    measured = [m.get("true_peak"), m.get("mono_compatible"),
                m.get("low_end_mono")]
    if any(v is not None for v in measured):
        mix, issues = 100.0, []
        tp = m.get("true_peak")
        if tp is not None and tp > -1.0:
            mix -= 30
            issues.append(f"true peak {tp} dBTP exceeds the -1.0 dBTP ceiling")
        if (m.get("clipped") or 0) > 0:
            mix -= 25
            issues.append(f"{m['clipped']} samples at full scale")
        if m.get("mono_compatible") is False:
            mix -= 25
            issues.append("the mix loses content in mono")
        if m.get("low_end_mono") is False:
            mix -= 20
            issues.append("the low end is not mono")
        add("mix_quality", "Mix and master integrity", max(0.0, mix), 0.15,
            "No technical blockers in the master.",
            "Technical problems to fix: " + "; ".join(issues) + ".")

    # -- tonal balance -------------------------------------------------------
    add("tonal_balance", "Tonal balance",
        _band_score(m.get("centroid_hz"), *prof["centroid_hz"], tolerance=0.7), 0.10,
        "Spectral balance sits in the genre's range.",
        f"Spectral centroid is {m.get('centroid_hz')} Hz against a genre range of "
        f"{prof['centroid_hz'][0]} to {prof['centroid_hz'][1]} Hz. "
        + ("Add high shelf and presence." if (m.get("centroid_hz") or 0) < prof["centroid_hz"][0]
           else "Tame the top end; it is brighter than the genre norm."))

    # -- dynamics ------------------------------------------------------------
    add("dynamics", "Dynamic range",
        _band_score(m.get("lra"), *prof["lra"], tolerance=0.8), 0.08,
        "Dynamic range is appropriate for the genre.",
        f"Loudness range is {m.get('lra')} LU against a genre range of "
        f"{prof['lra'][0]} to {prof['lra'][1]} LU. "
        + ("Over-compressed; back off the limiter." if (m.get("lra") or 0) < prof["lra"][0]
           else "Wide for the genre; tighten with bus compression."))

    # -- vocal presence ------------------------------------------------------
    inst = m.get("instrumentalness")
    if inst is not None and inst < 0.6:
        mid = m.get("midrange_share")
        vs = _band_score(mid, 0.18, 0.42, tolerance=1.0) if mid is not None else None
        add("vocal_space", "Vocal space", vs, 0.05,
            "The midrange leaves room for the lead vocal.",
            f"Midrange occupancy is {mid}. Carve 2 to 4 kHz in the instrumental "
            f"so the vocal sits above it.")

    if not factors:
        return {"available": False,
                "reason": "Not enough measurements to benchmark this track."}

    total_w = sum(f["weight"] for f in factors)
    score = round(sum(f["score"] * f["weight"] for f in factors) / total_w, 1)

    actions = [f for f in factors if f["is_action"]]
    actions.sort(key=lambda f: -(f["weight"] * (100 - f["score"])))
    strengths = [f for f in factors if not f["is_action"]]
    strengths.sort(key=lambda f: -f["score"])

    return {
        "available": True,
        "score": score,
        "grade": _grade(score),
        "benchmarked_against": prof["label"],
        "genre_key": key,
        "factors": factors,
        "priority_actions": [
            {"factor": f["label"], "score": f["score"], "do": f["verdict"]}
            for f in actions[:5]
        ],
        "strengths": [
            {"factor": f["label"], "score": f["score"], "note": f["verdict"]}
            for f in strengths[:4]
        ],
        "coverage": f"{len(factors)} factors measured",
        "disclaimer": (
            "A benchmark against genre reference ranges, not a prediction of "
            "commercial performance. It measures whether the record is "
            "competitive on the axes that can be measured from audio."),
    }


# --------------------------------------------------------------------------
# 4. release readiness
# --------------------------------------------------------------------------
_PLATFORM_TARGETS = {
    "Spotify": -14.0, "Apple Music": -16.0, "YouTube": -14.0,
    "Amazon Music": -14.0, "Tidal": -14.0, "Club / DJ": -8.0,
}


def release_readiness(report: Dict) -> Dict:
    """Concrete blockers and warnings between this file and a clean release."""
    m = _measure(report)
    blockers: List[Dict] = []
    warnings: List[Dict] = []

    def blocker(check, detail, fix):
        blockers.append({"check": check, "detail": detail, "fix": fix})

    def warn(check, detail, fix):
        warnings.append({"check": check, "detail": detail, "fix": fix})

    tp = m.get("true_peak")
    if tp is not None:
        if tp > 0.0:
            blocker("true_peak", f"True peak {tp} dBTP is over full scale.",
                    "Lower the limiter ceiling to -1.0 dBTP and re-render.")
        elif tp > -1.0:
            warn("true_peak", f"True peak {tp} dBTP is above the -1.0 dBTP ceiling.",
                 "Set the limiter ceiling to -1.0 dBTP so lossy encoding does not clip.")

    if (m.get("clipped") or 0) > 0:
        blocker("clipping", f"{m['clipped']} samples sit at full scale.",
                "Reduce gain before the limiter and re-render.")

    if m.get("mono_compatible") is False:
        blocker("mono", "The mix loses content when summed to mono.",
                "Check for polarity inversion and excessive stereo widening.")

    if m.get("low_end_mono") is False:
        warn("low_end", "Content below roughly 120 Hz is not mono.",
             "Sum the low end to mono so it stays stable on club systems and vinyl.")

    lufs = m.get("lufs")
    if lufs is not None:
        if lufs > -6.0:
            warn("loudness", f"Integrated loudness {lufs} LUFS is extremely hot.",
                 "Every platform will turn this down and the limiting will be audible.")
        elif lufs < -20.0:
            warn("loudness", f"Integrated loudness {lufs} LUFS is very quiet.",
                 "It will sound weak next to other tracks in a playlist.")

    lra = m.get("lra")
    if lra is not None and lra < 2.0:
        warn("dynamics", f"Loudness range {lra} LU is very compressed.",
             "Back off bus and master compression to restore movement.")

    dur = m.get("duration")
    if dur is not None:
        if dur < 30:
            blocker("duration", f"Duration {dur}s is under the 30 second minimum "
                                f"most platforms count as a stream.",
                    "Extend the track past 30 seconds.")
        elif dur < 60:
            warn("duration", f"Duration {dur}s is short for a commercial release.",
                 "Most catalogue tracks run past 90 seconds.")

    qc = _get(report, "industry", "quality_control", default={}) or {}
    for c in (qc.get("checks") or []):
        if c.get("status") == "failed":
            blocker(c.get("check", "quality"), c.get("detail", ""),
                    c.get("note") or "See the quality control section.")

    # Per-platform loudness preview.
    platforms = []
    if lufs is not None:
        for name, target in _PLATFORM_TARGETS.items():
            delta = round(target - lufs, 1)
            platforms.append({
                "platform": name, "target_lufs": target,
                "your_lufs": lufs, "gain_applied_db": delta,
                "note": (f"turned down {abs(delta)} dB" if delta < -0.5 else
                         f"turned up {delta} dB" if delta > 0.5 else
                         "played close to as delivered"),
            })

    if blockers:
        status, summary = "blocked", (
            f"{len(blockers)} issue{'s' if len(blockers) != 1 else ''} must be "
            f"fixed before release.")
    elif warnings:
        status, summary = "review", (
            f"No hard blockers. {len(warnings)} item"
            f"{'s' if len(warnings) != 1 else ''} worth reviewing.")
    else:
        status, summary = "ready", "No technical blockers found. Ready to deliver."

    return {
        "status": status,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "platform_loudness": platforms,
        "checks_run": 8 + len(qc.get("checks") or []),
    }


# --------------------------------------------------------------------------
# 5. mix report
# --------------------------------------------------------------------------
def mix_report(report: Dict) -> Dict:
    """Hook timing, tonal balance against a reference tilt, and stereo health."""
    m = _measure(report)

    # -- opening ------------------------------------------------------------
    opening = None
    peak, intro = m.get("first_peak_s"), m.get("intro_s")
    if peak is not None or intro is not None:
        ref = peak if peak is not None else intro
        opening = {
            "intro_length_s": intro,
            "first_peak_s": peak,
            "verdict": ("fast" if ref <= 15 else
                        "acceptable" if ref <= 30 else "slow"),
            "note": (
                f"The track reaches its first peak at {ref} seconds. "
                + ("That is well inside the window where listeners decide to stay."
                   if ref <= 15 else
                   "That is late; most skips happen inside the first 30 seconds."
                   if ref > 30 else
                   "That is workable, though earlier is better for playlists.")),
        }

    # -- tonal balance by band ----------------------------------------------
    bands = []
    for b in (m.get("bands") or []):
        share = b.get("share")
        if share is None:
            continue
        bands.append({
            "band": b.get("name"), "share": _f(share, 4),
            "db": _f(b.get("db"), 1),
        })

    # -- stereo -------------------------------------------------------------
    stereo = {
        "width_pct": m.get("width_pct"),
        "correlation": m.get("correlation"),
        "mono_compatible": m.get("mono_compatible"),
        "low_end_mono": m.get("low_end_mono"),
        "verdict": (
            "narrow" if (m.get("width_pct") or 50) < 35 else
            "wide" if (m.get("width_pct") or 50) > 85 else "balanced"),
    }

    # -- groove -------------------------------------------------------------
    groove = {
        "swing_pct": m.get("swing_pct"),
        "quantization": m.get("quantization"),
        "timing_deviation_ms": m.get("timing_deviation_ms"),
        "feel": ("programmed" if (m.get("timing_deviation_ms") or 99) < 8 else
                 "performed" if (m.get("timing_deviation_ms") or 0) > 18 else
                 "lightly humanised"),
    }

    # -- harmonic / DJ ------------------------------------------------------
    dj = None
    if m.get("camelot"):
        dj = {
            "key": m.get("key"), "camelot": m.get("camelot"),
            "bpm": m.get("bpm"),
            "compatible_camelot": _camelot_neighbours(m["camelot"]),
            "note": ("Tracks in these Camelot codes will mix harmonically. "
                     "Keep tempo within roughly 6 percent for a clean blend."),
        }

    return {
        "opening": opening,
        "tonal_balance": bands,
        "stereo": stereo,
        "groove": groove,
        "harmonic_mixing": dj,
    }


def _camelot_neighbours(code: str) -> List[str]:
    """Adjacent Camelot codes: same number opposite letter, and ±1 same letter."""
    try:
        num = int(code[:-1])
        letter = code[-1].upper()
    except (ValueError, IndexError):
        return []
    other = "B" if letter == "A" else "A"
    up = (num % 12) + 1
    down = ((num - 2) % 12) + 1
    return [f"{num}{other}", f"{up}{letter}", f"{down}{letter}"]


# --------------------------------------------------------------------------
# 6. sync / licensing readiness
# --------------------------------------------------------------------------
def sync_readiness(report: Dict) -> Dict:
    """How usable this track is for sync licensing."""
    m = _measure(report)
    inst = m.get("instrumentalness")
    notes, score = [], 100.0

    if inst is not None:
        if inst >= 0.7:
            notes.append("Largely instrumental, which suits underscore use.")
        elif inst >= 0.4:
            notes.append("Partly instrumental; an instrumental stem would widen its use.")
        else:
            score -= 20
            notes.append("Vocal-led. Supply an instrumental version for sync.")

    dur = m.get("duration")
    if dur is not None and dur < 60:
        score -= 15
        notes.append("Short duration limits placement options.")

    if m.get("lra") is not None and m["lra"] < 3.0:
        score -= 15
        notes.append("Heavily compressed; sync editors prefer dynamic range to duck under dialogue.")

    if m.get("mono_compatible") is False:
        score -= 20
        notes.append("Mono incompatibility is a blocker for broadcast.")

    # Usable instrumental windows from the arrangement.
    windows = []
    for s in (m.get("sections") or []):
        lbl = str(s.get("label", "")).lower()
        start, end = s.get("start"), s.get("end")
        if start is None or end is None:
            continue
        length = end - start
        if length >= 8 and any(w in lbl for w in ("intro", "break", "outro", "bridge")):
            windows.append({"label": s.get("label"), "start": _f(start, 1),
                            "end": _f(end, 1), "length_s": _f(length, 1)})

    return {
        "score": round(max(0.0, score), 1),
        "grade": _grade(max(0.0, score)),
        "notes": notes,
        "instrumental_windows": windows,
        "metadata_ready": {
            "tempo": m.get("bpm") is not None,
            "key": m.get("key") is not None,
            "duration": dur is not None,
        },
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def build(report: Dict, target_genre: Optional[str] = None) -> Dict:
    """Assemble the artist section. Never fatal to the parent report."""
    out: Dict = {}
    try:
        out["genre_fit"] = genre_fit(report)
    except Exception:
        log.exception("genre_fit failed")
    try:
        out["hit_potential"] = hit_potential(report, genre=target_genre)
    except Exception:
        log.exception("hit_potential failed")
    try:
        out["release_readiness"] = release_readiness(report)
    except Exception:
        log.exception("release_readiness failed")
    try:
        out["mix_report"] = mix_report(report)
    except Exception:
        log.exception("mix_report failed")
    try:
        out["sync_readiness"] = sync_readiness(report)
    except Exception:
        log.exception("sync_readiness failed")

    if target_genre:
        try:
            out["genre_transform"] = genre_transform(report, target_genre)
        except Exception:
            log.exception("genre_transform failed")

    out["available_genres"] = G.labels()
    return out
