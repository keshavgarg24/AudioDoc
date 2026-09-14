"""Tier orchestration: Level 1, then Level 2, then the rest.

    screen   Level 1 only.                      free, ungated, ~1.5 s
    ai       Level 1, then Level 2 if needed.   gated
    audio    production/musical analysis only.  gated, no detection
    full     Level 1 + Level 2 + everything.    gated, never short-circuits

WHY THE ORDER PAYS
------------------
Level 2 is a 170M-parameter music-SSL backbone over 48 beat-aligned 10 s
windows: 60-90 s of CPU per track. Level 1 is 1.2 MB of ONNX over one decode:
~1.5 s. When Level 1 is decisively certain a track is AI - two independent
representations agreeing, no dissent, and the verdict holding under
perturbation - Level 2 would restate that conclusion rather than revise it. So
`ai` mode stops and the backbone is never loaded.

WHAT DOES NOT SHORT-CIRCUIT, AND WHY
------------------------------------
`full` always runs both levels even when Level 1 is decisive. A full report is
bought for its evidence - the per-window timeline, the embeddings, the
musicological pass - and silently omitting the deep layers because a cheap
model was confident would deliver a thinner document than the one requested.
Early exit is a latency optimisation for a verdict, not for a report.

A confident Level-1 `human-made` NEVER exits, in any mode. See
`screen.policy.decide` for that asymmetry: the two small models only know the
generators they were trained on, so their silence is not evidence, and an
exoneration is exactly the claim that needs the expensive model.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional

from .core.config import Settings
from .screen import policy as screen_policy
from .verdicts import VERDICT_AI, VERDICT_HUMAN, VERDICT_INCONCLUSIVE

log = logging.getLogger(__name__)

LEVEL_1 = "level_1_screen"
LEVEL_2 = "level_2_deep"

MODES = ("screen", "ai", "audio", "full")
# Modes that actually load the 1.29 GB backbone, and therefore need a key with
# the "deep" scope. `audio` is deliberately NOT here: it runs the DSP and
# musicological passes and never touches Stage-1 or Stage-2, so gating it
# behind the deep scope would charge for a tier it does not use.
DEEP_MODES = ("ai", "full")


def _noop(_stage: str) -> None:
    pass


def analyse(path: str, *, mode: str, settings: Settings,
            detector=None, display_name: Optional[str] = None,
            target_genre: Optional[str] = None,
            progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Run the tiers appropriate to `mode` and return one merged report."""
    if mode not in MODES:
        raise ValueError(f"Unknown analysis mode: {mode}")

    tick = progress or _noop
    started = time.time()
    levels: List[str] = []
    screen_result = None

    # ---- Level 1 ------------------------------------------------------
    # Skipped for `audio`, which asks for production analysis and not for a
    # verdict at all, so a detector opinion would be answering a different
    # question than the one posed.
    if mode != "audio" and settings.screen.enabled:
        from .screen import pipeline as screen_pipeline

        tick("screening")
        screen_result = screen_pipeline.run(path, settings.screen,
                                            progress=progress)
        levels.append(LEVEL_1)

        if mode == "screen":
            return _screen_only(screen_result, settings, display_name, path,
                                started, levels)

        if mode == "ai" and screen_result.decisive:
            log.info("Level 1 decisive for %s (%s); skipping the backbone",
                     display_name or path, screen_result.verdict)
            return _screen_only(screen_result, settings, display_name, path,
                                started, levels, early_exit=True)

    if mode == "screen":
        # Level 1 is the whole of this mode, so a disabled tier is a
        # configuration error the caller has to see rather than an empty result.
        raise RuntimeError(
            "mode=screen requires the Level-1 tier, which is disabled "
            "(LABS_SCREEN=0).")

    # ---- Level 2 ------------------------------------------------------
    if detector is None:
        from .ml.detector import get_detector

        detector = get_detector(settings)

    tick("analysing")
    report = detector.predict(path, mode="audio" if mode == "audio" else mode,
                              display_name=display_name,
                              target_genre=target_genre)
    if mode != "audio":
        levels.append(LEVEL_2)

    report["tier"] = mode
    report["levels_run"] = levels
    if screen_result is not None:
        report.setdefault("detection", {})
        report["detection"]["level_1"] = screen_result.as_dict()
        report["detection"]["level_agreement"] = _agreement(
            screen_result, report)
    return report


def _screen_only(result, settings: Settings, display_name: Optional[str],
                 path: str, started: float, levels: List[str],
                 early_exit: bool = False) -> Dict:
    """Shape a Level-1-only result like a full report's detection envelope.

    The field names mirror the deep path deliberately: a frontend that renders
    a verdict should not need to branch on which tier produced it. What differs
    is `levels_run` and `early_exit`, which say plainly how much was actually
    computed - so a caller can tell a cheap answer from an expensive one
    instead of having to infer it.
    """
    import os

    prob = result.probability
    prediction = None
    if result.verdict == VERDICT_AI:
        prediction = "Fake"
    elif result.verdict == VERDICT_HUMAN:
        prediction = "Real"

    out: Dict = {
        "tier": "screen",
        "levels_run": levels,
        "filename": display_name or os.path.basename(path),
        "duration": result.duration_s,
        "mode": "screen",
        "verdict": result.verdict,
        "prediction": prediction,
        "fake_probability": prob,
        "real_probability": round(1.0 - prob, 4) if prob is not None else None,
        "confidence": round(result.confidence * 100, 2),
        "review_recommended": bool(result.decision.get("review_recommended")),
        "reasons": result.decision.get("reasons", []),
        "vetoes": result.decision.get("vetoes", []),
        "caveat": result.decision.get("caveat", ""),
        "detection": {"level_1": result.as_dict(), "level_2": None},
        "elapsed_seconds": round(time.time() - started, 2),
        "model": {"api_version": settings.server.api_version,
                  "level_1_models": list(result.models.keys())},
    }

    if early_exit:
        out["early_exit"] = {
            "exited_at": LEVEL_1,
            "reason": result.decision.get("decided_by"),
            "note": "Level 1 was decisive, so the deep model was not run. "
                    "Request mode=full for the complete report.",
        }
    else:
        out["next_step"] = result.next_step
        if result.next_step == screen_policy.NEXT_ESCALATE:
            out["escalation_note"] = (
                "Level 1 could not settle this track. A Level-1 'human-made' "
                "or 'inconclusive' result is not an exoneration: run mode=ai "
                "or mode=full for the deep model.")
    return out


def _agreement(screen_result, report: Dict) -> Dict:
    """Do the two levels agree, and what does it mean if they do not.

    Recorded rather than resolved. The levels read genuinely different things -
    Level 1 reads narrowband spectral structure left by a signal chain, Level 2
    reads learned musical structure - so a disagreement is a fact about the
    track, and flattening it into a single number throws away the most
    interesting thing either model said.
    """
    from .screen.policy import VERDICT_AI, VERDICT_HUMAN

    # The BANDED verdict, not `prediction`. `prediction` is the raw sign of the
    # logit and is kept only for older callers, so comparing against it would
    # read a coin flip at |logit| 0.4 as a firm position and manufacture a
    # disagreement out of a track the deep model has no opinion about.
    deep = report.get("verdict")
    if deep is None or screen_result.verdict == screen_policy.VERDICT_UNAVAILABLE:
        return {"state": "unavailable",
                "note": "One level did not produce a verdict."}

    if deep == VERDICT_INCONCLUSIVE:
        return {
            "state": "level-2-inconclusive",
            "note": "The deep model's logit sits inside its inconclusive "
                    "band, so there is no deep verdict to compare against.",
        }

    screen_ai = screen_result.verdict == VERDICT_AI
    screen_human = screen_result.verdict == VERDICT_HUMAN
    deep_ai = deep == VERDICT_AI

    if not (screen_ai or screen_human):
        return {
            "state": "level-1-inconclusive",
            "note": "Level 1 reached no verdict, so the deep model stands "
                    "alone. This is the expected path for most traffic.",
        }

    if screen_ai == deep_ai:
        return {
            "state": "agree",
            "note": ("Both levels independently reach the same conclusion, "
                     "from a narrowband spectral signature and from learned "
                     "musical structure respectively. Two different questions, "
                     "one answer."),
        }

    if deep_ai and screen_human:
        return {
            "state": "disagree",
            "note": ("The deep model flags this track and Level 1 does not. "
                     "Consistent with a generator whose signal chain leaves no "
                     "artifact the two small models were trained on - which is "
                     "precisely the case the deep model exists to catch."),
        }
    return {
        "state": "disagree",
        "note": ("Level 1 flags a spectral artifact the deep model does not "
                 "corroborate. Bitcrushing and sample-rate reduction "
                 "manufacture comb artifacts by the same mechanism as vocoder "
                 "upsampling, so a heavily processed human track fits this "
                 "pattern too. Treat as unresolved."),
    }
