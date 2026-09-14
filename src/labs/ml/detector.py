"""Detector service: owns the loaded models and turns a file path into a verdict.

Models are constructed once (see `load()`); `predict()` is re-entrant against
that same instance and never touches the network or the filesystem cache.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import torch

from ..analysis.audio import load_segments, validate
from ..analysis.features import extract as extract_features
from ..analysis.musical import analyse as analyse_musical
from ..analysis.production import analyse as analyse_production
from ..analysis.report import build_report
from ..core.config import Settings, resolve_device
from ..ml.checkpoints import resolve_all
from ..verdicts import VERDICT_AI, VERDICT_HUMAN, VERDICT_INCONCLUSIVE

log = logging.getLogger(__name__)


def scaled_sigmoid(x, scale_factor: float = 0.2, linear_property: float = 0.3):
    """Upstream probability mapping, preserved verbatim."""
    scaled_x = x * scale_factor
    raw_prob = torch.sigmoid(scaled_x) * (1 - linear_property) \
        + linear_property * ((x + 25) / 50)
    return torch.clamp(raw_prob, min=0.011, max=0.989)


def _band(raw_logit: float, cfg) -> Dict:
    """Turn an unbounded logit into a verdict that admits uncertainty.

    See DeepPolicyConfig for why this is not just `raw_logit > 0`. In short:
    ai1.mp3 sits at |logit| 0.4 and lands on either side of zero depending on
    which windows Stage-1 was handed, so publishing the sign as a verdict makes
    a coin flip look like a finding.

    The vocabulary matches Level 1 deliberately, so one frontend code path can
    render either tier.
    """
    if not cfg.enabled:
        return {"verdict": VERDICT_AI if raw_logit > 0 else VERDICT_HUMAN,
                "decisive": True,
                "band_note": "Verdict banding is disabled; the sign of the "
                             "logit is reported directly."}

    if abs(raw_logit) < cfg.inconclusive_below:
        return {
            "verdict": VERDICT_INCONCLUSIVE,
            "decisive": False,
            "band_note": (
                f"|logit| {abs(raw_logit):.2f} is below "
                f"{cfg.inconclusive_below}, where this model's output is not "
                f"separable from the decision boundary: tracks in this band "
                f"move to the other side of zero on a different but equally "
                f"valid choice of analysis windows. The sign is reported in "
                f"`prediction` for continuity, but it is not a verdict."),
        }

    return {
        "verdict": VERDICT_AI if raw_logit > 0 else VERDICT_HUMAN,
        "decisive": True,
        "band_note": (f"|logit| {abs(raw_logit):.2f} is clear of the "
                      f"{cfg.inconclusive_below} inconclusive band."),
    }


def _pad_sequence(embedding: torch.Tensor, length: int) -> torch.Tensor:
    """Zero-pad [S, D] up to [length, D] for Stage-2's fixed-width input.

    The padded rows are masked out downstream, so their value is arbitrary;
    zeros are the cheapest thing to put there.
    """
    n = embedding.shape[0]
    if n >= length:
        return embedding
    pad = torch.zeros(length - n, embedding.shape[1],
                      dtype=embedding.dtype, device=embedding.device)
    return torch.cat([embedding, pad], dim=0)


@dataclass
class DetectorInfo:
    device: str
    stage1_path: str
    stage2_path: str
    loaded_at: float
    load_seconds: float
    # Provenance, so a verdict can be traced back to the exact weights.
    revision: Optional[str] = None
    stage1_source: Optional[str] = None
    stage2_source: Optional[str] = None
    mirror: Optional[Dict] = None


class Detector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._backbone = None
        self._head = None
        self._info: Optional[DetectorInfo] = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        # Resolved eagerly: mode="audio" runs without ever calling load(), and
        # still needs a device for the beat tracker.
        self._device = resolve_device(settings.model.device)

    # -- lifecycle ---------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        return self._backbone is not None and self._head is not None

    @property
    def info(self) -> Optional[DetectorInfo]:
        return self._info

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def load(self) -> DetectorInfo:
        """Idempotent. Safe to call from startup or from a readiness probe."""
        with self._lock:
            if self.is_ready:
                return self._info
            started = time.time()
            try:
                if self.settings.model.torch_threads > 0:
                    torch.set_num_threads(self.settings.model.torch_threads)
                    # interop threads control parallelism between independent ops;
                    # must be set before any parallel work starts.
                    try:
                        torch.set_num_interop_threads(self.settings.model.torch_threads)
                    except RuntimeError:
                        pass  # already set by a previous call; harmless

                paths = resolve_all(self.settings.checkpoints)
                device = resolve_device(self.settings.model.device)
                log.info("Loading detection models on %s (%d torch threads)",
                         device, torch.get_num_threads())

                from .architecture import MERT_AudioCAT, MusicAudioClassifier

                backbone = MERT_AudioCAT.load_from_checkpoint(
                    paths["stage1"], map_location=device)
                backbone.eval().to(device).float()

                head = MusicAudioClassifier.load_from_checkpoint(
                    checkpoint_path=paths["stage2"],
                    input_dim=self.settings.model.input_dim,
                    backbone="fusion_segment_transformer",
                    is_emb=True,
                    map_location=device,
                )
                head.eval().to(device).float()

                # NOTE: dynamic INT8 quantization was evaluated here but breaks
                # nn.TransformerEncoder's fast-path device check (both stages
                # use it), causing an AttributeError on every real inference
                # call. Warm-up used dummy zero tensors and didn't reliably hit
                # this path, so it passed while real requests failed. Removed.

                self._backbone, self._head = backbone, head
                self._device = device

                # Warm up BOTH models at startup so the first real request
                # does not pay for lazy kernel initialisation or quantisation
                # overhead. Stage-1 gets a realistic single-segment input;
                # Stage-2 gets the full padded sequence shape.
                try:
                    with torch.inference_mode():
                        dummy_seg = torch.zeros(1, 240000, device=device)
                        _, dummy_emb = backbone(dummy_seg)
                        # Build a padded sequence of the configured max length
                        max_s = self.settings.audio.max_segments
                        seq = dummy_emb.unsqueeze(0).expand(1, max_s, -1)
                        mask = torch.zeros(1, max_s, dtype=torch.bool, device=device)
                        head(seq, mask)
                    log.info("Warm-up pass complete")
                except Exception:
                    log.warning("Warm-up pass failed", exc_info=True)

                self._info = DetectorInfo(
                    device=device,
                    stage1_path=paths["stage1"],
                    stage2_path=paths["stage2"],
                    loaded_at=started,
                    load_seconds=round(time.time() - started, 2),
                    revision=paths.get("revision"),
                    stage1_source=paths.get("stage1_source"),
                    stage2_source=paths.get("stage2_source"),
                    mirror=paths.get("mirror"),
                )
                self._load_error = None
                log.info("Models ready in %.1fs", self._info.load_seconds)
                return self._info
            except Exception as exc:
                self._load_error = str(exc)
                log.exception("Model load failed")
                raise

    # -- inference ---------------------------------------------------------
    def _autocast(self):
        """Autocast context for the configured dtype, or a no-op.

        Off unless LABS_AUTOCAST_DTYPE is set. See ModelConfig.autocast_dtype:
        enabling this changes verdicts and has to be validated against
        labelled audio first.
        """
        import contextlib

        name = self.settings.model.autocast_dtype
        if not name:
            return contextlib.nullcontext()
        dtype = {"bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
                 "float16": torch.float16, "fp16": torch.float16}.get(name)
        if dtype is None:
            log.warning("Unknown LABS_AUTOCAST_DTYPE %r; running in float32", name)
            return contextlib.nullcontext()
        return torch.autocast(device_type=self._device, dtype=dtype)

    def _batch_size(self, n_segments: int) -> int:
        """Segments per backbone forward, honouring an explicit override.

        See ModelConfig.backbone_batch_size for the measurements behind the
        CPU/GPU split. 0 (the default) means resolve it here.
        """
        configured = self.settings.model.backbone_batch_size
        if configured > 0:
            return configured
        return 4 if self._device == "cpu" else max(1, n_segments)

    def embed(self, segments: torch.Tensor):
        """Stage-1: waveform segments -> (embeddings[S,768], logits[S,2]).

        Stage-1 carries its own 2-class head. Keeping its output gives an
        independent per-window opinion for the report.
        """
        bs = self._batch_size(segments.shape[0])
        flat = segments.squeeze(1).to(self._device).float()
        embs, logits = [], []
        with torch.inference_mode(), self._autocast():
            for i in range(0, flat.size(0), bs):
                logit, emb = self._backbone(flat[i:i + bs])
                embs.append(emb)
                logits.append(logit)
        # Cast back so everything downstream - the calibration, the report, the
        # serialiser - sees float32 regardless of what ran inside autocast.
        return (torch.cat(embs, dim=0).float(),
                torch.cat(logits, dim=0).float())

    def embed_indices(self, segments: torch.Tensor, indices: List[int],
                      cache: Dict[int, tuple]) -> None:
        """Stage-1 over `indices` only, filling `cache` in place.

        The cache is what makes the cascade free. Stage-1 embeddings are
        per-segment and INDEPENDENT - nothing couples them until Stage-2's
        self-similarity matrix - so a second pass can compute only the windows
        the first pass skipped and reuse the rest verbatim. Escalating costs
        1.0x of a full pass, not 1.33x, and the numbers are bit-identical to
        having run at full density from the start.
        """
        todo = [i for i in indices if i not in cache]
        if not todo:
            return
        batch = segments[torch.tensor(todo, dtype=torch.long)]
        embs, logits = self.embed(batch)
        for slot, idx in enumerate(todo):
            cache[idx] = (embs[slot], logits[slot])

    def classify_subset(self, cache: Dict[int, tuple], indices: List[int],
                        max_segments: int) -> torch.Tensor:
        """Stage-2 over a chosen subset of cached Stage-1 embeddings.

        Indices are sorted before stacking. Stage-2's self-similarity matrix
        and its SSM are order-sensitive, so a subset handed over out of
        chronological order describes a different song.
        """
        order = sorted(indices)
        seq = torch.stack([cache[i][0] for i in order])
        mask = torch.zeros(max_segments, dtype=torch.bool)
        if len(order) < max_segments:
            mask[len(order):] = True
        return self.classify(_pad_sequence(seq, max_segments), mask)

    def classify(self, embedding: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """Stage-2: embeddings + padding mask -> scalar logit."""
        if embedding.dim() == 2:
            embedding = embedding.unsqueeze(0)
        if mask.dim() == 1:
            mask = mask.unsqueeze(0)
        with torch.inference_mode(), self._autocast():
            out = self._head(
                embedding.to(self._device), mask.to(self._device)).squeeze()
        return out.float()

    def stage1(self, audio, cfg: Settings):
        """Run Stage-1 + Stage-2, cascading when that is safe.

        Returns (logit, embedding[k,768], stage1_logits[k,2], used_indices, info).

        WHY A CASCADE AND NOT JUST FEWER SEGMENTS
        -----------------------------------------
        Measured on the real checkpoints over 10 tracks at strides 1-4, the
        sensitivity to segment count is purely a function of confidence:

            |logit| >= 5   immune. 1.mp3 moves 0.004 between stride 1 and 4.
            |logit| <  2   unstable. ai1 FLIPS Fake->Real at stride 2; ai4
                           collapses +4.574 -> +0.937.

        So blanket decimation is not a free speedup - it is an accuracy trade
        that happens to be invisible on the easy majority. A cascade buys the
        same time from only the tracks that provably do not need it, and
        escalates the rest to full density. Verdicts are unchanged.
        """
        n = audio.n_real
        casc = cfg.cascade
        full = list(range(n))
        cache: Dict[int, tuple] = {}

        stride = max(1, casc.first_pass_stride)
        eligible = casc.enabled and n >= casc.min_segments and stride > 1

        if not eligible:
            self.embed_indices(audio.segments, full, cache)
            logit = self.classify_subset(cache, full, cfg.audio.max_segments)
            info = {"enabled": bool(casc.enabled), "escalated": False,
                    "passes": 1, "segments_scored": n, "segments_available": n,
                    "note": ("Cascade skipped: too few segments to save "
                             "anything." if casc.enabled else
                             "Cascade disabled; full density.")}
            return logit, cache, full, info

        first = full[::stride]
        self.embed_indices(audio.segments, first, cache)
        logit = self.classify_subset(cache, first, cfg.audio.max_segments)

        first_logit = float(logit.item())
        if abs(first_logit) >= casc.escalate_below:
            info = {
                "enabled": True, "escalated": False, "passes": 1,
                "first_pass_stride": stride,
                "segments_scored": len(first), "segments_available": n,
                "first_pass_logit": round(first_logit, 6),
                "threshold": casc.escalate_below,
                "note": (f"|logit| {abs(first_logit):.2f} is at or above "
                         f"{casc.escalate_below}, which measures as immune to "
                         f"segment count, so the remaining "
                         f"{n - len(first)} windows were not computed."),
            }
            return logit, cache, first, info

        # Borderline. Compute only what the first pass skipped and re-score at
        # full density; the first pass's embeddings are reused, not recomputed.
        self.embed_indices(audio.segments, full, cache)
        logit = self.classify_subset(cache, full, cfg.audio.max_segments)
        info = {
            "enabled": True, "escalated": True, "passes": 2,
            "first_pass_stride": stride,
            "segments_scored": n, "segments_available": n,
            "first_pass_logit": round(first_logit, 6),
            "final_logit": round(float(logit.item()), 6),
            "threshold": casc.escalate_below,
            "note": (f"|logit| {abs(first_logit):.2f} was below "
                     f"{casc.escalate_below}, where the verdict is sensitive "
                     f"to segment count, so all {n} windows were scored. The "
                     f"first pass's embeddings were reused, so this cost the "
                     f"same as a single full-density pass."),
        }
        return logit, cache, full, info

    def predict(self, path: str, mode: str = "ai",
                display_name: Optional[str] = None,
                target_genre: Optional[str] = None) -> Dict:
        """Analyse a track.

        mode:
          "ai"    detection verdict plus its supporting model evidence
          "audio" production and musical analysis only, no detection
          "full"  both

        `display_name` lets the API report the caller's original filename
        rather than the temp file it streamed the upload into.
        """
        if mode not in ("ai", "audio", "full"):
            raise ValueError(f"Unknown analysis mode: {mode}")
        if mode in ("ai", "full") and not self.is_ready:
            raise RuntimeError("Detector is not loaded.")

        cfg = self.settings
        started = time.time()
        validate(path, cfg.audio)

        want_ai = mode in ("ai", "full")
        want_audio = mode in ("audio", "full")

        # Segmentation is needed for the model, and its beat grid is reused by
        # the musical pass, so run it whenever either side needs it.
        audio = load_segments(
            path, cfg.audio, cfg.checkpoints,
            beat_device="cpu" if self._device == "mps" else self._device,
            plan_cfg=cfg.segments)

        verdict, stage1_logits, embedding = None, None, None
        used: Optional[List[int]] = None
        if want_ai:
            # Only the real segments go through the backbone.
            #
            # `audio.segments` is always padded out to max_segments with zeros
            # so Stage-2 sees a fixed-width sequence, but Stage-2 masks every
            # padded position out of the self-similarity matrix, out of the
            # attention (src_key_padding_mask) and out of the pooler, so the
            # backbone's output for those rows cannot reach the verdict. It was
            # being computed and then discarded.
            #
            # Verified against the real Stage-2 checkpoint: substituting zeros,
            # a different random draw, or garbage scaled by 1000 into the padded
            # rows moves the logit by 0.0e+00.
            #
            # That guarantee is what the cascade builds on: if a masked row
            # cannot reach the verdict, then a run with fewer real rows is a
            # legitimate verdict rather than a truncated one, and `stage1` is
            # free to score a subset and pad the rest. See `stage1` for which
            # subsets are safe and why.
            logit, cache, used, cascade_info = self.stage1(audio, cfg)
            embedding = torch.stack([cache[i][0] for i in used])
            stage1_logits = torch.stack([cache[i][1] for i in used])
            prob = scaled_sigmoid(
                logit,
                scale_factor=cfg.model.sigmoid_scale_factor,
                linear_property=cfg.model.sigmoid_linear_property,
            ).item()
            raw = float(logit.item())
            verdict = {
                # Preserved verbatim for existing callers: a hard side of zero.
                "prediction": "Fake" if prob > 0.5 else "Real",
                "confidence": round(max(prob, 1 - prob) * 100, 2),
                "fake_probability": round(prob, 4),
                "real_probability": round(1 - prob, 4),
                "raw_logit": round(raw, 6),
                "segments_used": len(used),
                "segment_plan": audio.plan,
                "cascade": cascade_info,
            }
            verdict.update(_band(raw, cfg.deep_policy))

        features, musical_data, production_data = {}, {}, {}
        cache = None
        if want_audio:
            # One decode and one harmonic/percussive split for all three
            # passes; they used to do four decodes and two separations of the
            # same file between them. Released before the report is built so
            # the buffers do not outlive the work that needed them.
            from ..analysis.decode import AudioCache

            cache = AudioCache(path)
            try:
                features = extract_features(path, cache=cache)
                musical_data = analyse_musical(
                    path, tracker_beats=audio.beats, cache=cache)
                production_data = analyse_production(path, cache=cache)
            finally:
                cache.release()

        # `embedding` and `stage1_logits` already cover exactly the windows the
        # cascade scored, so the starts must be narrowed to match. Passing the
        # full list would misalign every per-window row in the report against
        # its timestamp on an early-exit run.
        starts = ([audio.starts[i] for i in used] if used is not None
                  else audio.starts)
        return build_report(
            mode=mode,
            verdict=verdict,
            stage1_logits=stage1_logits,
            embeddings=embedding,
            segment_starts=starts,
            segment_seconds=audio.segment_seconds,
            downbeats_raw=audio.downbeats_raw,
            downbeats_clean=audio.downbeats_clean,
            filename=display_name or os.path.basename(path),
            duration=audio.duration,
            device=self._device,
            elapsed=time.time() - started,
            fake_index=cfg.model.stage1_fake_index,
            source_path=path,
            features=features,
            musical_data=musical_data,
            production_data=production_data,
            target_genre=target_genre,
        )


_detector: Optional[Detector] = None
_detector_lock = threading.Lock()


def get_detector(settings: Optional[Settings] = None) -> Detector:
    """Process-wide singleton so weights are held once per worker."""
    global _detector
    with _detector_lock:
        if _detector is None:
            from ..core.config import get_settings
            _detector = Detector(settings or get_settings())
        return _detector


def reset_detector() -> None:
    """Test hook."""
    global _detector
    with _detector_lock:
        _detector = None
