"""Level 1: the cheap, free, ungated AI-music screen.

Two small ONNX models (~1.2 MB together) over two different representations of
the same audio, fused so that agreement raises confidence and disagreement
lowers it, plus three deterministic screens. Runs in a couple of seconds on
CPU and needs no torch, which is what makes it affordable to give away.

Level 2 (`labs.ml.detector`) is the 1.29 GB MERT backbone. It is gated, and it
runs only when Level 1 says it should - see `labs.tiers`.
"""
from .pipeline import ScreenResult, run  # noqa: F401
from .policy import (  # noqa: F401
    NEXT_ESCALATE,
    NEXT_RETURN,
    VERDICT_AI,
    VERDICT_HUMAN,
    VERDICT_INCONCLUSIVE,
    VERDICT_UNAVAILABLE,
)

__all__ = [
    "ScreenResult", "run", "NEXT_ESCALATE", "NEXT_RETURN",
    "VERDICT_AI", "VERDICT_HUMAN", "VERDICT_INCONCLUSIVE", "VERDICT_UNAVAILABLE",
]
