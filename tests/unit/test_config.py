"""Configuration is read from the environment, so its parsing is API surface."""
from __future__ import annotations

import pytest

from labs.core.config import Settings, get_settings


def test_defaults_are_safe_without_any_environment(monkeypatch):
    for var in list(dict(__import__("os").environ)):
        if var.startswith("LABS_"):
            monkeypatch.delenv(var, raising=False)

    s = Settings()
    assert s.server.require_auth is False
    assert s.storage.mongo_uri is None
    assert s.audio.max_upload_bytes == 50 * 1024 * 1024
    assert s.model.autocast_dtype == ""


def test_settings_are_read_fresh_from_the_environment(monkeypatch):
    """Cached settings would make a redeploy with new configuration a no-op
    until the process restarted, which is the opposite of what an operator
    changing an environment variable expects."""
    monkeypatch.setenv("LABS_API_VERSION", "9.9.9")
    assert get_settings().server.api_version == "9.9.9"


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("", False),
    ("nonsense", False),
])
def test_boolean_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("LABS_REQUIRE_AUTH", raw)
    assert Settings().server.require_auth is expected


def test_malformed_integer_falls_back_rather_than_crashing(monkeypatch):
    """A typo in an environment variable must not stop the service booting."""
    monkeypatch.setenv("LABS_MAX_UPLOAD_MB", "fifty")
    assert Settings().audio.max_upload_bytes == 50 * 1024 * 1024


def test_comma_lists_are_split_and_stripped(monkeypatch):
    monkeypatch.setenv("LABS_CORS_ORIGINS",
                       " https://a.example , https://b.example ,, ")
    assert Settings().server.cors_origins == (
        "https://a.example", "https://b.example")


def test_upload_cap_is_expressed_in_megabytes(monkeypatch):
    monkeypatch.setenv("LABS_MAX_UPLOAD_MB", "120")
    assert Settings().audio.max_upload_bytes == 120 * 1024 * 1024


def test_autocast_is_off_by_default(monkeypatch):
    """Enabling autocast changes numerics, and the verdict is a thresholded
    probability, so it must never switch on implicitly."""
    monkeypatch.delenv("LABS_AUTOCAST_DTYPE", raising=False)
    assert Settings().model.autocast_dtype == ""


def test_autocast_value_is_normalised(monkeypatch):
    monkeypatch.setenv("LABS_AUTOCAST_DTYPE", "  BFloat16 ")
    assert Settings().model.autocast_dtype == "bfloat16"


def test_torch_threads_defaults_to_letting_torch_decide(monkeypatch):
    monkeypatch.delenv("LABS_TORCH_THREADS", raising=False)
    assert Settings().model.torch_threads == 0


def test_device_resolution_prefers_an_explicit_request():
    from labs.core.config import resolve_device
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("cpu", "cuda")
