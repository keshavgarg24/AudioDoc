"""The exit gate: is this verdict a property of the audio, or of these bytes?

WHY THIS EXISTS
---------------
Level 1 is allowed to end a request early when it is confident a track is AI
(see `policy.decide`). That shortcut removes 60-90 s of backbone CPU from the
requests that need it least, which is the entire economic case for the tier.

But "confident" measured on ONE rendering of one file is not the same claim as
"confident about this track". Both Level-1 models read narrowband spectral
structure, and that structure is exactly what a re-encode perturbs. The
measurement that forces the issue is in this repo's own audio/: ai1.mp3 scores
a saturated 1.0 here, and under the reference implementation's codec-level
augmentation its confidence fell to 0.6 - the opinion moved when the encoding
moved. A verdict like that must not be published as decisive, and must not be
allowed to skip the model that could actually settle it.

So: re-ask the same models the same question about perturbed renderings of the
same audio. If the answer holds, exit early. If it moves, escalate.

WHY IT IS CHEAP
---------------
It runs ONLY for tracks the policy already wants to exit on - a small fraction
of traffic, and the fraction that was about to skip the expensive tier anyway.

The perturbations are computed in the numpy domain on the signal that is
already decoded: no ffmpeg, no subprocess, no temp files. That is a deliberate
choice for an ungated public endpoint, where every subprocess spawned on
attacker-supplied input is attack surface. Codec-level augmentation - which
tests a strictly stronger property - belongs to the full tier, which is gated
and already shells out to ffmpeg.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

import numpy as np

from ..core.config import ScreenConfig
from . import ensemble as ens
from . import models

log = logging.getLogger(__name__)


@dataclass
class RobustnessResult:
    available: bool
    baseline: Optional[float] = None
    scores: Dict[str, float] = field(default_factory=dict)
    sigma: Optional[float] = None
    spread: Optional[float] = None
    flipped: int = 0
    stable: bool = True
    stability: float = 1.0
    most_fragile: Optional[str] = None
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _gain(y: np.ndarray, db: float) -> np.ndarray:
    return (y * (10.0 ** (db / 20.0))).astype(np.float32)


def _band_limit(y: np.ndarray, sr: int, cutoff_sr: int) -> np.ndarray:
    """Down- then up-sample, discarding everything above the lower Nyquist.

    This is the perturbation the fakeprint model should be most sensitive to,
    because it destroys the exact peak LOCATIONS that model reads while leaving
    the music perceptually intact. A verdict that survives it is one the model
    reached from structure rather than from one resampler's ringing.
    """
    import librosa

    down = librosa.resample(y, orig_sr=sr, target_sr=cutoff_sr, res_type="soxr_hq")
    return librosa.resample(down, orig_sr=cutoff_sr, target_sr=sr,
                            res_type="soxr_hq").astype(np.float32)


def _dither(y: np.ndarray, db: float, seed: int = 0) -> np.ndarray:
    """Additive noise at a fixed seed, so the check is reproducible.

    A random seed here would make the same file's verdict vary between
    identical requests, which is indefensible in a forensic report.
    """
    rng = np.random.default_rng(seed)
    amp = 10.0 ** (db / 20.0)
    return (y + rng.standard_normal(y.size).astype(np.float32) * amp).astype(np.float32)


def _transforms(y: np.ndarray, sr: int) -> Dict[str, np.ndarray]:
    return {
        "band_limit_12k": _band_limit(y, sr, 12000),
        "gain_-6db": _gain(y, -6.0),
        "dither_-45db": _dither(y, -45.0),
    }


# The check re-scores the signal once per perturbation, so it costs a multiple
# of the model pass. It is run on a centred excerpt with fewer CNN windows
# instead of the whole track: the question is whether the OPINION MOVES, which
# is a comparison between renderings of the same audio and does not need the
# same coverage as the verdict itself. Measured, this is the difference between
# ~7 s and ~1.5 s for the gate.
EXCERPT_SECONDS = 60.0
EXCERPT_CNN_WINDOWS = 6


def _excerpt(y: np.ndarray, sr: int) -> np.ndarray:
    """The middle EXCERPT_SECONDS of the signal, or all of it if shorter."""
    want = int(EXCERPT_SECONDS * sr)
    if y.size <= want:
        return y
    start = (y.size - want) // 2
    return y[start:start + want]


def check(y: np.ndarray, cfg: ScreenConfig) -> RobustnessResult:
    """Re-score perturbed renderings and report whether the verdict holds.

    Self-contained: it establishes its own baseline rather than accepting the
    caller's, because the caller's came from the full track at the full window
    count and is not comparable with the excerpt-based variants.
    """
    import dataclasses

    # Fewer windows for every score in this comparison INCLUDING the baseline
    # recomputed below, so all four numbers come from the same settings. Mixing
    # a 16-window baseline with 6-window variants would report the window count
    # as instability.
    cheap = dataclasses.replace(cfg, cnn_segments=EXCERPT_CNN_WINDOWS)
    clip = _excerpt(y, cfg.sample_rate)

    try:
        variants = _transforms(clip, cfg.sample_rate)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not build robustness variants", exc_info=True)
        return RobustnessResult(available=False,
                                note=f"perturbations unavailable: {exc}")

    def score(sig: np.ndarray) -> Optional[float]:
        fused = ens.combine(models.score_both(sig, cheap), cheap)
        return fused.probability if fused.available else None

    # The caller's baseline came from the full track at full window count, so
    # it is not comparable with the variants. Rescore the excerpt instead.
    baseline = score(clip)
    if baseline is None:
        return RobustnessResult(available=False,
                                note="baseline score unavailable")

    scores: Dict[str, float] = {}
    for name, sig in variants.items():
        try:
            p = score(sig)
        except Exception:  # noqa: BLE001
            log.warning("Robustness variant %s failed", name, exc_info=True)
            continue
        if p is not None:
            scores[name] = round(float(p), 4)

    if not scores:
        return RobustnessResult(available=False, baseline=round(baseline, 4),
                                note="every perturbation failed to score")

    vals = np.array([baseline] + list(scores.values()), dtype=np.float64)
    base_side = baseline > cfg.ai_threshold
    flipped = sum(1 for v in scores.values()
                  if (v > cfg.ai_threshold) != base_side)

    sigma = float(vals.std())
    spread = float(vals.max() - vals.min())
    # Thresholds mirror the reference implementation's: a sigma above 0.15, or
    # any flip at all, is disqualifying for an early exit.
    stable = bool(sigma <= 0.15 and flipped == 0)
    stability = float(np.clip(1.0 - sigma / 0.30, 0.0, 1.0)) * (
        1.0 - flipped / len(scores))
    fragile = max(scores, key=lambda k: abs(scores[k] - baseline))

    if stable:
        note = ("The verdict survives band limiting, level change and dither, "
                "so the evidence is in the audio rather than in this "
                "particular rendering of it.")
    elif flipped:
        note = (f"The verdict flips under {flipped} of {len(scores)} benign "
                f"perturbations (most fragile: {fragile}). Level 1 will not "
                f"decide this track.")
    else:
        note = (f"The score varies by {spread:.2f} across benign perturbations "
                f"(most fragile: {fragile}). The artifact is marginal.")

    return RobustnessResult(
        available=True, baseline=round(float(baseline), 4), scores=scores,
        sigma=round(sigma, 4), spread=round(spread, 4), flipped=flipped,
        stable=stable, stability=round(float(stability), 4),
        most_fragile=fragile, note=note)
