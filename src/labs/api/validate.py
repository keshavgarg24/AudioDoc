"""Input validation shared by every submission route.

The routes differ in what they accept, never in how strictly they accept it.
Keeping these here means a rule added for one route cannot silently fail to
apply to the other.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Optional

from .errors import api_error

# Longest filename we will echo back or store. Long enough for any real track
# name, short enough that it cannot be used to bloat a database row or a log
# line.
MAX_FILENAME = 200

# Longest free-text field (the caller's own `reference`). Echoed back
# verbatim, so it is bounded for the same reasons.
MAX_REFERENCE = 512

# Anything in this class is never legitimate in a form field and is a reliable
# marker of an injection attempt: NUL truncates in C string handling, and CR/LF
# are how a value smuggles a second header or a forged log line.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Optional[str], field: str,
               max_length: int = MAX_REFERENCE) -> Optional[str]:
    """Reject control characters, bound the length. Returns the value."""
    if value is None:
        return None
    if _CONTROL.search(value):
        raise api_error(422, "invalid_request",
                        f"{field} contains control characters.")
    if len(value) > max_length:
        raise api_error(422, "invalid_request",
                        f"{field} exceeds {max_length} characters.")
    return value


def require_choice(value: Optional[str], field: str,
                   allowed: Iterable[str], code: str) -> str:
    """Enforce an enum, treating empty and whitespace-only as invalid.

    A form field sent as `mode=` arrives as the empty string, and FastAPI
    substitutes the parameter default for it. Silently analysing in a mode the
    caller did not ask for is worse than refusing: the caller believes they
    selected something and the response does not contradict them.
    """
    options = tuple(allowed)
    if value is None or not value.strip() or value not in options:
        raise api_error(422, code,
                        f"{field} must be one of: {', '.join(options)}.")
    return value


def safe_filename(raw: Optional[str], fallback: str = "audio") -> str:
    """A display name safe to echo, store and log.

    `os.path.basename` alone is not enough. On POSIX it does not treat the
    backslash as a separator, so a Windows-style path survives it intact and
    the traversal segments are echoed back to the caller, written to the
    database, and handed to whatever renders them next.

    The staged file never takes its name from here — `tempfile.mkstemp`
    generates that — so this is about what leaves the service, not about
    where bytes land on disk.
    """
    name = (raw or "").replace("\\", "/")
    name = os.path.basename(name)
    name = _CONTROL.sub("", name)
    name = name.strip().strip(".")
    # Whatever is left cannot climb: no separators survived basename, and a
    # name consisting only of dots has been reduced to nothing.
    if not name or name in (".", ".."):
        return fallback
    return name[:MAX_FILENAME]
