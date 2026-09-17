"""Response field selection.

A full report is large. A caller that only wants the verdict should not have to
receive, parse and pay transfer for the structural analysis and every numeric
series behind it, so `fields` on a submission narrows what comes back.

Selection is by named group rather than by raw key. Groups are stable API
surface; the keys inside one are an implementation detail that may gain members
as the analysis improves, and a caller asking for "musical" should keep
receiving everything musical without having to track that list.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Set

# Always present, whatever was asked for, so a narrowed response is still
# self-describing: what mode produced it, where it came from, how long it took
# and which build answered.
#
# `assessment` is here rather than in the `verdict` group because it is the
# envelope that makes a narrowed response interpretable at all: a caller who
# asked only for `musical` still needs to know what the service concluded, and
# it is four scalars, not a payload worth trimming.
ALWAYS_FIELDS: FrozenSet[str] = frozenset(
    {"mode", "source", "runtime", "model", "assessment", "verdict"})

FIELD_GROUPS: Dict[str, Set[str]] = {
    "verdict": {"prediction", "confidence", "raw_logit", "fake_probability",
                "real_probability", "summary", "assessment", "verdict",
                "band_note", "decisive"},
    "reliability": {"reliability"},
    "timeline": {"timeline"},
    "structure": {"structure"},
    "findings": {"findings", "signal_findings", "signal_summary"},
    "detection": {"detection"},
    "musical": {"musical", "rhythm"},
    "production": {"production"},
    "character": {"character"},
    "industry": {"industry"},
    "features": {"features"},
    "artist": {"artist"},
}


def group_names() -> list:
    """Selectable group names, for documentation and error messages."""
    return sorted(FIELD_GROUPS)


def filter_fields(result: Dict, fields_csv: str) -> Dict:
    """Narrow `result` to the requested groups.

    An unrecognised name is treated as a literal top-level key rather than an
    error: the report grows over time, and refusing a name this build does not
    know would break a caller against a newer field the moment they upgraded.
    """
    requested = {f.strip().lower() for f in fields_csv.split(",") if f.strip()}
    if not requested:
        return result

    keep = set(ALWAYS_FIELDS)
    for name in requested:
        keep |= FIELD_GROUPS.get(name, {name})
    return {k: v for k, v in result.items() if k in keep}
