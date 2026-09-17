"""Level 1: the free screen.

    decode once (16 kHz mono)
      -> fakeprint model      averaged linear spectrum -> peak locations
      -> cepstrum model       CQT / DCT texture, median-pooled over windows
      -> ensemble             agreement raises confidence, disagreement lowers it
      -> deterministic screens  C2PA (decisive), container + bandwidth (weak)
      -> policy               asymmetric bands, and a next_step

Every stage is individually exception-isolated. Level 1 is the ungated tier, so
it is the one most exposed to whatever a stranger uploads: a screen that throws
must degrade to "that screen is unavailable" and never to a failed request.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional

from ..assessment import build as build_assessment
from ..core.config import ScreenConfig, get_settings
from . import decode, ensemble, models, policy, screens
from . import robustness as robust

log = logging.getLogger(__name__)

TIER = "screen"


@dataclass
class ScreenResult:
    """Level 1's complete output."""
    tier: str
    verdict: str
    probability: Optional[float]
    confidence: float
    next_step: str
    duration_s: Optional[float]
    elapsed_s: float
    # The cross-tier envelope: score, label, band, confidence, all defined once
    # in assessment.py so Level 1 and Level 2 publish the same quantities under
    # the same names. Empty only when no score was produced at all.
    assessment: Dict[str, Any] = field(default_factory=dict)
    models: Dict[str, Any] = field(default_factory=dict)
    ensemble: Dict[str, Any] = field(default_factory=dict)
    screens: Dict[str, Any] = field(default_factory=dict)
    # Present only when the exit gate ran, i.e. when Level 1 proposed to end
    # the request on its own. Absent is the normal case, not a missing value.
    robustness: Optional[Dict[str, Any]] = None
    decision: Dict[str, Any] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def decisive(self) -> bool:
        """True when Level 1 answered well enough that Level 2 adds nothing."""
        return self.next_step == policy.NEXT_RETURN


def run(path: str, cfg: Optional[ScreenConfig] = None,
        progress: Optional[Callable[[str], None]] = None) -> ScreenResult:
    """Screen one file. Never raises for a per-stage failure."""
    cfg = cfg or get_settings().screen
    started = time.time()
    timings: Dict[str, float] = {}
    errors: Dict[str, str] = {}

    def tick(stage: str) -> None:
        if progress:
            progress(stage)

    def timed(name: str, fn, default=None):
        """Run a stage, record its cost, and isolate its failure.

        Same pattern as the layer runner in the deep tier: one screen raising
        must not take the other three with it, and the reason has to survive
        into the response rather than only into the log.
        """
        t0 = time.time()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("Level-1 stage %s failed", name, exc_info=True)
            errors[name] = str(exc)[:300]
            return default
        finally:
            timings[name] = round(time.time() - t0, 3)

    # -- one decode, shared -------------------------------------------------
    tick("decoding")
    audio = timed("decode", lambda: decode.load(path, cfg))
    if audio is None:
        # Without a decode there is no Level 1 at all. This is the one stage
        # whose failure is total, and it still returns a result rather than
        # raising: the caller needs the reason, and `escalate` is the honest
        # next step because the deep tier decodes differently and may succeed.
        return ScreenResult(
            tier=TIER, verdict=policy.VERDICT_UNAVAILABLE, probability=None,
            confidence=0.0, next_step=policy.NEXT_ESCALATE, duration_s=None,
            elapsed_s=round(time.time() - started, 3), timings=timings,
            errors=errors,
            # Still an envelope, with a null score. A caller parsing
            # `assessment` must never have to branch on its absence.
            assessment=build_assessment(
                score=None, verdict=policy.VERDICT_UNAVAILABLE,
                confidence=0.0, decided_by="level_1_none",
                ai_decisive=cfg.ai_threshold,
                human_decisive=cfg.human_threshold,
                uncertain_margin=cfg.uncertain_margin,
                note="Level 1 could not decode this file, so there is no "
                     "score to place on either side of the boundary."),
            decision={"verdict": policy.VERDICT_UNAVAILABLE,
                      "reasons": ["Level 1 could not decode this file."],
                      "decided_by": "none", "review_recommended": True})

    meta = timed("probe", lambda: decode.probe(path), default={}) or {}

    # -- models -------------------------------------------------------------
    tick("screening")
    scored = timed("models", lambda: models.score_both(audio.y, cfg),
                   default={}) or {}
    fused = timed("ensemble", lambda: ensemble.combine(scored, cfg))

    # -- deterministic screens ---------------------------------------------
    c2pa_res = (timed("c2pa", lambda: screens.c2pa(path))
                if cfg.c2pa_enabled else None)
    container_res = (timed("container", lambda: screens.container(meta))
                     if cfg.container_enabled else None)

    bandwidth_res = None
    if cfg.bandwidth_enabled:
        wide = timed("bandwidth_decode", lambda: decode.load_wideband(path))
        if wide is not None:
            bandwidth_res = timed(
                "bandwidth", lambda: screens.bandwidth(wide[0], wide[1]))

    # -- policy -------------------------------------------------------------
    decision = timed(
        "policy",
        lambda: policy.decide(ensemble=fused, c2pa=c2pa_res,
                              container=container_res,
                              bandwidth=bandwidth_res, cfg=cfg))
    if decision is None:
        decision = policy.ScreenDecision(
            verdict=policy.VERDICT_UNAVAILABLE, probability=None,
            confidence=0.0, next_step=policy.NEXT_ESCALATE,
            reasons=["The Level-1 policy could not be evaluated."],
            decided_by="none", review_recommended=True)

    # -- exit gate ----------------------------------------------------------
    #
    # Only tracks the policy already wants to RETURN on are re-checked, so the
    # cost falls exactly on the requests that were about to skip the backbone.
    # A decisive-looking verdict that moves under a benign perturbation is not
    # decisive, and must not be allowed to end the request: it escalates
    # instead. See robustness.py for why ai1.mp3 makes this non-optional.
    robustness = None
    if decision.next_step == policy.NEXT_RETURN and decision.decided_by != "c2pa":
        tick("verifying")
        robustness = timed("robustness", lambda: robust.check(audio.y, cfg))
        if robustness is not None and robustness.available and not robustness.stable:
            decision.next_step = policy.NEXT_ESCALATE
            decision.vetoes.append(
                f"not stable under benign perturbation - {robustness.note}")
            decision.confidence = round(
                decision.confidence * max(robustness.stability, 0.15), 3)
            decision.review_recommended = True
            # The penalty can drop confidence below the bar that produced the
            # verdict, so the verdict has to be re-asked. See policy.reband.
            policy.reband(decision, cfg)
        elif robustness is not None and robustness.stable:
            decision.reasons.append(
                "verdict is stable under benign perturbation")

    # Built last, so it reflects the robustness penalty and any re-band rather
    # than the policy's first answer.
    assessment = build_assessment(
        score=decision.probability,
        verdict=decision.verdict,
        confidence=decision.confidence,
        decided_by=f"level_1_{decision.decided_by}",
        ai_decisive=cfg.ai_threshold,
        human_decisive=cfg.human_threshold,
        uncertain_margin=cfg.uncertain_margin,
        note="Level 1 reads narrowband spectral structure only. A `human-made` "
             "label here means neither small model recognised an artifact, "
             "which is not the same as an exoneration.")

    return ScreenResult(
        tier=TIER, verdict=decision.verdict, probability=decision.probability,
        confidence=decision.confidence, next_step=decision.next_step,
        assessment=assessment,
        duration_s=round(audio.full_duration or audio.duration, 2),
        elapsed_s=round(time.time() - started, 3),
        models={k: v.as_dict() for k, v in scored.items()},
        ensemble=fused.as_dict() if fused is not None else {},
        screens={
            "c2pa": c2pa_res.as_dict() if c2pa_res is not None else None,
            "container": container_res.as_dict() if container_res is not None else None,
            "bandwidth": bandwidth_res.as_dict() if bandwidth_res is not None else None,
        },
        robustness=robustness.as_dict() if robustness is not None else None,
        decision=decision.as_dict(), timings=timings, errors=errors)
