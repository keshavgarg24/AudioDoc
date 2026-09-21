"""core.warmup: the sweep runs every entry point and never raises.

The module exists to remove a 20-second first request on fresh containers,
so the properties worth pinning are that the sweep covers every registered
tool (a new tool is warmed the day it is registered), that a baked cache
short-circuits it, and that nothing in it can take a container down.
"""
from __future__ import annotations

import numpy as np
import pytest

from labs.core import warmup


def test_signal_is_stereo_float32():
    y = warmup._signal()
    assert y.dtype == np.float32
    assert y.ndim == 2 and y.shape[1] == 2
    assert y.shape[0] == int(warmup._SR * warmup._SECONDS)
    assert np.isfinite(y).all()


def test_baked_cache_short_circuits_the_sweep(tmp_path, monkeypatch):
    (tmp_path / "kernel-1.nbi").write_bytes(b"")
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path))
    out = warmup.warm_signal_paths()
    assert out == {"cached_entries": 1, "total": 0.0}


def test_empty_cache_dir_runs_the_sweep(tmp_path, monkeypatch):
    pytest.importorskip("librosa")
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path))
    out = warmup.warm_signal_paths()
    assert out.get("total", 0) > 0 and "cached_entries" not in out


def test_sweep_covers_every_registered_tool():
    pytest.importorskip("librosa")
    from labs.tools import slugs

    out = warmup.warm_signal_paths(force=True)
    for name in ["decode", "features", "musical", "production", *slugs()]:
        assert name in out, f"{name} not in the sweep"
        assert out[name] is not None, f"{name} failed during the sweep"


def test_sweep_never_raises(monkeypatch):
    def broken(_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(warmup, "_steps", broken)
    assert warmup.warm_signal_paths(force=True)["total"] >= 0
