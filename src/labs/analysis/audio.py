"""Audio loading and beat-aligned segmentation.

Preserves the upstream pipeline: beat-track the file, keep downbeats whose
spacing agrees with the modal bar length, and cut a fixed 10 s window at each
one until 48 segments are collected.
"""
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torchaudio

from ..core.config import AudioConfig, CheckpointConfig, SegmentPlanConfig

log = logging.getLogger(__name__)


class AudioError(ValueError):
    """Raised for input the caller can fix (bad format, too short, too long)."""


def probe_duration(path: str) -> float:
    try:
        info = torchaudio.info(path)
        if info.sample_rate:
            return info.num_frames / float(info.sample_rate)
    except Exception:
        pass
    try:
        import soundfile as sf
        i = sf.info(path)
        return i.frames / float(i.samplerate)
    except Exception as exc:
        raise AudioError("Unsupported or unreadable audio file.") from exc


def validate(path: str, cfg: AudioConfig) -> float:
    suffix = os.path.splitext(path)[1].lower()
    if cfg.allowed_suffixes and suffix not in cfg.allowed_suffixes:
        raise AudioError(
            f"Unsupported file type '{suffix}'. Allowed: {', '.join(cfg.allowed_suffixes)}")

    size = os.path.getsize(path)
    if size == 0:
        raise AudioError("Uploaded file is empty.")
    if size > cfg.max_upload_bytes:
        raise AudioError(
            f"File too large ({size / 1e6:.1f} MB). "
            f"Limit is {cfg.max_upload_bytes / 1e6:.0f} MB.")

    duration = probe_duration(path)
    if duration < cfg.min_duration_s:
        raise AudioError(
            f"Audio is too short ({duration:.2f}s). "
            f"Minimum is {cfg.min_duration_s:.1f}s.")
    if duration > cfg.max_duration_s:
        raise AudioError(
            f"Audio is too long ({duration:.1f}s). "
            f"Maximum is {cfg.max_duration_s:.0f}s.")
    return duration


_RESAMPLERS: Dict[Tuple[int, int], "torchaudio.transforms.Resample"] = {}
_RESAMPLER_LOCK = threading.Lock()


def _resampler(orig_sr: int, target_sr: int):
    """Cached Resample transform.

    Building one computes a windowed-sinc kernel, and the pair of rates is
    fixed by the file's format and the model's input rate, so there are only
    ever a handful of distinct kernels across a deployment's whole lifetime.
    Rebuilding one per request paid for that work every time.
    """
    key = (int(orig_sr), int(target_sr))
    tf = _RESAMPLERS.get(key)
    if tf is None:
        with _RESAMPLER_LOCK:
            tf = _RESAMPLERS.get(key)
            if tf is None:
                tf = torchaudio.transforms.Resample(orig_sr, target_sr)
                _RESAMPLERS[key] = tf
    return tf


@dataclass
class SegmentedAudio:
    """Everything the segmentation step learned about a track."""
    segments: torch.Tensor          # [48, 1, fixed_samples]
    mask: torch.Tensor              # [48], True == padding
    n_real: int
    starts: List[float]             # seconds, one per real segment
    segment_seconds: float          # modal 4-bar length
    beats: List[float]              # every beat from the tracker
    downbeats_raw: List[float]
    downbeats_clean: List[float]
    duration: float
    # How the windows were chosen, so a verdict can be reproduced and so the
    # report can state what fraction of the track Stage-1 actually heard.
    plan: Dict = field(default_factory=dict)


def plan_starts(eligible: List[float], max_segments: int,
                spread: bool = True, stride: int = 1) -> Tuple[List[int], Dict]:
    """Choose which eligible downbeats become Stage-1 windows.

    UPSTREAM BEHAVIOUR, AND WHY IT IS CHANGED
    -----------------------------------------
    Upstream takes the FIRST `max_segments` eligible downbeats. beat_this emits
    downbeats every ~2.5 s and the windows are a fixed 10 s, so consecutive
    windows overlap ~75% and the 48 slots are spent long before the track ends.
    Measured across this repo's audio/ folder:

        mean redundancy  3.77x   (480 s of MERT input per ~127 s of unique audio)
        mean coverage    0.77
        at the 48 cap    7 of 10 tracks

    For 1.mp3 the 48th window ends at 144 s of a 186 s track, so Stage-1
    renders its verdict having never heard the last 42 s.

    With `spread`, the same 48 slots are distributed across the whole track by
    taking every Nth eligible downbeat. Cost is identical, the tensor Stage-2
    sees is the identical shape it was trained on, and coverage goes to ~1.0.
    That is why it is the default rather than an opt-in.

    `stride` multiplies the spacing further. It genuinely trades accuracy for
    speed - borderline tracks move - so it stays at 1 and the cascade is the
    supported way to buy the same speed safely.

    Returns (indices into `eligible`, a description of the choice).
    """
    n = len(eligible)
    if n == 0:
        return [], {"strategy": "none", "eligible": 0}

    stride = max(1, stride)
    budget = max(1, max_segments // stride)

    if not spread:
        idx = list(range(0, n, stride))[:budget]
        strategy = "upstream-prefix"
    elif n <= budget:
        idx = list(range(n))
        strategy = "spread"
    else:
        # Evenly spaced indices across the WHOLE grid, endpoints included.
        #
        # NOT a constant integer step. `range(0, n, ceil(n / budget))` looks
        # equivalent and is not: for n=54 and budget=48 the step rounds up to 2
        # and yields 27 windows, silently spending half the budget. That cost
        # real accuracy - ai1.mp3 sits at |logit| 0.39, inside the band the
        # cascade measurements show is sensitive to segment count, and running
        # it on 27 windows instead of 48 flipped its verdict.
        #
        # Interpolating the indices instead fills the budget exactly and still
        # reaches the final downbeat.
        idx = sorted({int(round(i * (n - 1) / (budget - 1)))
                      for i in range(budget)}) if budget > 1 else [n // 2]
        strategy = "spread"

    return idx, {
        "strategy": strategy,
        "eligible_downbeats": n,
        "selected": len(idx),
        "budget": budget,
        "extra_stride": stride,
    }


def load_segments(
    path: str,
    audio_cfg: AudioConfig,
    ckpt_cfg: CheckpointConfig,
    beat_device: str = "cpu",
    plan_cfg: Optional[SegmentPlanConfig] = None,
) -> SegmentedAudio:
    """Beat-track, then cut fixed windows at selected downbeats.

    `plan_cfg` controls WHICH downbeats are selected; see `plan_starts`. None
    means the default spread, which is what every caller in the service uses.
    """
    from ..audio.segmentation import find_optimal_segment_length, get_segments_from_wav

    sr = audio_cfg.sample_rate
    fixed = audio_cfg.fixed_samples

    beats, downbeats = get_segments_from_wav(
        path, device=beat_device, checkpoint_path=ckpt_cfg.beat_checkpoint)
    segment_seconds, cleaned = find_optimal_segment_length(downbeats)

    try:
        waveform, sample_rate = torchaudio.load(path)
        waveform = waveform.to(torch.float32)
    except ImportError:
        import soundfile as sf
        _data, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        waveform = torch.from_numpy(_data.T).contiguous()
    duration = waveform.shape[1] / float(sample_rate or sr)
    if sample_rate != sr:
        waveform = _resampler(sample_rate, sr)(waveform)
    if waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)
    if waveform.shape[1] <= fixed:
        waveform = torch.cat(
            [waveform, torch.zeros(1, fixed, dtype=torch.float32)], dim=1)

    # Eligibility first, selection second. A downbeat whose 10 s window would
    # run off the end of the file cannot be used at all, so it must not consume
    # a slot in the plan - filtering after selection is what would leave the
    # cap partly unfilled on a track that had plenty of usable downbeats.
    eligible = [float(t) for t in cleaned
                if int(t * sr) + fixed <= waveform.size(1)]
    chosen, plan = plan_starts(
        eligible, audio_cfg.max_segments,
        spread=plan_cfg.spread if plan_cfg else True,
        stride=plan_cfg.stride if plan_cfg else 1)

    segments, starts = [], []
    for i in chosen:
        start_time = eligible[i]
        start = int(start_time * sr)
        seg = waveform[:, start:start + fixed].reshape(1, -1).contiguous()
        if audio_cfg.normalize_waveform and seg.var() > 0:
            seg = (seg - seg.mean()) / torch.sqrt(seg.var() + 1e-7)
        segments.append(seg)
        starts.append(start_time)

    if starts:
        window_s = fixed / float(sr)
        span = (starts[-1] + window_s) - starts[0]
        plan["coverage"] = round(min(span / max(duration, 1e-9), 1.0), 3)
        plan["unique_seconds"] = round(min(span, duration), 1)
        plan["mert_seconds"] = round(len(starts) * window_s, 1)
        plan["redundancy"] = round(
            plan["mert_seconds"] / max(plan["unique_seconds"], 1e-9), 2)

    if not segments:
        raise AudioError(
            "No beat-aligned segments could be extracted. The clip may be too "
            "short or lack a detectable beat.")

    stacked = torch.stack(segments)
    n_real = stacked.shape[0]
    mask = torch.zeros(audio_cfg.max_segments, dtype=torch.bool)
    if n_real < audio_cfg.max_segments:
        pad = torch.zeros(
            (audio_cfg.max_segments - n_real, 1, fixed), dtype=torch.float32)
        stacked = torch.cat([stacked, pad], dim=0)
        mask[n_real:] = True

    log.info("Extracted %d real segments from %s", n_real, os.path.basename(path))
    return SegmentedAudio(
        segments=stacked, mask=mask, n_real=n_real, starts=starts,
        segment_seconds=float(segment_seconds),
        beats=[float(v) for v in np.asarray(beats).ravel()],
        downbeats_raw=[float(v) for v in np.asarray(downbeats).ravel()],
        downbeats_clean=[float(v) for v in np.asarray(cleaned).ravel()],
        duration=duration,
        plan=plan,
    )
