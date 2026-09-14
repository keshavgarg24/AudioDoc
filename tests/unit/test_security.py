"""API keys, authentication and rate limiting."""
from __future__ import annotations

import inspect
import time

from labs.core import security
from labs.core.config import Settings
from labs.core.security import (
    ANONYMOUS,
    KEY_PREFIX,
    Principal,
    RateLimiter,
    authenticate,
    generate_key,
    hash_key,
    key_fingerprint,
)


# ------------------------------------------------------------ key material --
def test_generated_keys_are_unique_and_prefixed():
    keys = {generate_key() for _ in range(200)}
    assert len(keys) == 200
    assert all(k.startswith(f"{KEY_PREFIX}_live_") for k in keys)


def test_hashing_is_stable_and_one_way():
    raw = generate_key()
    assert hash_key(raw) == hash_key(raw)
    assert len(hash_key(raw)) == 64
    assert raw not in hash_key(raw)


def test_hashing_ignores_surrounding_whitespace():
    """A key pasted with a trailing newline must still authenticate."""
    raw = generate_key()
    assert hash_key(f"  {raw}\n") == hash_key(raw)


def test_fingerprint_does_not_leak_the_key():
    raw = generate_key()
    fp = key_fingerprint(raw)
    assert raw not in fp
    assert fp.endswith("...")
    assert len(fp) < len(raw)


def test_fingerprint_exposes_only_a_few_characters_of_the_secret():
    """`token_urlsafe` emits underscores, so an unbounded split on "_" cuts the
    secret apart and rejoining all but the last piece publishes most of it.
    The fingerprint is stored, logged and displayed, so it must carry only a
    short, bounded prefix of the secret."""
    raw = generate_key()
    secret = raw.split("_", 2)[2]
    fp = key_fingerprint(raw)

    shown = fp[len(f"{KEY_PREFIX}_live_"):].removesuffix("...")
    assert len(shown) <= 8, f"fingerprint exposes {len(shown)} secret chars"
    assert secret[len(shown):] not in fp


def test_fingerprint_survives_underscores_in_the_secret():
    raw = f"{KEY_PREFIX}_live_aaaa_bbbb_cccc_dddd"
    fp = key_fingerprint(raw)
    assert fp.startswith(f"{KEY_PREFIX}_live_")
    assert "dddd" not in fp


def test_fingerprint_handles_keys_that_are_not_ours():
    """Static keys from the environment are arbitrary strings."""
    fp = key_fingerprint("a-plain-static-key")
    assert fp.endswith("...")
    assert "static-key" not in fp


# ------------------------------------------------------------- principals --
def test_admin_scope_implies_every_scope():
    admin = Principal(id="1", name="ops", scopes=("admin",))
    assert admin.can("analyze")
    assert admin.can("read")
    assert admin.can("admin")


def test_scopes_are_otherwise_exact():
    reader = Principal(id="1", name="reader", scopes=("read",))
    assert reader.can("read")
    assert not reader.can("analyze")
    assert not reader.can("admin")


# ---------------------------------------------------------- authentication --
def _settings(**server) -> Settings:
    s = Settings()
    for k, v in server.items():
        object.__setattr__(s.server, k, v)
    return s


def test_open_deployment_returns_anonymous():
    who, err = authenticate(None, settings=_settings(
        api_keys=(), require_auth=False))
    assert err is None
    assert who is ANONYMOUS


def test_missing_key_is_rejected_when_auth_is_required():
    who, err = authenticate(None, settings=_settings(require_auth=True))
    assert who is None
    assert err == "missing_api_key"


def test_valid_static_key_authenticates():
    who, err = authenticate("secret-key", settings=_settings(
        api_keys=("secret-key",)))
    assert err is None
    assert who.source == "environment"


def test_wrong_static_key_is_rejected():
    who, err = authenticate("wrong", settings=_settings(
        api_keys=("secret-key",)))
    assert who is None
    assert err == "invalid_api_key"


def test_static_key_comparison_is_constant_time():
    """A plain `in` test short-circuits on the first differing byte, which
    leaks how much of a guessed key was correct. Assert the implementation
    uses a constant-time comparison rather than trying to time it, which is
    hopelessly flaky in CI."""
    source = inspect.getsource(security.authenticate)
    assert "compare_digest" in source
    assert "raw_key in static_keys" not in source


def test_every_candidate_key_is_compared():
    """Breaking out of the loop on the first match reintroduces the timing
    oracle at the level of key position rather than key byte."""
    source = inspect.getsource(security.authenticate)
    body = source.split("for candidate in static_keys:")[1].split("if matched")[0]
    assert "break" not in body


# ------------------------------------------------------------ rate limiting --
def test_requests_are_allowed_up_to_the_limit():
    limiter = RateLimiter(per_minute=3)
    assert all(limiter.check("k")[0] for _ in range(3))


def test_request_over_the_limit_is_refused_with_a_retry_hint():
    limiter = RateLimiter(per_minute=2)
    limiter.check("k")
    limiter.check("k")
    allowed, retry_after = limiter.check("k")
    assert allowed is False
    assert 0 < retry_after <= 61


def test_limits_are_per_key():
    limiter = RateLimiter(per_minute=1)
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert limiter.check("a")[0] is False


def test_zero_disables_the_limiter():
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.check("k")[0] for _ in range(500))


def test_window_slides(monkeypatch):
    limiter = RateLimiter(per_minute=1)
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False

    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 61)
    assert limiter.check("k")[0] is True


# --------------------------------------------------------------- inflight --
def test_inflight_slots_are_bounded_and_released():
    limiter = RateLimiter(per_minute=0)
    assert limiter.acquire("k", 2) is True
    assert limiter.acquire("k", 2) is True
    assert limiter.acquire("k", 2) is False

    limiter.release("k")
    assert limiter.acquire("k", 2) is True


def test_release_below_zero_is_ignored():
    """A double release must not create free capacity out of nothing."""
    limiter = RateLimiter(per_minute=0)
    limiter.release("k")
    limiter.release("k")
    assert limiter.acquire("k", 1) is True
    assert limiter.acquire("k", 1) is False


def test_zero_inflight_limit_is_unbounded():
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.acquire("k", 0) for _ in range(100))
