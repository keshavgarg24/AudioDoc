"""Fusion of the two Level-1 detectors.

WHY NOT max()
-------------
    final = max(p_fakeprint, p_cepstrum)

is an OR gate. If EITHER model false-positives, the ensemble false-positives.
At 2% FPR each with partly independent errors the union approaches ~4%: the
false-accusation rate has doubled while the change felt like an improvement.
On a marketplace that is the expensive direction to be wrong in.

WHAT THIS DOES INSTEAD
----------------------
Agreement raises confidence. Disagreement lowers it AND is recorded, because
which model dissented tells you which failure mode you are in:

  both high        two representations, two methods, one conclusion. Confident.
  both low         the same, in the other direction. Confident.
  cepstrum only    texture present, comb absent. Consistent with PROCESSED
                   generator output: a pitch shift or resample smears the comb
                   while the texture survives on a log axis. Suspicious.
  fakeprint only   comb present, texture absent. Bitcrushing and sample-rate
                   reduction manufacture a comb by the same mechanism as
                   vocoder upsampling, so this also fits a heavily processed
                   HUMAN track. Suspicious in the other direction.

A max() throws that distinction away and reports both cases identically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from ..core.config import ScreenConfig

AGREE_AI = "agree-ai"
AGREE_HUMAN = "agree-human"
DISAGREE = "disagree"
SINGLE = "single"


@dataclass
class EnsembleResult:
    available: bool
    probability: Optional[float] = None
    agreement: str = ""
    agreement_score: float = 0.0      # 1.0 identical, 0.0 opposite
    models: Dict[str, Optional[float]] = field(default_factory=dict)
    interpretation: str = ""
    confidence_multiplier: float = 1.0
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def combine(scores: Dict[str, Any], cfg: ScreenConfig) -> EnsembleResult:
    fp = scores.get("fakeprint")
    cp = scores.get("cepstrum")

    p_fp = fp.probability if (fp and fp.available) else None
    p_cp = cp.probability if (cp and cp.available) else None
    models = {"fakeprint": p_fp, "cepstrum": p_cp}

    if p_fp is None and p_cp is None:
        return EnsembleResult(available=False, models=models,
                              note="neither Level-1 model produced a score")

    if p_fp is None or p_cp is None:
        p = p_fp if p_fp is not None else p_cp
        which = "fakeprint" if p_fp is not None else "cepstrum"
        return EnsembleResult(
            available=True, probability=round(float(p), 4), agreement=SINGLE,
            models=models,
            confidence_multiplier=cfg.single_model_confidence,
            interpretation=f"only the {which} model was available",
            note="No cross-check was possible, so confidence is reduced. This "
                 "is a degraded result, not a cheaper one.")

    hi, lo = cfg.high_band, cfg.low_band
    delta = abs(p_fp - p_cp)
    agreement_score = round(max(0.0, 1.0 - delta), 4)
    fused = cfg.fakeprint_weight * p_fp + cfg.cnn_weight * p_cp

    if p_fp >= hi and p_cp >= hi:
        return EnsembleResult(
            available=True,
            probability=round(min(1.0, fused * cfg.agreement_boost), 4),
            agreement=AGREE_AI, agreement_score=agreement_score, models=models,
            confidence_multiplier=cfg.agreement_confidence,
            interpretation="both detectors agree on AI origin",
            note="The spectral comb and the learned CQT texture both indicate "
                 "generation. Two independent representations, one conclusion.")

    if p_fp <= lo and p_cp <= lo:
        return EnsembleResult(
            available=True,
            probability=round(max(0.0, fused / cfg.agreement_boost), 4),
            agreement=AGREE_HUMAN, agreement_score=agreement_score,
            models=models, confidence_multiplier=cfg.agreement_confidence,
            interpretation="both detectors agree on human origin",
            note="Neither the comb nor the texture shows generation artifacts.")

    if p_cp >= hi > p_fp:
        interp = "cepstrum flags, fakeprint does not"
        note = ("The learned CQT texture indicates generation but the spectral "
                "comb is absent. This pattern is consistent with PROCESSED "
                "generator output - pitch shifting, resampling or heavy EQ "
                "smears the comb while the texture survives on a "
                "log-frequency axis. Suspicious, not confirmed.")
    elif p_fp >= hi > p_cp:
        interp = "fakeprint flags, cepstrum does not"
        note = ("A periodic comb is present but the CQT texture does not read "
                "as generated. Bitcrushing and sample-rate reduction "
                "manufacture comb artifacts by the same mechanism as vocoder "
                "upsampling, so this pattern also fits a heavily processed "
                "HUMAN track. Suspicious, not confirmed.")
    else:
        interp = "detectors disagree in the middle band"
        note = ("Neither model is confident and they do not agree. There is no "
                "basis for a verdict at Level 1.")

    return EnsembleResult(
        available=True, probability=round(float(fused), 4), agreement=DISAGREE,
        agreement_score=agreement_score, models=models,
        confidence_multiplier=cfg.disagreement_confidence,
        interpretation=interp, note=note)
