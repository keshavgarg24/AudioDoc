"""Level-1 decision policy: rules, not a model.

Deliberately asymmetric. On a marketplace, wrongly flagging a human producer
costs a customer and a public complaint; missing one AI track costs very
little. So both bars are high, and the gap between them is reported as
`inconclusive` rather than rounded to whichever side happens to be nearer.

A verdict here carries a `next_step`, because Level 1's job is not only to
answer but to decide whether answering is its business at all. That field is
what the tier orchestrator reads to choose between returning now and paying for
the backbone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..core.config import ScreenConfig
from ..verdicts import (
    NEXT_ESCALATE,
    NEXT_RETURN,
    VERDICT_AI,
    VERDICT_HUMAN,
    VERDICT_INCONCLUSIVE,
    VERDICT_UNAVAILABLE,
)
from .ensemble import AGREE_AI, DISAGREE, SINGLE

__all__ = ["decide", "ScreenDecision", "NEXT_RETURN", "NEXT_ESCALATE",
           "VERDICT_AI", "VERDICT_HUMAN", "VERDICT_INCONCLUSIVE",
           "VERDICT_UNAVAILABLE"]


@dataclass
class ScreenDecision:
    verdict: str
    probability: Optional[float]
    confidence: float
    next_step: str = NEXT_ESCALATE
    reasons: List[str] = field(default_factory=list)
    vetoes: List[str] = field(default_factory=list)
    decided_by: str = "policy"
    review_recommended: bool = False
    caveat: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def decide(*, ensemble, c2pa, container, bandwidth,
           cfg: ScreenConfig) -> ScreenDecision:
    reasons: List[str] = []
    vetoes: List[str] = []

    # A signed declaration outranks every inference below it, and costs nothing.
    if c2pa is not None and c2pa.ai_declared:
        return ScreenDecision(
            verdict=VERDICT_AI, probability=1.0, confidence=0.99,
            next_step=NEXT_RETURN, decided_by="c2pa",
            reasons=[f"A C2PA manifest names a generative tool "
                     f"({c2pa.generator}). This is a signed declaration, not "
                     f"an inference."],
            caveat="Content credentials are trivially stripped, so their "
                   "absence on other files means nothing.")

    if ensemble is None or not ensemble.available or ensemble.probability is None:
        return ScreenDecision(
            verdict=VERDICT_UNAVAILABLE, probability=None, confidence=0.0,
            next_step=NEXT_ESCALATE, decided_by="none",
            reasons=["No Level-1 detector could score this file."],
            review_recommended=True)

    score = float(ensemble.probability)
    confidence = min(abs(score - 0.5) * 2.0, 1.0) * ensemble.confidence_multiplier

    if ensemble.agreement == AGREE_AI:
        reasons.append("both detectors agree on AI origin (spectral comb and "
                       "CQT texture independently indicate generation)")
    elif ensemble.agreement == DISAGREE:
        vetoes.append(f"detectors disagree - {ensemble.interpretation}")
    elif ensemble.agreement == SINGLE:
        vetoes.append("only one detector was available; no cross-check")
    else:
        reasons.append("both detectors agree on human origin")

    if bandwidth is not None and bandwidth.available:
        if bandwidth.unusually_sharp_edge:
            reasons.append("unusually sharp spectral ceiling")
        if bandwidth.inferred_chain:
            reasons.append(f"signal chain: {bandwidth.inferred_chain}")
    if container is not None and container.available:
        reasons.extend(f"container: {s}" for s in container.signals)

    confidence = float(max(0.0, min(confidence, 1.0)))

    # ---- banding -------------------------------------------------------
    if confidence < cfg.min_confidence:
        verdict, review = VERDICT_INCONCLUSIVE, True
        reasons.append("confidence is below the threshold required for a verdict")
    elif score >= cfg.ai_threshold:
        verdict, review = VERDICT_AI, bool(vetoes)
    elif score <= cfg.human_threshold:
        verdict, review = VERDICT_HUMAN, False
    else:
        verdict, review = VERDICT_INCONCLUSIVE, True
        reasons.append(f"score {score:.3f} falls between the human threshold "
                       f"({cfg.human_threshold}) and the AI threshold "
                       f"({cfg.ai_threshold})")

    # ---- what should happen next ---------------------------------------
    #
    # Only the AI side is allowed to stop here, and the asymmetry is the point.
    # Two small models agreeing that a track is generated, with no dissent and
    # no veto, is a conclusion the backbone would restate rather than revise.
    #
    # A confident "human" is the opposite case: it is exactly where the large
    # model earns its cost, because a generator neither Level-1 model was
    # trained on is indistinguishable from human audio TO THEM. Their silence
    # is not evidence. So `human-made` at Level 1 always escalates, and Level 1
    # alone never publishes an exoneration.
    next_step = NEXT_ESCALATE
    if (cfg.exit_on_ai and verdict == VERDICT_AI and not vetoes
            and confidence >= cfg.exit_confidence):
        next_step = NEXT_RETURN
        reasons.append("Level 1 is decisive; the deep model was not run")

    return ScreenDecision(
        verdict=verdict, probability=round(score, 4),
        confidence=round(confidence, 3), next_step=next_step,
        reasons=reasons, vetoes=vetoes, review_recommended=review,
        caveat="A Level-1 'human-made' result means neither small model found "
               "an artifact it recognises. Both know only the generators they "
               "were trained on; neither can rule out one it has never seen. "
               "Use the deep tier for an exoneration.")
