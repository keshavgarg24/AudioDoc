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

WHY THE AGREEMENT BONUS RAMPS INSTEAD OF SWITCHING
--------------------------------------------------
The agreement bonus used to be applied as a step: the moment both detectors
crossed 0.6 the fused score was multiplied by 1.08 and the confidence
multiplier jumped from 0.55 to 1.15. That made the published score
DISCONTINUOUS at the band edge - two detectors at 0.599/0.600 fused to 0.600,
and at 0.600/0.600 to 0.648, for an input difference of one thousandth. A
number with a cliff in it cannot be thresholded or compared: whichever side of
the cliff a track lands on is an artifact of the band edge, not of the audio.

Both the bonus and the multiplier now ramp from zero at the band edge to full
at saturation, so the edge is a point where nothing happens rather than a step.
Inside the disagreement region the behaviour is unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from ..core.config import ScreenConfig

AGREE_AI = "agree-ai"
AGREE_HUMAN = "agree-human"
DISAGREE = "disagree"
SINGLE = "single"


def _ramp(value: float, start: float, end: float) -> float:
    """0.0 at `start`, 1.0 at `end`, linear between, clamped outside.

    `end` may be below `start` (the human side ramps from 0.4 down to 0.0).
    """
    if end == start:
        return 1.0
    return max(0.0, min(1.0, (value - start) / (end - start)))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class EnsembleResult:
    available: bool
    probability: Optional[float] = None
    agreement: str = ""
    agreement_score: float = 0.0      # 1.0 identical, 0.0 opposite
    # How far past the band edge the WEAKER detector sits: 0.0 exactly at the
    # edge, 1.0 at saturation. This is what sizes the bonus, and publishing it
    # is what makes the fused score auditable rather than just asserted.
    agreement_strength: float = 0.0
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
        # Ramped on the WEAKER of the two: a pair at 0.99/0.61 has one detector
        # barely inside the band, and that is the one that should size the
        # bonus. Taking the mean or the stronger score would let a saturated
        # detector drag a hesitant one across the line.
        strength = _ramp(min(p_fp, p_cp), hi, 1.0)
        return EnsembleResult(
            available=True,
            probability=round(min(1.0, fused * _lerp(1.0, cfg.agreement_boost,
                                                     strength)), 4),
            agreement=AGREE_AI, agreement_score=agreement_score, models=models,
            agreement_strength=round(strength, 4),
            confidence_multiplier=round(_lerp(cfg.disagreement_confidence,
                                              cfg.agreement_confidence,
                                              strength), 4),
            interpretation="both detectors agree on AI origin",
            note="The spectral comb and the learned CQT texture both indicate "
                 "generation. Two independent representations, one conclusion.")

    if p_fp <= lo and p_cp <= lo:
        strength = _ramp(max(p_fp, p_cp), lo, 0.0)
        return EnsembleResult(
            available=True,
            probability=round(max(0.0, fused / _lerp(1.0, cfg.agreement_boost,
                                                     strength)), 4),
            agreement=AGREE_HUMAN, agreement_score=agreement_score,
            models=models, agreement_strength=round(strength, 4),
            confidence_multiplier=round(_lerp(cfg.disagreement_confidence,
                                              cfg.agreement_confidence,
                                              strength), 4),
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
