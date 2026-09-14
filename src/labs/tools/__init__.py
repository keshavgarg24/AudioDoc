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
            "accuracy": s.accuracy,
            "basis": s.basis,
            "limitations": list(s.limitations),
        }
        for s in SPECS.values()
    ]


__all__ = ["REGISTRY", "SPECS", "get", "spec", "slugs",
           "catalogue", "Decoded", "ToolError", "ToolSpec", "decode",
           "file_digest", "params_fingerprint"]
