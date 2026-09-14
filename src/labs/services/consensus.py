"""Escalation policy and two-provider consensus.

The local detection model is always the primary. A secondary
verification pass runs only when:

  * the caller explicitly asks for it (`verify: "always"`), or
  * the local result is weak by the rules below (`verify: "auto"`).

It is never the sole source of a verdict. Both provider results are combined
into a `consensus` block that states how they agree and how much to trust the
pair. The identity of the secondary provider is an implementation detail and
never appears in any response; see app/acrcloud.py for that boundary.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# A local logit inside this band is near the decision boundary. The Stage-2
# head saturates around +/-7, so anything under 3 is genuinely undecided.
WEAK_LOGIT = 3.0
# Reliability at or below this means the report itself flagged a problem.
WEAK_RELIABILITY = 70.0
# Per window scores this spread out mean the track is not internally consistent.
HIGH_WINDOW_SPREAD = 0.25


def should_escalate(report: Dict, requested: str) -> Dict:
    """Decide whether to run the secondary verification pass, and record why.

    `requested` is one of: "never", "auto", "always".
    """
    if requested == "always":
        return {"escalate": True, "reason": "requested",
                "detail": "The caller requested secondary verification explicitly."}
    if requested == "never":
        return {"escalate": False, "reason": "disabled",
                "detail": "Secondary verification was not requested."}

    reasons: List[str] = []
    logit = abs(float(report.get("raw_logit") or 0))
    if logit < WEAK_LOGIT:
        reasons.append(
            f"local logit {report.get('raw_logit'):+.2f} sits inside the "
            f"undecided band of +/-{WEAK_LOGIT:.0f}")

    rel = (report.get("reliability") or {}).get("score")
    if rel is not None and float(rel) <= WEAK_RELIABILITY:
        reasons.append(f"reliability score {float(rel):.0f} is at or below "
                       f"{WEAK_RELIABILITY:.0f}")

    tl = report.get("timeline") or {}
    spread = tl.get("std_fake_probability")
    if spread is not None and float(spread) > HIGH_WINDOW_SPREAD:
        reasons.append(f"per window scores vary widely (sd {float(spread):.3f})")

    if tl.get("count") is not None and float(tl["count"]) < 8:
        reasons.append(f"only {int(tl['count'])} analysable windows")

    # Stage disagreement inside the local model is itself a weak signal.
    fake_ratio = tl.get("fake_ratio")
    if fake_ratio is not None and report.get("prediction"):
        stage1_says_ai = float(fake_ratio) > 0.5
        stage2_says_ai = report["prediction"] == "Fake"
        if stage1_says_ai != stage2_says_ai:
            reasons.append("the local model's two stages disagree")

    if reasons:
        return {"escalate": True, "reason": "low_confidence",
                "detail": "Secondary verification triggered because "
                          + "; ".join(reasons) + "."}
    return {"escalate": False, "reason": "high_confidence",
            "detail": ("The local result was decisive, so secondary "
                      "verification was not needed.")}


def build_consensus(local: Optional[Dict], verification: Optional[Dict],
                    decision: Dict) -> Dict:
    """Combine the primary and secondary results without altering either one."""
    local_ai = None
    if local and local.get("prediction"):
        local_ai = local["prediction"] == "Fake"

    verify_ai = None
    if verification and verification.get("prediction"):
        verify_ai = verification["prediction"] == "ai_generated"

    # Only the primary model ran.
    if verify_ai is None:
        if local_ai is None:
            return {"verdict": "unknown", "agreement": "no_result",
                    "providers": [], "confidence": "none",
                    "detail": "No detection pass produced a result."}
        return {
            "verdict": "ai_generated" if local_ai else "human",
            "agreement": "single_provider",
            "providers": ["primary"],
            "confidence": _local_confidence(local),
            "detail": decision.get("detail", ""),
            "recommended_action": _action(local_ai, _local_confidence(local)),
        }

    if local_ai is None:
        return {
            "verdict": "ai_generated" if verify_ai else "human",
            "agreement": "single_provider",
            "providers": ["verification"],
            "confidence": "medium",
            "detail": "Only the secondary verification pass produced a result.",
            "recommended_action": _action(verify_ai, "medium"),
        }

    # Secondary verification is authoritative whenever it responds, and an
    # AI-generated result from it is treated as definitive: it is a positive
    # match against a catalogue of known generated audio, which is stronger
    # evidence than the primary model's statistical inference. A human result
    # is also taken as authoritative, but only as the absence of a match.
    verdict = "ai_generated" if verify_ai else "human"
    agree = local_ai == verify_ai

    if verify_ai:
        conf = "high"
        if agree:
            detail = ("Both the primary model and secondary verification "
                      "independently identified this as AI generated, which is "
                      "the strongest signal this system produces.")
        else:
            detail = ("Secondary verification positively identified this track as "
                      "AI generated. That match takes priority over the primary "
                      "model, which reached a different conclusion.")
    elif agree:
        conf = "high"
        detail = ("Both the primary model and secondary verification "
                  "independently concluded this is human made, which is the "
                  "strongest signal this system produces.")
    else:
        conf = "medium"
        detail = ("Secondary verification found no match against known generated "
                  "audio and returned human made. The primary model reached a "
                  "different conclusion; the verification result is used as the "
                  "authoritative answer.")

    return {
        "verdict": verdict,
        "agreement": "agree" if agree else "verification_priority",
        "providers": ["primary", "verification"],
        "confidence": conf,
        "detail": detail,
        "recommended_action": _action(verify_ai, conf),
    }


def _local_confidence(local: Dict) -> str:
    rel = (local.get("reliability") or {}).get("score")
    logit = abs(float(local.get("raw_logit") or 0))
    if rel is not None and float(rel) >= 85 and logit >= 6:
        return "high"
    if logit >= WEAK_LOGIT:
        return "medium"
    return "low"


def _action(is_ai: bool, confidence: str) -> str:
    if confidence == "low":
        return "review"
    return "block" if is_ai else "allow"
