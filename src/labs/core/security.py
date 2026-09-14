"""API key authentication, scopes, quotas and rate limiting.

Keys are shown to the caller exactly once, at creation, and only a SHA-256
digest is stored. A leaked database therefore does not leak usable keys.

Two sources, checked in order:

  1. MongoDB `api_keys`  full records: owner, scopes, daily quota, revocation
  2. LABS_API_KEYS        static env list, no identity, no quota

The env list is the bootstrap path so the service is usable before a database
exists. Once MongoDB is configured, create real keys with:

    labs-keys create --name "web frontend" --scopes analyze,read

Auth is only enforced when LABS_REQUIRE_AUTH=true, or when at least one key
source is configured. A deployment with neither stays open, which is what you
want for local development and nothing else.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

KEY_PREFIX = "labs"
# "screen" is Level 1: the free, ungated tier. "deep" is Level 2, the MERT
# backbone, which costs 60-90 s of CPU per track and is therefore billable.
# Splitting them is what lets one deployment serve an open screen endpoint and
# a paid deep endpoint without running two services.
#
# "analyze" predates the split and still grants the deep tier, so existing keys
# keep working after an upgrade. New keys should be issued with explicit
# scopes; see labs-keys and docs/security.md.
SCOPES = ("screen", "analyze", "deep", "read", "admin")

# Whether the deep tier requires its own scope. On by default: the backbone
# costs 60-90 s of CPU per track, so serving it to unauthenticated callers is
# a standing invitation to have it mined.
#
# Turning this off restores the pre-tier behaviour exactly, where "analyze"
# alone admitted every mode. That is the right setting for local development
# and for a private deployment behind its own gateway.
def gate_deep() -> bool:
    # Read per call rather than cached at import, so a test (or an operator
    # flipping it) takes effect without reimporting the module.
    from .config import _env_bool

    return _env_bool("LABS_GATE_DEEP", True)

# Level-2 modes. Kept here rather than in the router so the auth rule and the
# billable surface cannot drift apart.
DEEP_SCOPE = "deep"
SCREEN_SCOPE = "screen"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# --------------------------------------------------------------------------
# key material
# --------------------------------------------------------------------------
# A key is "<prefix>_<env>_<secret>". Only these two leading segments are
# structural; everything after the second underscore is secret material.
_KEY_SEGMENTS = 3

# How much of the secret appears in a fingerprint. Six base64 characters is
# ~36 bits: enough to tell two keys apart on sight, and it leaves the
# remaining 37 characters (~220 bits) of a 32-byte token untouched.
_FINGERPRINT_CHARS = 6


def generate_key(env: str = "live") -> str:
    """A fresh key. Returned once, never stored in this form."""
    return f"{KEY_PREFIX}_{env}_{secrets.token_urlsafe(32)}"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def key_fingerprint(raw: str) -> str:
    """A short, non-secret identifier safe to show in a UI or a log line.

    The split is bounded to `_KEY_SEGMENTS`. That bound is the whole point:
    `secrets.token_urlsafe` draws from an alphabet that includes the
    underscore, so an unbounded split cuts the *secret* into pieces as well as
    the prefix, and rejoining all but the last piece emits most of the secret
    into a value that is written to the database, returned by the key
    management commands and printed in logs.
    """
    raw = raw.strip()
    parts = raw.split("_", _KEY_SEGMENTS - 1)
    if len(parts) == _KEY_SEGMENTS:
        prefix, env, secret = parts
        return f"{prefix}_{env}_{secret[:_FINGERPRINT_CHARS]}..."
    # Not one of ours - a static key from the environment can be any string.
    return f"{raw[:_FINGERPRINT_CHARS]}..."


# --------------------------------------------------------------------------
# principal
# --------------------------------------------------------------------------
@dataclass
class Principal:
    """Who is calling."""
    id: Optional[str]
    name: str
    scopes: Tuple[str, ...] = ("analyze", "read")
    source: str = "database"
    quota_per_day: int = 0          # 0 = unlimited
    fingerprint: str = ""

    def can(self, scope: str) -> bool:
        if "admin" in self.scopes or scope in self.scopes:
            return True
        # "analyze" grants the free screen, because a key that may submit an
        # analysis may certainly submit the cheap version of one. It does NOT
        # grant "deep": that is the entire point of the split, and a key that
        # inherited the billable tier would give it away silently.
        if scope == SCREEN_SCOPE and "analyze" in self.scopes:
            return True
        # The one escape hatch, and it is explicit and deployment-wide rather
        # than per-key, so it cannot be granted by accident.
        if scope == DEEP_SCOPE and not gate_deep():
            return True
        return False


# Unchanged from before the tier split, plus the free screen. An open
# deployment therefore keeps serving every pre-existing route exactly as it
# did, and gains /v1/screen - but not the backbone, which now needs a key
# carrying "deep" unless LABS_GATE_DEEP=0.
ANONYMOUS = Principal(id=None, name="anonymous",
                      scopes=(SCREEN_SCOPE, "analyze", "read"),
                      source="open", fingerprint="-")


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------
class RateLimiter:
    """Per-key sliding window, in process.

    Adequate for a single container. If this ever runs on more than one
    instance, move the window into Redis or accept per-instance limits.
    """

    # Buckets are pruned when the tracked-key count crosses this. Every
    # distinct key that ever calls gets an entry, and without a sweep the dict
    # only ever grows: a deployment rotating keys, or any caller iterating
    # them, turns the limiter into a slow memory leak in a long-lived process.
    _PRUNE_ABOVE = 4096

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._inflight: Dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        """Drop buckets with no hits inside the window. Caller holds the lock."""
        if len(self._hits) <= self._PRUNE_ABOVE:
            return
        dead = [k for k, q in self._hits.items()
                if (not q or now - q[-1] > 60.0) and not self._inflight.get(k)]
        for k in dead:
            self._hits.pop(k, None)
            self._inflight.pop(k, None)

    def check(self, key: str) -> Tuple[bool, Optional[int]]:
        """(allowed, retry_after_seconds)."""
        if self.per_minute <= 0:
            return True, None
        now = time.time()
        with self._lock:
            self._prune(now)
            q = self._hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            if len(q) >= self.per_minute:
                return False, max(1, int(60 - (now - q[0])) + 1)
            q.append(now)
            return True, None

    def acquire(self, key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        with self._lock:
            if self._inflight[key] >= limit:
                return False
            self._inflight[key] += 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            if self._inflight.get(key, 0) > 0:
                self._inflight[key] -= 1

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "per_minute": self.per_minute,
                "tracked_keys": len(self._hits),
                "inflight": {k: v for k, v in self._inflight.items() if v},
            }


_limiter: Optional[RateLimiter] = None
_llock = threading.Lock()


def get_limiter(per_minute: Optional[int] = None) -> RateLimiter:
    global _limiter
    with _llock:
        if _limiter is None:
            from ..core.config import get_settings
            _limiter = RateLimiter(
                per_minute if per_minute is not None
                else get_settings().server.rate_limit_per_min)
        return _limiter


def reset_limiter() -> None:
    """Test hook: discard the cached rate-limiter singleton."""
    global _limiter
    with _llock:
        _limiter = None


# --------------------------------------------------------------------------
# key management
# --------------------------------------------------------------------------
def create_key(name: str, owner: Optional[str] = None,
               scopes: Optional[List[str]] = None,
               quota_per_day: int = 0, env: str = "live") -> Dict:
    """Mint a key. The raw value is in the return and is never recoverable."""
    from ..services.storage import get_mongo
    mongo = get_mongo()
    if not mongo.enabled:
        raise RuntimeError(
            "LABS_MONGO_URI is not configured, so keys cannot be stored. "
            "Use LABS_API_KEYS for a static key instead.")

    scopes = [s for s in (scopes or ["analyze", "read"]) if s in SCOPES]
    raw = generate_key(env)
    doc = {
        "key_hash": hash_key(raw),
        "fingerprint": key_fingerprint(raw),
        "name": name,
        "owner": owner,
        "scopes": scopes,
        "quota_per_day": int(quota_per_day),
        "active": True,
        "created_at": _utcnow(),
        "last_used_at": None,
    }
    res = mongo.db().api_keys.insert_one(doc)
    log.info("Created API key '%s' (%s)", name, doc["fingerprint"])
    return {"id": str(res.inserted_id), "key": raw, **{
        k: v for k, v in doc.items() if k != "key_hash"}}


def revoke_key(fingerprint: str) -> bool:
    from ..services.storage import get_mongo
    mongo = get_mongo()
    if not mongo.enabled:
        return False
    res = mongo.db().api_keys.update_one(
        {"fingerprint": fingerprint},
        {"$set": {"active": False, "revoked_at": _utcnow()}})
    return res.modified_count > 0


def list_keys() -> List[Dict]:
    from ..services.storage import get_mongo
    mongo = get_mongo()
    if not mongo.enabled:
        return []
    return list(mongo.db().api_keys.find({}, {"key_hash": 0}))


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------
def authenticate(raw_key: Optional[str],
                 settings=None) -> Tuple[Optional[Principal], Optional[str]]:
    """(principal, error_code). A None principal with a None error means open.

    `settings` is injectable so callers that hold their own Settings instance
    (and tests that swap one in) are authoritative, rather than this silently
    re-reading the process environment.
    """
    from ..core.config import get_settings
    from ..services.storage import get_mongo

    settings = settings or get_settings()
    mongo = get_mongo()
    static_keys = settings.server.api_keys

    # Whether MongoDB happens to be configured for storage has nothing to do
    # with whether auth is enforced. Auth is on only when explicitly asked
    # for (LABS_REQUIRE_AUTH=true) or a static key already exists. Presence of
    # database-issued keys is checked below, on the actual request, not here
    # against every anonymous caller with no key at all.
    auth_configured = settings.server.require_auth or bool(static_keys)

    if not auth_configured:
        return ANONYMOUS, None

    if not raw_key:
        return None, "missing_api_key"

    raw_key = raw_key.strip()

    # Static env keys first: cheap, and the bootstrap path.
    #
    # compare_digest rather than `in`: the plain membership test short-circuits
    # on the first differing byte, so response time leaks how much of a guess
    # was correct and a key can be recovered a byte at a time. Every candidate
    # is compared so the loop itself does not become the oracle instead.
    matched = False
    for candidate in static_keys:
        if secrets.compare_digest(raw_key, candidate):
            matched = True
    if matched:
        return Principal(
            id=None, name="static", scopes=("analyze", "read"),
            source="environment", fingerprint=key_fingerprint(raw_key)), None

    if not mongo.enabled:
        return None, "invalid_api_key"

    try:
        doc = mongo.db().api_keys.find_one({"key_hash": hash_key(raw_key)})
    except Exception:
        log.warning("Key lookup failed", exc_info=True)
        return None, "auth_unavailable"

    if not doc:
        return None, "invalid_api_key"
    if not doc.get("active", True):
        return None, "revoked_api_key"

    principal = Principal(
        id=str(doc["_id"]),
        name=doc.get("name") or "unnamed",
        scopes=tuple(doc.get("scopes") or ("analyze", "read")),
        source="database",
        quota_per_day=int(doc.get("quota_per_day") or 0),
        fingerprint=doc.get("fingerprint") or key_fingerprint(raw_key),
    )

    if principal.quota_per_day:
        used = mongo.usage_today(principal.id)
        if used >= principal.quota_per_day:
            return None, "quota_exceeded"

    try:
        mongo.db().api_keys.update_one(
            {"_id": doc["_id"]}, {"$set": {"last_used_at": _utcnow()}})
    except Exception:
        # Best effort: a bookkeeping write must never fail an authenticated
        # request. Logged at debug rather than swallowed silently, because a
        # version of this that fails every time is otherwise invisible - and
        # last_used_at is what an audit uses to find keys to retire.
        log.debug("Could not update last_used_at for key %s",
                  doc.get("fingerprint"), exc_info=True)

    return principal, None
