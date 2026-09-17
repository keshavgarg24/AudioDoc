"""One decision envelope, built the same way by both tiers.

WHY THIS MODULE EXISTS
----------------------
Before this, each tier published its own answer in its own shape. Level 1 sent
`probability` (a fused, boosted ensemble score) and a `confidence` meaning
"distance from 0.5, scaled by detector agreement". Level 2 sent
`fake_probability` (a sigmoid of the logit) and a `confidence` meaning
`max(p, 1-p)` - which has a floor of 0.5 and therefore reads as "50% confident"
for a track the model has no opinion about at all. Same field name, two
different quantities, two different ranges. A caller could not compare them and
a dashboard could not render them on one axis.

This module defines the three numbers exactly once:

    score       P(AI-generated) in [0, 1]. 0.5 is the decision boundary.
    label       score >= 0.5 -> ai-generated, else human-made. ALWAYS present.
    confidence  |score - 0.5| * 2, in [0, 1], times whatever reliability
                evidence the tier has. 0.0 means "on the boundary", not
                "half sure".

WHY `label` AND `verdict` ARE BOTH HERE
---------------------------------------
They answer different questions and a forensic service owes both.

    label    "if you forced me to pick a side, which side?"   Always answers.
    verdict  "is this worth acting on?"                       May say no.

Rounding a coin flip to the nearer side and publishing it as a finding is how a
detector acquires a false-accusation rate it cannot see. But refusing to say
anything is useless to a caller running a bulk triage queue who has their own
downstream review step. So both are reported, they are computed from the same
`score`, and the docs say which is which. A caller picks by risk appetite
instead of by whatever the service happened to decide for them.

`band` sits between the two: a graded tag (strong/likely/uncertain) so a caller
can sort a queue by strength without re-deriving thresholds from the raw score.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .verdicts import (
    BAND_LIKELY_AI,
    BAND_LIKELY_HUMAN,
    BAND_STRONG_AI,
    BAND_STRONG_HUMAN,
    BAND_UNCERTAIN,
    VERDICT_AI,
    VERDICT_HUMAN,
)

__all__ = ["build", "label_for", "band_for", "confidence_for", "DECISION_BOUNDARY"]

# The one place this number is written down. `label` is the sign of
# (score - DECISION_BOUNDARY) and nothing else.
DECISION_BOUNDARY = 0.5


def label_for(score: Optional[float]) -> Optional[str]:
    """The unconditional binary call. None only when there is no score at all.

    Deliberately has no inconclusive case: that is what `verdict` is for. A
    caller asking for `label` has already accepted that a track sitting on the
    boundary will be assigned a side.
    """
    if score is None:
        return None
    return VERDICT_AI if score >= DECISION_BOUNDARY else VERDICT_HUMAN


def confidence_for(score: Optional[float], multiplier: float = 1.0) -> float:
    """Distance from the decision boundary, rescaled to [0, 1].

    `multiplier` carries whatever reliability evidence the tier has beyond the
    score itself - detector agreement at Level 1, robustness under
    perturbation, cascade stability at Level 2. It is applied here rather than
    at each call site so the clamp cannot be forgotten.
    """
    if score is None:
        return 0.0
    raw = min(abs(score - DECISION_BOUNDARY) * 2.0, 1.0) * multiplier
    return float(max(0.0, min(raw, 1.0)))


def band_for(score: Optional[float], *, ai_decisive: float,
             human_decisive: float, uncertain_margin: float) -> str:
    """Grade the score into a sortable strength tag.

    The `strong-*` cutoffs are passed in rather than fixed here because each
    tier's decisive point is a property of its own model: Level 1 measures its
    bar in probability space (0.80), Level 2 measures it in logit space
    (|logit| >= 2, which is P = 0.88). Taking them as arguments keeps `band`
    and `verdict` from ever disagreeing about the same track.
    """
    if score is None:
        return BAND_UNCERTAIN
    if abs(score - DECISION_BOUNDARY) < uncertain_margin:
        return BAND_UNCERTAIN
    if score >= ai_decisive:
        return BAND_STRONG_AI
    if score <= human_decisive:
        return BAND_STRONG_HUMAN
    return BAND_LIKELY_AI if score >= DECISION_BOUNDARY else BAND_LIKELY_HUMAN


def build(*, score: Optional[float], verdict: str, confidence: float,
          decided_by: str, ai_decisive: float, human_decisive: float,
          uncertain_margin: float,
          note: str = "") -> Dict[str, Any]:
    """Assemble the envelope. Every detection response carries exactly one."""
    lab = label_for(score)
    band = band_for(score, ai_decisive=ai_decisive,
                    human_decisive=human_decisive,
                    uncertain_margin=uncertain_margin)
    out: Dict[str, Any] = {
        "score": round(score, 4) if score is not None else None,
        "label": lab,
        "verdict": verdict,
        "band": band,
        "confidence": round(float(confidence), 4),
        "margin": (round(abs(score - DECISION_BOUNDARY), 4)
                   if score is not None else None),
        "threshold": DECISION_BOUNDARY,
        "decided_by": decided_by,
        # Said in the payload, not only in the docs, because the distinction is
        # the one most likely to be got wrong by someone integrating quickly.
        "label_vs_verdict": (
            "`label` is the side of the 0.5 boundary and is always populated. "
            "`verdict` is the same score after an uncertainty band and may be "
            "`inconclusive`. Use `label` for ranking or bulk triage; use "
            "`verdict` when a wrong call has a cost."),
    }
    if note:
        out["note"] = note
    return out


def agrees(label: Optional[str], verdict: str) -> bool:
    """True when the banded verdict backs the binary label.

    False here is not an error - it is the normal state for a track inside the
    uncertainty band, and it is the flag a reviewer should sort on.
    """
    if label is None:
        return False
    return verdict in (VERDICT_AI, VERDICT_HUMAN) and verdict == label
