"""Closing the human feedback loop.

An appeal is only half a data point. It becomes useful when the deep model has
answered and we can say which of the two was right - so this runs on the
completion of any analysis that was created by an escalation, compares the two
verdicts, and writes the outcome back onto the feedback record.

  upheld       Level 2 agrees with Level 1. The human disputed and was wrong.
  overturned   Level 2 disagrees. A CONFIRMED Level-1 false positive, with the
               audio and the disputing human's reason attached.
  inconclusive Level 2 landed in its own uncertainty band. Neither confirms nor
               clears the screen, and recording it as either would be a lie.

The overturn rate over these is a measurement of the detector's error rate on
exactly the population where it matters - tracks confident enough for Level 1
to have stopped, and wrong enough for a person to complain. No offline
evaluation set gives you that.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from ..verdicts import VERDICT_AI, VERDICT_HUMAN, VERDICT_INCONCLUSIVE

log = logging.getLogger(__name__)

# The outcome vocabulary lives here, with the code that decides it. Anything
# that needs to write one imports it rather than restating the string.
PENDING = "pending"
UPHELD = "upheld"
OVERTURNED = "overturned"
INCONCLUSIVE = "inconclusive"


def classify(level_1_verdict: Optional[str],
             level_2_verdict: Optional[str]) -> str:
    """Which way the appeal went."""
    if level_2_verdict in (None, VERDICT_INCONCLUSIVE):
        return INCONCLUSIVE
    if level_1_verdict not in (VERDICT_AI, VERDICT_HUMAN):
        return INCONCLUSIVE
    return UPHELD if level_1_verdict == level_2_verdict else OVERTURNED


def resolve(screen_id: Optional[str], report: Optional[Dict]) -> Optional[str]:
    """Attach the deep verdict to the appeal. Returns the outcome, or None.

    Best effort throughout, and called from a job's completion path: a
    bookkeeping failure here must never turn a finished analysis into a failed
    one. The caller already has the result the user asked for.
    """
    if not screen_id or not report:
        return None

    try:
        from .storage import get_mongo

        mongo = get_mongo()
        if not mongo.enabled:
            return None

        deep_verdict = report.get("verdict")
        screen = mongo.get_screen(screen_id) or {}
        level_1 = (screen.get("result") or {}).get("verdict")

        outcome = classify(level_1, deep_verdict)
        mongo.resolve_feedback(screen_id, outcome, {
            "verdict": deep_verdict,
            "probability": report.get("fake_probability"),
            "raw_logit": report.get("raw_logit"),
            "decisive": report.get("decisive"),
        })

        if outcome == OVERTURNED:
            # Worth a warning rather than an info line. This is a confirmed
            # false positive on the free tier, and a cluster of them is the
            # signal to re-examine the Level-1 thresholds.
            log.warning(
                "Appeal OVERTURNED for screen %s: Level 1 said %s, Level 2 "
                "said %s", screen_id, level_1, deep_verdict)
        else:
            log.info("Appeal %s for screen %s (L1 %s, L2 %s)",
                     outcome, screen_id, level_1, deep_verdict)
        return outcome
    except Exception:
        log.warning("Could not resolve feedback for screen %s", screen_id,
                    exc_info=True)
        return None
