"""The tool layer: one measurement product per module, one registry here.

Adding a tool means writing a module with a `SPEC` and a `run()`, then listing
it below. The API, the docs endpoint and the result cache all read from the
registry, so nothing else has to change.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import (
    beat_vocal_fit,
    key_lab,
    master_check,
    reference_match,
    tempo_lab,
    vocal_lab,
)
from .base import Decoded, ToolError, ToolSpec, decode, file_digest, params_fingerprint

_MODULES = (master_check, tempo_lab, key_lab, reference_match, vocal_lab,
            beat_vocal_fit)

REGISTRY: Dict[str, object] = {m.SPEC.slug: m for m in _MODULES}
SPECS: Dict[str, ToolSpec] = {m.SPEC.slug: m.SPEC for m in _MODULES}

# Every `typical_seconds` range is measured, not estimated, and they mean a
# specific thing. Saying so matters because an integrator sizes timeouts and
# progress UI off these numbers, and the two things they exclude - queue wait
# and upload - are both larger than the tool runtime under load.
TYPICAL_SECONDS_NOTE = (
    "Compute time on the analysis worker for a three to five minute track, "
    "measured on this deployment. Runtime scales close to linearly with audio "
    "duration. Excludes upload, queue wait and your own polling interval, so "
    "the wall-clock time you observe will be longer - size client timeouts "
    "well above the upper bound."
)


def get(slug: str):
    return REGISTRY.get(slug)


def spec(slug: str) -> Optional[ToolSpec]:
    return SPECS.get(slug)


def slugs() -> List[str]:
    return list(REGISTRY.keys())


def catalogue() -> List[Dict]:
    """Everything GET /v1/tools advertises."""
    return [
        {
            "slug": s.slug,
            "name": s.name,
            "summary": s.summary,
            "inputs": list(s.inputs),
            "file_count": s.file_count,
            "scope": s.scope,
            "typical_seconds": list(s.typical_seconds),
            "typical_seconds_note": TYPICAL_SECONDS_NOTE,
            "accuracy": s.accuracy,
            "basis": s.basis,
            "limitations": list(s.limitations),
        }
        for s in SPECS.values()
    ]


__all__ = ["REGISTRY", "SPECS", "get", "spec", "slugs",
           "catalogue", "Decoded", "ToolError", "ToolSpec", "decode",
           "file_digest", "params_fingerprint"]
