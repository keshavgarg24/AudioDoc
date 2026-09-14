"""ONNX Runtime sessions for the two Level-1 detectors.

Sessions are process-wide singletons built once under a lock, then used
concurrently WITHOUT one. `InferenceSession.run` is thread-safe by design in
ONNX Runtime, so serialising it - as the reference implementation did with a
global mutex around every call - throws away the whole reason this tier is
cheap: it turns a 1 s model into a queue.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.config import ScreenConfig
from . import features

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SESSIONS: Dict[str, Any] = {}


class ModelUnavailable(RuntimeError):
    """The weights are missing or unloadable. Never a request-level failure."""


@dataclass
class ModelScore:
    """One detector's opinion, plus enough context to interpret it."""
    available: bool
    name: str = ""
    probability: Optional[float] = None
    windows: List[dict] = field(default_factory=list)
    reads: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Weights that ship with the package. 1.2 MB total, so they are package data
# rather than something to mirror: a tier that has to fetch anything before it
# can answer is not a tier you can give away.
PACKAGE_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "weights")


def model_path(cfg: ScreenConfig, filename: str) -> str:
    """Resolve a model file: absolute path, then override dir, then package.

    The package directory is checked LAST but is the normal answer, because
    `LABS_SCREEN_MODELS_DIR` is empty by default. An operator pointing it
    somewhere else - to test a re-exported graph, say - wins over the shipped
    weights without having to replace them in site-packages.
    """
    if os.path.isabs(filename):
        return filename

    candidates = []
    if cfg.models_dir:
        candidates.append(os.path.join(cfg.models_dir, filename))
    candidates.append(os.path.join(PACKAGE_WEIGHTS, filename))

    for cand in candidates:
        if os.path.exists(cand):
            return cand
    raise ModelUnavailable(
        f"{filename} not found. Looked in: {', '.join(candidates)}")


def _session(cfg: ScreenConfig, filename: str):
    """Double-checked singleton per model file."""
    sess = _SESSIONS.get(filename)
    if sess is not None:
        return sess
    with _LOCK:
        sess = _SESSIONS.get(filename)
        if sess is not None:
            return sess
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelUnavailable("onnxruntime is not installed") from exc

        path = model_path(cfg, filename)
        opts = ort.SessionOptions()
        if cfg.onnx_threads > 0:
            opts.intra_op_num_threads = cfg.onnx_threads
            opts.inter_op_num_threads = 1
        # Errors only: ORT logs a warning per unused initialiser at startup,
        # which is noise for a model we did not export.
        opts.log_severity_level = 3
        try:
            sess = ort.InferenceSession(
                path, sess_options=opts, providers=["CPUExecutionProvider"])
        except Exception as exc:
            raise ModelUnavailable(f"could not load {path}: {exc}") from exc
        _SESSIONS[filename] = sess
        log.info("Level-1 model ready: %s", path)
        return sess


def reset() -> None:
    """Test hook: drop cached sessions so a new config takes effect."""
    with _LOCK:
        _SESSIONS.clear()


def warm(cfg: ScreenConfig) -> Dict[str, bool]:
    """Build both sessions up front. Reported by /v1/health.

    Worth doing at startup rather than on the first request: session creation
    parses and plans the graph, which is most of the CNN's fixed cost.
    """
    out = {}
    for key, filename in (("fakeprint", cfg.fakeprint_onnx),
                          ("cepstrum", cfg.cepstrum_onnx)):
        if key == "cepstrum" and not cfg.use_cnn:
            continue
        try:
            _session(cfg, filename)
            out[key] = True
        except ModelUnavailable as exc:
            log.warning("Level-1 model %s unavailable: %s", key, exc)
            out[key] = False
    return out


# ------------------------------------------------------------- detectors --
def score_fakeprint(y: np.ndarray, cfg: ScreenConfig) -> ModelScore:
    """Averaged linear spectrum, 1-8 kHz -> exact peak locations."""
    reads = "averaged linear spectrum 1-8 kHz; exact peak locations"
    try:
        sess = _session(cfg, cfg.fakeprint_onnx)
    except ModelUnavailable as exc:
        return ModelScore(available=False, name="fakeprint", reads=reads,
                          error=str(exc))
    try:
        vec = features.fit_features(features.fakeprint(y, cfg), cfg.n_features)
        name = sess.get_inputs()[0].name
        out = sess.run(None, {name: vec.reshape(1, -1)})[0]
        # This graph ends in a sigmoid, so the output is already a probability.
        prob = float(np.asarray(out).ravel()[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("fakeprint scoring failed", exc_info=True)
        return ModelScore(available=False, name="fakeprint", reads=reads,
                          error=str(exc)[:300])

    return ModelScore(available=True, name="fakeprint",
                      probability=round(prob, 6), reads=reads,
                      detail={"n_features": int(cfg.n_features)})


def score_cepstrum(y: np.ndarray, cfg: ScreenConfig,
                   n_windows: Optional[int] = None) -> ModelScore:
    """CQT cepstrum on a log-frequency axis -> learned texture.

    Pooling is a MEDIAN across windows, not a max and not a mean. A max makes
    the file's verdict the single most extreme window, which is an OR gate over
    however many windows were requested; a mean lets one pathological window
    drag the whole track. The median asks whether the artifact is a property of
    the TRACK, which is the actual question.
    """
    reads = "CQT / cepstrum on a log-frequency axis; learned texture"
    try:
        sess = _session(cfg, cfg.cepstrum_onnx)
    except ModelUnavailable as exc:
        return ModelScore(available=False, name="cepstrum", reads=reads,
                          error=str(exc))

    use_cfg = cfg
    if n_windows is not None and n_windows != cfg.cnn_segments:
        import dataclasses

        use_cfg = dataclasses.replace(cfg, cnn_segments=n_windows)

    try:
        batch = features.cepstrum_batch(y, use_cfg)
        name = sess.get_inputs()[0].name
        logits = np.asarray(sess.run(None, {name: batch})[0]).ravel()
        probs = 1.0 / (1.0 + np.exp(-logits))
    except Exception as exc:  # noqa: BLE001
        log.warning("cepstrum scoring failed", exc_info=True)
        return ModelScore(available=False, name="cepstrum", reads=reads,
                          error=str(exc)[:300])

    starts = features.plan_windows(y.size, use_cfg)
    win = use_cfg.segment_seconds
    per_window = [
        {"index": i,
         "start_s": round(starts[i] / float(use_cfg.sample_rate), 2)
         if i < len(starts) else None,
         "end_s": round(starts[i] / float(use_cfg.sample_rate) + win, 2)
         if i < len(starts) else None,
         "probability": round(float(p), 4)}
        for i, p in enumerate(probs)
    ]

    return ModelScore(
        available=True, name="cepstrum",
        probability=round(float(np.median(probs)), 6), windows=per_window,
        reads=reads,
        detail={"n_windows": int(probs.size), "pooling": "median",
                "max": round(float(probs.max()), 4),
                "min": round(float(probs.min()), 4),
                "mean": round(float(probs.mean()), 4)})


def score_both(y: np.ndarray, cfg: ScreenConfig) -> Dict[str, ModelScore]:
    """Both detectors over the same decoded signal."""
    out = {"fakeprint": score_fakeprint(y, cfg)}
    if cfg.use_cnn:
        out["cepstrum"] = score_cepstrum(y, cfg)
    return out
