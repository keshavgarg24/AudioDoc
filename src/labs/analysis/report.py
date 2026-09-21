"""Deep analysis of a track from the model's own internals.

The verdict is one number out of Stage-2. Everything else in this module is
signal the pipeline already computes and used to discard:

  * Stage-1 ships a per-segment 2-class head alongside the embedding it returns.
    That gives an independent opinion for every 10 s window, so the verdict can
    be shown as a timeline rather than a single label.
  * Stage-2's structure stream is built from a segment x segment similarity
    matrix. That matrix is the track's self-similarity - repeated choruses,
    section boundaries - and is worth surfacing directly.
  * The beat tracker produces downbeats, whose spacing gives tempo and, more
    interestingly, tempo *stability*.

Nothing here re-runs the model; it all derives from tensors already in hand.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn.functional as F

log = logging.getLogger(__name__)

# Segment embeddings leave Stage-1 LayerNormed, so exp(-mean squared distance)
# lands in a narrow high band - measured mean 0.88-0.97, min 0.41-0.72 across
# test tracks. Absolute cutoffs are therefore useless (0.80 matches ~90% of
# pairs). Section detection instead uses each track's own novelty distribution.
NOVELTY_PEAK_SIGMA = 2.0
# Floor so near-silent, near-identical tracks don't manufacture boundaries.
NOVELTY_MIN_ABSOLUTE = 0.02


def _f(x, nd: int = 4) -> float:
    """JSON-safe float (NaN/Inf are not valid JSON)."""
    v = float(x)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(v, nd)


def _i(x):
    """Whole number, for tempo. See `labs.tools.base._i` for the reasoning."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return int(round(v))


def _pct(x) -> float:
    return _f(float(x) * 100.0, 2)


# --------------------------------------------------------------------------
# rhythm
# --------------------------------------------------------------------------
def analyse_rhythm(downbeats_raw, downbeats_clean, segment_seconds: float,
                   musical_rhythm: Optional[Dict] = None) -> Dict:
    """Tempo and how mechanically regular it is.

    `segment_seconds` is the modal 4-bar length the segmenter settled on, so
    bar = segment/4 and beat = bar/4 under the 4/4 assumption the repo makes.

    That derivation exists to describe the detection grid, not to be a tempo
    estimate, and it used to be published as `bpm` with no metrical-level
    correction. Alongside `musical.rhythm.bpm`, which is a dedicated tracker
    with that correction applied, a single response could therefore carry two
    fields called `bpm` that differed by a factor of two - 71 against 140 on
    the same file - and the human-readable summary quoted the wrong one.

    So `musical_rhythm` wins when musical analysis ran: it is the better
    measurement and it is what every other consumer (character, industry,
    Tempo Lab) already reads. The grid figure is kept under `grid_bpm`
    because the detection reasoning is derived from it and dropping it would
    make that reasoning unauditable.
    """
    from .musical import notated_tempo

    raw = np.asarray(downbeats_raw, dtype=float).ravel()
    clean = np.asarray(downbeats_clean, dtype=float).ravel()

    bar_s = segment_seconds / 4.0 if segment_seconds else 0.0
    grid_bpm = (60.0 * 4.0 / bar_s) if bar_s > 0 else 0.0

    # `is not None`, not truthiness: a tracker that ran and found no pulse
    # reports 0, and that 0 must be published as-is. Falling back to the grid
    # in that case would put two different tempos in one response again.
    tracked = (musical_rhythm or {}).get("bpm")
    if tracked is not None:
        bpm, bpm_source = float(tracked), "beat tracker"
        level = (musical_rhythm or {}).get("metrical_level", "as tracked")
    else:
        # No musical analysis on this request. Apply the same metrical rule
        # rather than publishing a raw half-time figure as the tempo, and say
        # so: a doubled figure with no label is indistinguishable from a
        # measured one.
        bpm, level, _reason = notated_tempo(grid_bpm)
        bpm_source = "detection grid"

    intervals = np.diff(raw) if raw.size > 1 else np.array([])
    if intervals.size:
        mean_i = float(intervals.mean())
        std_i = float(intervals.std())
        # Coefficient of variation: 0 == metronomic.
        cov = (std_i / mean_i) if mean_i > 0 else 0.0
        drift = float(np.abs(np.diff(intervals)).mean()) if intervals.size > 1 else 0.0
    else:
        mean_i = std_i = cov = drift = 0.0

    # How many detected downbeats survived the modal-interval filter. A low
    # retention means the tracker kept changing its mind about the pulse.
    retention = (clean.size / raw.size) if raw.size else 0.0

    return {
        "bpm": _i(bpm),
        "bpm_source": bpm_source,
        "metrical_level": level,
        "grid_bpm": _i(grid_bpm),
        "bar_seconds": _f(bar_s, 3),
        "segment_seconds": _f(segment_seconds, 2),
        "downbeats_detected": int(raw.size),
        "downbeats_retained": int(clean.size),
        "downbeat_retention": _f(retention),
        "mean_downbeat_interval": _f(mean_i, 3),
        "downbeat_interval_std": _f(std_i, 4),
        "tempo_variation": _f(cov),
        "tempo_drift": _f(drift, 4),
        "tempo_stability": _f(max(0.0, 1.0 - cov * 10.0)),
    }


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------
def analyse_structure(embeddings: torch.Tensor) -> Dict:
    """Self-similarity of the real segments.

    Uses the same exp(-mean squared distance) kernel as
    FusionSegmentTransformer's structure stream, so the matrix shown is the one
    the classifier actually consumed.
    """
    x = embeddings.detach().float().cpu()
    n = x.shape[0]
    if n < 2:
        return {
            "matrix": [[1.0]] * n, "size": n, "homogeneity": 0.0,
            "structural_contrast": 0.0, "mean_similarity": 0.0,
            "max_similarity": 0.0, "min_similarity": 0.0,
            "novelty_curve": [], "section_count": max(n, 1),
            "section_boundaries": [], "repeat_pairs": [], "structural_entropy": 0.0,
        }

    d = torch.mean((x.unsqueeze(1) - x.unsqueeze(0)) ** 2, dim=-1)
    sim = torch.exp(-d)

    off_vals = sim[~torch.eye(n, dtype=torch.bool)]

    # Novelty: how unlike the previous window each one is. Peaks are boundaries.
    consecutive = torch.stack([sim[i, i + 1] for i in range(n - 1)])
    novelty = (1.0 - consecutive)

    # Threshold against this track's own novelty distribution, since the
    # absolute scale differs per track.
    nmean, nstd = float(novelty.mean()), float(novelty.std())
    cutoff = max(nmean + NOVELTY_PEAK_SIGMA * nstd, NOVELTY_MIN_ABSOLUTE)
    boundaries = [i + 1 for i, v in enumerate(novelty.tolist()) if v > cutoff]

    # Strongest non-adjacent recurrences - choruses, loops. Reported as a
    # ranked list rather than gated on an absolute cutoff.
    repeats: List[Dict] = []
    for i in range(n):
        for j in range(i + 2, n):
            repeats.append({"from": i, "to": j, "similarity": _f(float(sim[i, j]))})
    repeats.sort(key=lambda r: -r["similarity"])

    hist = torch.histc(off_vals, bins=20, min=0.0, max=1.0)
    p = hist / hist.sum().clamp(min=1e-9)
    p = p[p > 0]
    entropy = float(-(p * torch.log(p)).sum() / math.log(20))

    return {
        "matrix": [[_f(v, 3) for v in row] for row in sim.tolist()],
        "size": n,
        # How much every window resembles every other one. High == loop-like.
        "homogeneity": _f(float(off_vals.mean())),
        # Spread of that similarity. High == clearly differentiated sections.
        "structural_contrast": _f(float(off_vals.std())),
        "mean_similarity": _f(float(off_vals.mean())),
        "max_similarity": _f(float(off_vals.max())),
        "min_similarity": _f(float(off_vals.min())),
        "novelty_curve": [_f(v) for v in novelty.tolist()],
        "novelty_cutoff": _f(cutoff),
        "section_count": len(boundaries) + 1,
        "section_boundaries": boundaries,
        "repeat_pairs": repeats[:24],
        "structural_entropy": _f(entropy),
    }


# --------------------------------------------------------------------------
# per-segment timeline
# --------------------------------------------------------------------------
def analyse_segments(
    stage1_logits: torch.Tensor,
    embeddings: torch.Tensor,
    starts: List[float],
    segment_seconds: float,
    fake_index: int = 1,
) -> Dict:
    """Stage-1's own per-window opinion, as a timeline."""
    logits = stage1_logits.detach().float().cpu()
    probs = F.softmax(logits, dim=-1)[:, fake_index]
    emb = embeddings.detach().float().cpu()

    centroid = emb.mean(dim=0, keepdim=True)
    # Distance from the track's own centre: flags windows that don't belong.
    deviation = torch.norm(emb - centroid, dim=-1)
    dev_norm = deviation / deviation.max().clamp(min=1e-9)

    n = probs.shape[0]
    items = []
    for i in range(n):
        start = float(starts[i]) if i < len(starts) else i * segment_seconds
        items.append({
            "index": i,
            "start": _f(start, 2),
            "end": _f(start + segment_seconds, 2),
            "fake_probability": _f(float(probs[i])),
            "leaning": "Fake" if float(probs[i]) > 0.5 else "Real",
            "deviation": _f(float(dev_norm[i])),
        })

    p = probs.numpy()
    fake_n = int((p > 0.5).sum())
    # Longest stretch of consecutive same-leaning windows.
    runs, cur, prev = [], 0, None
    for v in (p > 0.5):
        if prev is None or v == prev:
            cur += 1
        else:
            runs.append((prev, cur))
            cur = 1
        prev = v
    runs.append((prev, cur))
    longest = max(runs, key=lambda r: r[1])

    return {
        "segments": items,
        "count": n,
        "fake_segments": fake_n,
        "real_segments": n - fake_n,
        "fake_ratio": _f(fake_n / n if n else 0.0),
        "mean_fake_probability": _f(float(p.mean()) if n else 0.0),
        "std_fake_probability": _f(float(p.std()) if n else 0.0),
        "min_fake_probability": _f(float(p.min()) if n else 0.0),
        "max_fake_probability": _f(float(p.max()) if n else 0.0),
        "longest_run_leaning": "Fake" if bool(longest[0]) else "Real",
        "longest_run_length": int(longest[1]),
        "most_anomalous_segment": int(np.argmax(dev_norm.numpy())) if n else -1,
    }


# --------------------------------------------------------------------------
# narrative
# --------------------------------------------------------------------------
def build_findings(verdict: Dict, segs: Dict, struct: Dict, rhythm: Dict) -> List[Dict]:
    """Plain-language evidence, each tied to a number shown elsewhere.

    Every entry states what was measured and what it implies - no claim here is
    unsupported by a field in the payload.
    """
    out: List[Dict] = []
    is_fake = verdict["prediction"] == "Fake"

    # 1. headline
    out.append({
        "type": "verdict",
        "weight": "primary",
        "title": f"Classified as {verdict['prediction'].lower()} "
                 f"at {verdict['confidence']:.1f}% confidence",
        "detail": (
            f"The detection score came out at "
            f"{verdict['raw_logit']:+.3f} across {segs['count']} beat-aligned "
            f"windows. Values beyond roughly ±7 sit at the limit of the scale, "
            f"so this is a "
            f"{'decisive' if abs(verdict['raw_logit']) > 6 else 'moderate'} "
            f"result."
        ),
    })

    # 2. do the two stages agree?
    agree = (segs["fake_ratio"] > 0.5) == is_fake
    out.append({
        "type": "agreement",
        "weight": "primary" if not agree else "secondary",
        "title": ("Window level and overall analysis agree" if agree
                  else "Window level and overall analysis disagree"),
        "detail": (
            f"Scored window by window: "
            f"{segs['fake_segments']} of {segs['count']} lean AI-generated "
            f"({segs['fake_ratio'] * 100:.0f}%), mean probability "
            f"{segs['mean_fake_probability']:.3f}. The overall pass, which also "
            f"weighs how the windows relate to each other, concluded "
            f"{verdict['prediction'].lower()}. "
            + ("The independent per-window evidence supports the verdict."
               if agree else
               "Per-window evidence points the other way, so the verdict rests on "
               "structural cues rather than local audio texture - treat it as "
               "lower confidence than the headline number suggests.")
        ),
    })

    # 3. consistency across the track
    spread = segs["std_fake_probability"]
    if spread < 0.10:
        c_title, c_note = "Highly uniform across the track", (
            "Little variation between windows. Consistent production throughout, "
            "which is typical of a single-source render.")
    elif spread < 0.25:
        c_title, c_note = "Moderately consistent across the track", (
            "Some variation between sections, within the range expected of a "
            "normally arranged track.")
    else:
        c_title, c_note = "Highly variable across the track", (
            "Windows disagree substantially. Worth checking for a hybrid track, "
            "a remix, or edited/spliced material.")
    out.append({
        "type": "consistency", "weight": "secondary",
        "title": c_title,
        "detail": (f"Per-window AI probability ranges "
                   f"{segs['min_fake_probability']:.3f} to "
                   f"{segs['max_fake_probability']:.3f} (sd "
                   f"{spread:.3f}). {c_note} The longest unbroken stretch is "
                   f"{segs['longest_run_length']} windows leaning "
                   f"{segs['longest_run_leaning'].lower()}."),
    })

    # 4. structure. Calibrated to the observed band (homogeneity 0.85-0.97,
    #    contrast 0.03-0.12) - see the constants note at the top of this module.
    homo, contrast = struct["homogeneity"], struct["structural_contrast"]
    if homo > 0.95 and contrast < 0.06:
        s_title, s_note = "Highly uniform, loop-like structure", (
            "Every window closely resembles every other one, with little "
            "sectional contrast. Characteristic of loop-based production - "
            "common in generated material, but equally so in electronic and "
            "ambient genres, so this is supporting evidence rather than proof.")
    elif contrast > 0.09:
        s_title, s_note = "Clearly differentiated sections", (
            "Self-similarity varies widely across the track, indicating "
            "genuinely distinct sections rather than a repeated loop.")
    else:
        s_title, s_note = "Conventional sectional structure", (
            "Repetition and contrast are both in the range expected of ordinary "
            "verse/chorus writing.")
    out.append({
        "type": "structure", "weight": "secondary",
        "title": s_title,
        "detail": (f"Mean self-similarity {homo:.3f} with contrast (sd) "
                   f"{contrast:.3f} across {struct['size']} windows, spanning "
                   f"{struct['min_similarity']:.2f}-{struct['max_similarity']:.2f}. "
                   f"{struct['section_count']} section"
                   f"{'s' if struct['section_count'] != 1 else ''} detected from "
                   f"novelty peaks. {s_note}"),
    })

    # 5. rhythm
    stab = rhythm["tempo_stability"]
    if stab > 0.9:
        r_title, r_note = "Metronomic timing", (
            "Essentially no tempo fluctuation - a fixed grid. Expected from "
            "programmed or generated music, and from any click-track recording.")
    elif stab > 0.6:
        r_title, r_note = "Steady timing with slight movement", (
            "Minor fluctuation, consistent with quantised production that retains "
            "some human feel.")
    else:
        r_title, r_note = "Loose, human timing", (
            "Noticeable push and pull in the pulse, characteristic of live "
            "performance.")
    out.append({
        "type": "rhythm", "weight": "secondary",
        "title": r_title,
        # Tempo and timing come from different places now - the tracker and
        # the detection grid respectively - so the sentence no longer implies
        # the BPM was read off the downbeat count.
        "detail": (f"Estimated {rhythm['bpm']} BPM. Timing measured over "
                   f"{rhythm['downbeats_detected']} detected downbeats "
                   f"({rhythm['downbeats_retained']} kept after filtering to the "
                   f"modal bar length). Inter-downbeat variation "
                   f"{rhythm['tempo_variation']:.4f}. {r_note}"),
    })

    # 6. outlier
    if segs["most_anomalous_segment"] >= 0 and segs["count"] > 3:
        idx = segs["most_anomalous_segment"]
        seg = segs["segments"][idx]
        out.append({
            "type": "anomaly", "weight": "tertiary",
            "title": f"Most distinctive passage at {seg['start']:.0f}s",
            "detail": (f"Window {idx} ({seg['start']:.0f}-{seg['end']:.0f}s) sits "
                       f"furthest from the track's average character. Its own AI "
                       f"probability is {seg['fake_probability']:.3f}. Often a "
                       f"bridge, breakdown, intro or outro."),
        })

    return out


def _reliability(verdict: Dict, segs: Dict) -> Dict:
    """How much to trust the headline, separate from the headline itself."""
    score, notes = 100.0, []
    if segs["count"] < 8:
        score -= 30
        notes.append(f"Only {segs['count']} analysable windows - short input.")
    elif segs["count"] < 20:
        score -= 10
        notes.append(f"{segs['count']} windows; a longer track would be firmer.")
    if (segs["fake_ratio"] > 0.5) != (verdict["prediction"] == "Fake"):
        score -= 35
        notes.append("Window level and overall analysis reach opposite conclusions.")
    if segs["std_fake_probability"] > 0.25:
        score -= 15
        notes.append("Per-window scores vary widely across the track.")
    if abs(verdict["raw_logit"]) < 2.0:
        score -= 20
        notes.append("Raw logit sits near the decision boundary.")
    score = max(0.0, min(100.0, score))
    label = ("High" if score >= 80 else "Moderate" if score >= 55
             else "Low" if score >= 30 else "Very low")
    if not notes:
        notes.append("No reliability concerns detected.")
    return {"score": _f(score, 1), "label": label, "notes": notes}


def build_report(
    *,
    mode: str = "ai",
    verdict: Optional[Dict] = None,
    stage1_logits: Optional[torch.Tensor] = None,
    embeddings: Optional[torch.Tensor] = None,
    segment_starts: List[float],
    segment_seconds: float,
    downbeats_raw,
    downbeats_clean,
    filename: str,
    duration: float,
    device: str,
    elapsed: float,
    fake_index: int = 1,
    features: Optional[Dict] = None,
    musical_data: Optional[Dict] = None,
    production_data: Optional[Dict] = None,
    source_path: Optional[str] = None,
    target_genre: Optional[str] = None,
) -> Dict:
    from ..analysis.character import build as build_character
    from ..analysis.interpret import signal_findings, signal_summary

    features = features or {}
    musical_data = musical_data or {}
    production_data = production_data or {}

    rhythm = analyse_rhythm(downbeats_raw, downbeats_clean, segment_seconds,
                            musical_rhythm=musical_data.get("rhythm"))

    report: Dict = {
        "mode": mode,
        "rhythm": rhythm,
        "source": {
            "filename": filename,
            "duration_seconds": _f(duration, 2),
        },
        "runtime": {
            "device": device,
            "elapsed_seconds": _f(elapsed, 2),
            "window_seconds": _f(segment_seconds, 2),
        },
    }

    # ---- detection side ---------------------------------------------------
    if verdict is not None and stage1_logits is not None and embeddings is not None:
        segs = analyse_segments(stage1_logits, embeddings, segment_starts,
                                segment_seconds, fake_index=fake_index)
        struct = analyse_structure(embeddings)
        findings = build_findings(verdict, segs, struct, rhythm)

        report.update({
            **verdict,
            "summary": findings[0]["detail"],
            "findings": findings,
            "reliability": _reliability(verdict, segs),
            "timeline": segs,
            "structure": struct,
        })
        report["runtime"]["windows_analysed"] = segs["count"]
        report["source"]["analysed_seconds"] = _f(
            min(duration, segs["count"] * segment_seconds), 2)
        report["source"]["coverage"] = _f(
            min(1.0, (segs["count"] * segment_seconds) / duration) if duration else 0.0)

    # ---- audio side -------------------------------------------------------
    if features or musical_data or production_data:
        report["industry"] = _industry(
            source_path, features, musical_data, production_data, duration)
        # Signal evidence is independent of the neural verdict and is kept in
        # its own group so the two can be weighed separately.
        sig = signal_findings(features)
        report["signal_findings"] = sig
        report["signal_summary"] = signal_summary(sig) if sig else None
        report["features"] = features
        report["musical"] = musical_data
        report["production"] = production_data
        report["character"] = build_character(
            features, musical_data, production_data.get("loudness", {}))
        if not report.get("summary"):
            report["summary"] = report["character"]["summary"]

        # Artist-facing layer. Reads the sections above, runs no new DSP, and
        # is never allowed to break the report.
        try:
            from ..artist.insights import build as build_artist
            report["artist"] = build_artist(report, target_genre=target_genre)
        except Exception:
            log.exception("Artist analysis failed")

    return report


def _industry(source_path: Optional[str], features: Dict, musical_data: Dict,
              production_data: Dict, duration: float) -> Dict:
    """Catalogue features and marketplace QC. Never fatal to the report."""
    if not source_path:
        return {}
    try:
        from ..analysis.industry import build as build_industry
        return build_industry(source_path, features, musical_data,
                              production_data, duration)
    except Exception:
        log.exception("Industry analysis failed")
        return {}
