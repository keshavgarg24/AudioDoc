"""Shared fixtures.

The environment is configured before any `labs` module is imported. Settings
are read from the environment at import time, so a setdefault after the first
import would be silently ignored and tests would run against whatever the
developer happened to have exported.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

# Pre-import modules that use threading._register_atexit at import time.
# On Python 3.14, importing concurrent.futures.process from inside a
# ThreadPoolExecutor thread that is being shut down raises RuntimeError.
# Importing it here (main thread, before any worker threads start) caches
# it in sys.modules so worker threads never need to import it themselves.
try:
    import concurrent.futures.process  # noqa: F401

    import joblib  # noqa: F401
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Never touch the network, never load weights, never reach a real database.
os.environ.setdefault("LABS_DEVICE", "cpu")
os.environ.setdefault("LABS_EAGER_LOAD", "false")
os.environ.setdefault("LABS_OFFLINE", "true")
os.environ.pop("LABS_MONGO_URI", None)

CHECKPOINT_DIR = Path(os.environ.get("LABS_CKPT_DIR", ROOT / "checkpoints"))
AUDIO_DIR = ROOT / "tests" / "fixtures" / "audio"


def _have_checkpoints() -> bool:
    return ((CHECKPOINT_DIR / "Stage-1.ckpt").is_file()
            and (CHECKPOINT_DIR / "Stage-2.ckpt").is_file())


requires_checkpoints = pytest.mark.skipif(
    not _have_checkpoints(),
    reason="Stage-1/Stage-2 checkpoints not present")

MODELS_DIR = Path(os.environ.get("LABS_SCREEN_MODELS_DIR")
                  or SRC / "labs" / "screen" / "weights")


def _have_screen_models() -> bool:
    return ((MODELS_DIR / "fakeprint.onnx").is_file()
            and (MODELS_DIR / "cepstrum_cnn.onnx").is_file())


# These ship inside the package, so in any working tree this marker never
# skips. If it DOES skip, the Level-1 weights are missing from the package and
# the free tier is broken - which is exactly the bug this replaced.
requires_screen_models = pytest.mark.skipif(
    not _have_screen_models(), reason="Level-1 ONNX models not present")


# --------------------------------------------------------------------------
# synthetic audio
# --------------------------------------------------------------------------
def make_wav(seconds: float = 1.0, sample_rate: int = 8000,
             channels: int = 1) -> bytes:
    """A syntactically valid RIFF/WAVE file of silence.

    Built by hand rather than with a library so the upload tests have a real
    container to push through the magic-byte check without depending on
    soundfile or a checked-in binary fixture.
    """
    frames = int(seconds * sample_rate)
    bits = 16
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    data = b"\x00" * (frames * block_align)

    fmt_chunk = struct.pack(
        "<4sIHHIIHH", b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits)
    data_chunk = struct.pack("<4sI", b"data", len(data)) + data
    body = b"WAVE" + fmt_chunk + data_chunk
    return struct.pack("<4sI", b"RIFF", len(body)) + body


@pytest.fixture()
def wav_bytes() -> bytes:
    return make_wav()


def make_tone_wav(seconds: float = 30.0, sample_rate: int = 22050) -> bytes:
    """A valid WAV carrying an actual signal, not silence.

    Level 1 measures spectral structure, so silence is not a useful input: the
    fakeprint of digital silence is all zeros and the CQT is -inf before the
    epsilon. This builds a few detuned harmonics plus low-level noise, which
    gives both models something to read and exercises the real numeric path
    rather than a degenerate one.

    Deterministic: a fixed seed, because a test whose input changes per run
    cannot assert anything about the output.
    """
    import math
    import random

    rng = random.Random(1234)
    frames = int(seconds * sample_rate)
    samples = bytearray()
    for n in range(frames):
        t = n / sample_rate
        v = (0.30 * math.sin(2 * math.pi * 220.0 * t)
             + 0.18 * math.sin(2 * math.pi * 441.3 * t)
             + 0.10 * math.sin(2 * math.pi * 1319.7 * t)
             + 0.05 * math.sin(2 * math.pi * 3300.0 * t)
             + 0.01 * (rng.random() - 0.5))
        samples += struct.pack("<h", max(-32768, min(32767, int(v * 30000))))

    block_align = 2
    fmt_chunk = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate,
                            sample_rate * block_align, block_align, 16)
    data_chunk = struct.pack("<4sI", b"data", len(samples)) + bytes(samples)
    body = b"WAVE" + fmt_chunk + data_chunk
    return struct.pack("<4sI", b"RIFF", len(body)) + body


@pytest.fixture(scope="session")
def tone_wav_bytes() -> bytes:
    """Session-scoped: synthesising 30 s of audio in Python is not free."""
    return make_tone_wav()


@pytest.fixture()
def tone_file(tmp_path, tone_wav_bytes) -> str:
    path = tmp_path / "tone.wav"
    path.write_bytes(tone_wav_bytes)
    return str(path)


@pytest.fixture()
def audio_files():
    """Real audio, when a developer has dropped some in the fixtures dir."""
    files = sorted(AUDIO_DIR.glob("*.mp3")) + sorted(AUDIO_DIR.glob("*.wav"))
    if len(files) < 2:
        pytest.skip("needs two audio files in tests/fixtures/audio/")
    return files


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------
@pytest.fixture()
def client():
    """TestClient with the lifespan suppressed.

    The lifespan sweeps temp files, opens MongoDB and loads weights. None of
    that is wanted per-test, and entering it would make every test depend on
    the model being present.
    """
    from fastapi.testclient import TestClient

    from labs.application import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Drop cached storage/job singletons between tests.

    They memoise a Settings snapshot, so a test that monkeypatches the
    environment would otherwise leak its configuration into the next one.
    """
    yield
    from labs.core.security import reset_limiter
    from labs.services import jobs, storage
    storage.reset()
    jobs.reset()
    reset_limiter()


@pytest.fixture()
def mongo(monkeypatch):
    """A mongomock-backed MongoStore wired into the storage singleton."""
    mongomock = pytest.importorskip("mongomock")

    from labs.core.config import StorageConfig
    from labs.services import storage as storage_mod

    client = mongomock.MongoClient()
    cfg = StorageConfig()
    store = storage_mod.MongoStore(cfg)
    object.__setattr__(store.cfg, "mongo_uri", "mongodb://mongomock/labs")
    store._db = client["labs"]

    monkeypatch.setattr(storage_mod, "_mongo", store)
    return store


@pytest.fixture(scope="session")
def loaded_detector():
    """One Detector for the whole session, mirroring the server lifecycle."""
    if not _have_checkpoints():
        pytest.skip("checkpoints not present")
    from labs.core.config import get_settings
    from labs.ml.detector import Detector

    d = Detector(get_settings())
    d.load()
    return d
