"""Turn signal measurements into production-desk language.

Each entry names the measured value, says what it means in the terms an
engineer or A&R listener would use, and states how much weight it carries as
evidence of synthesis. Thresholds are commented with their rationale; none of
them is a verdict on its own.
"""
from __future__ import annotations

from typing import Dict, List


def _hz(v: float) -> str:
    return f"{v / 1000:.1f} kHz" if v >= 1000 else f"{v:.0f} Hz"


def spectral_findings(sp: Dict) -> List[Dict]:
    out: List[Dict] = []

    # --- high-frequency ceiling -------------------------------------------
    ceil, cliff = sp["ceiling_hz"], sp["rolloff_cliff_db"]
    ratio = sp["ceiling_ratio"]
    if ceil < 16500 and cliff > 18:
        w, title = "primary", "Hard high-frequency ceiling"
        note = (
            f"Energy stops abruptly at {_hz(ceil)} with a {cliff:.0f} dB cliff "
            f"immediately above it. A brick wall this steep is characteristic of "
            f"a codec bottleneck or a neural vocoder's output band limit, most "
            f"generative systems synthesise up to a fixed ceiling. Acoustic "
            f"recordings taper gradually instead. Note that a lossy MP3/AAC "
            f"source produces the same signature, so this is only meaningful "
            f"alongside the other evidence.")
        flag = "synthetic-leaning"
    elif ceil < 16500:
        w, title = "secondary", "Limited high-frequency extension"
        note = (
            f"Content rolls off by {_hz(ceil)} ({ratio * 100:.0f}% of the "
            f"available bandwidth) but without a sharp cliff. Consistent with "
            f"lossy encoding somewhere in the chain, or simply a dark mix.")
        flag = "neutral"
    else:
        w, title = "secondary", "Full-bandwidth high end"
        note = (
            f"Energy extends to {_hz(ceil)}, close to the {_hz(sp['nyquist_hz'])} "
            f"Nyquist limit, with no artificial ceiling. Typical of an "
            f"uncompressed or high-bitrate source.")
        flag = "human-leaning"
    out.append({"type": "bandwidth", "weight": w, "title": title,
                "detail": note, "flag": flag})

    # --- brightness --------------------------------------------------------
    c = sp["centroid_hz"]
    tone = ("bright and forward" if c > 3000 else
            "balanced" if c > 1800 else
            "dark and mid-focused")
    out.append({
        "type": "brightness", "weight": "tertiary",
        "title": f"Tonal balance reads {tone}",
        "detail": (
            f"Spectral centroid averages {_hz(c)} (±{_hz(sp['centroid_std'])}), "
            f"with 85% of energy below {_hz(sp['rolloff85_hz'])} and a bandwidth "
            f"of {_hz(sp['bandwidth_hz'])}. Centroid is the spectral centre of "
            f"mass, the objective correlate of perceived brightness."),
        "flag": "neutral",
    })

    # --- flatness ----------------------------------------------------------
    # Thresholds are on the dB form. Music typically sits -50..-15 dB; the raw
    # ratio is ~1e-4 for both test tracks, so ratio thresholds never fire.
    fl_db = sp.get("flatness_db", -60.0)
    if fl_db > -15:
        t, n, f = ("Noise-like spectral character",
                   "High spectral flatness indicates broadband, noise-like "
                   "content rather than clear harmonic partials. In generated "
                   "audio this often shows up as vocoder haze; it is equally "
                   "normal in distorted, percussive or heavily textured genres.",
                   "synthetic-leaning")
    elif fl_db > -32:
        t, n, f = ("Mixed tonal and noise content",
                   "A normal balance of harmonic partials against broadband "
                   "elements such as drums, air and room tone.", "neutral")
    else:
        t, n, f = ("Strongly tonal spectrum",
                   "Energy concentrates in discrete harmonic partials with "
                   "little broadband noise, clean, pitched material.", "neutral")
    out.append({"type": "flatness", "weight": "tertiary", "title": t,
                "detail": f"Spectral flatness {fl_db:.1f} dB. {n}", "flag": f})

    return out


def dynamics_findings(dy: Dict) -> List[Dict]:
    out: List[Dict] = []
    crest, dr = dy["crest_factor_db"], dy["dynamic_range_db"]

    # Crest factor against DR-meter norms: <8 dB is aggressively limited,
    # 8-15 dB covers essentially every modern commercial master, >15 dB is
    # genuinely audiophile-dynamic. Lossy decode adds intersample overshoot,
    # which nudges these upward by a decibel or so.
    if crest < 8:
        t, n, f = ("Heavily limited master",
                   "A crest factor this low means peaks sit barely above the "
                   "average level, the signature of aggressive brickwall "
                   "limiting. Common in commercial masters chasing loudness, "
                   "and also in generated audio rendered at a fixed level.",
                   "neutral")
    elif crest < 15:
        t, n, f = ("Conventional commercial master",
                   "Peak-to-average ratio in the normal range for a modern "
                   "release, controlled, but with transients intact.", "neutral")
    else:
        t, n, f = ("Wide, dynamic master",
                   "Substantial headroom between peaks and average level, "
                   "typical of acoustic, classical or audiophile mastering.",
                   "human-leaning")
    out.append({
        "type": "dynamics", "weight": "secondary", "title": t,
        "detail": (f"Crest factor {crest:.1f} dB, macro-dynamic range "
                   f"{dr:.1f} dB (95th vs 10th percentile of short-term RMS), "
                   f"peak {dy['peak_db']:.1f} dBFS. {n}"),
        "flag": f,
    })

    # Macro-dynamic movement across the arrangement.
    lstd = dy["loudness_std_db"]
    if lstd < 2.5:
        t2, n2, f2 = ("Unusually flat level across the track",
                      "Short-term loudness barely moves from start to finish. "
                      "Real arrangements normally breathe between verse, chorus "
                      "and breakdown. Sustained flatness is a mild synthetic "
                      "indicator, though loop-based and ambient work behaves "
                      "the same way.", "synthetic-leaning")
    elif lstd < 9:
        t2, n2, f2 = ("Normal arrangement dynamics",
                      "Level moves between sections as expected of a written "
                      "arrangement.", "neutral")
    else:
        t2, n2, f2 = ("Strong sectional level contrast",
                      "Pronounced loudness movement between sections, "
                      "characteristic of live or through-composed material.",
                      "human-leaning")
    out.append({"type": "loudness-movement", "weight": "secondary", "title": t2,
                "detail": f"Short-term RMS standard deviation {lstd:.2f} dB. {n2}",
                "flag": f2})

    if dy["clipped_ratio"] > 0.0005:
        out.append({
            "type": "clipping", "weight": "tertiary",
            "title": "Full-scale samples detected",
            "detail": (f"{dy['clipped_ratio'] * 100:.3f}% of samples sit at or "
                       f"above digital full scale. Indicates clipping in the "
                       f"master or an intersample-peak issue on export."),
            "flag": "neutral",
        })
    return out


def tonal_findings(tn: Dict, on: Dict) -> List[Dict]:
    out: List[Dict] = []

    clarity = tn["key_clarity"]
    strength = ("unambiguous" if clarity > 0.12 else
                "reasonably clear" if clarity > 0.05 else "ambiguous")
    out.append({
        "type": "key", "weight": "tertiary",
        "title": f"Estimated key: {tn['key']}",
        "detail": (
            f"Krumhansl-Schmuckler profile matching gives {tn['key']} at "
            f"r={tn['key_correlation']:.3f}, {strength} against the runner-up "
            f"(margin {clarity:.3f}). Modulating or chromatic material scores "
            f"lower here by nature."),
        "flag": "neutral",
    })

    h = tn["harmonic_ratio"]
    character = ("strongly harmonic, sustained, pitched material dominates"
                 if h > 0.7 else
                 "percussion-forward, transient energy dominates" if h < 0.4
                 else "balanced between harmonic and percussive energy")
    out.append({
        "type": "texture", "weight": "tertiary",
        "title": "Harmonic/percussive balance",
        "detail": (
            f"Median-filter separation splits the signal {h * 100:.0f}% "
            f"harmonic / {tn['percussive_ratio'] * 100:.0f}% percussive, "
            f"{character}. Onset density {on['onset_density']:.2f} events per "
            f"second across {on['onset_count']} detected attacks, with "
            f"{on['onset_regularity'] * 100:.0f}% inter-onset regularity."),
        "flag": "neutral",
    })
    return out


def stereo_findings(st: Dict) -> List[Dict]:
    if not st.get("is_stereo"):
        return [{
            "type": "stereo", "weight": "secondary",
            "title": "Mono source",
            "detail": ("The file carries a single channel, so no stereo image "
                       "analysis is possible. Worth noting: some generative "
                       "systems output mono and it is upmixed later."),
            "flag": "neutral",
        }]

    w, corr = st["width"], st["correlation"]
    if w < 0.05:
        t, n, f = ("Near-mono stereo image",
                   "The two channels are almost identical, leaving no usable "
                   "stereo field. Real productions place sources deliberately "
                   "across the image; several generative systems render "
                   "effectively mono.", "synthetic-leaning")
    elif w > 0.45 or corr < 0.1:
        t, n, f = ("Very wide or decorrelated image",
                   "Unusually high side energy. Either aggressive width "
                   "processing, or the artificial decorrelation some "
                   "generators produce. Check mono compatibility.",
                   "synthetic-leaning")
    else:
        t, n, f = ("Conventional stereo image",
                   "Side-to-mid balance in the range expected of a normally "
                   "mixed production.", "human-leaning")
    out = [{
        "type": "stereo", "weight": "secondary", "title": t,
        "detail": (f"Stereo width {w:.3f} (0 = mono), L/R correlation "
                   f"{corr:.3f}, side channel at {st['side_energy_db']:.1f} dB "
                   f"against mid at {st['mid_energy_db']:.1f} dB. {n}"),
        "flag": f,
    }]
    return out


def signal_findings(features: Dict) -> List[Dict]:
    if not features:
        return []
    return [
        *spectral_findings(features["spectral"]),
        *dynamics_findings(features["dynamics"]),
        *stereo_findings(features["stereo"]),
        *tonal_findings(features["tonal"], features["onsets"]),
    ]


def signal_summary(findings: List[Dict]) -> Dict:
    """Tally which way the independent signal evidence points."""
    syn = sum(1 for f in findings if f.get("flag") == "synthetic-leaning")
    hum = sum(1 for f in findings if f.get("flag") == "human-leaning")
    total = syn + hum
    if total == 0:
        lean, note = "inconclusive", "No signal-level indicator leans either way."
    elif syn > hum:
        lean = "synthetic-leaning"
        note = (f"{syn} signal indicator{'s' if syn != 1 else ''} lean synthetic "
                f"against {hum} leaning human.")
    elif hum > syn:
        lean = "human-leaning"
        note = (f"{hum} signal indicator{'s' if hum != 1 else ''} lean human "
                f"against {syn} leaning synthetic.")
    else:
        lean, note = "mixed", f"Signal indicators are split {syn} to {hum}."
    return {"lean": lean, "synthetic_indicators": syn,
            "human_indicators": hum, "note": note}
