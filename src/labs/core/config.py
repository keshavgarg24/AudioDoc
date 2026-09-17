"""Centralized configuration.

Everything tunable lives here and is overridable by environment variable, so
the checkpoint source can change without touching inference code.
"""
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_list(key: str, default: str) -> Tuple[str, ...]:
    raw = os.environ.get(key, default)
    return tuple(p.strip() for p in raw.split(",") if p.strip())


@dataclass(frozen=True)
class CheckpointConfig:
    """Where Stage-1 / Stage-2 weights come from.

    Resolution order per stage:
      1. explicit *_path if it exists
      2. <dir>/<filename> if it exists
      3. the S3 mirror at `models_s3_uri`
      4. Hugging Face, pinned to `revision`

    `models_root` is the parent of every model cache (checkpoints, the HF cache
    for MERT, the torch.hub cache for the beat tracker). Mirroring that whole
    tree from S3 in one pass is what makes a cold start fast.
    """
    dir: str = field(default_factory=lambda: _env("LABS_CKPT_DIR", "checkpoints"))
    models_root: str = field(default_factory=lambda: _env("LABS_MODELS_DIR", "/models"))
    models_s3_uri: Optional[str] = field(
        default_factory=lambda: os.environ.get("LABS_MODELS_S3_URI"))
    s3_region: Optional[str] = field(
        default_factory=lambda: (os.environ.get("LABS_S3_REGION")
                                 or os.environ.get("AWS_REGION")))
    repo_id: str = field(default_factory=lambda: _env(
        "LABS_CKPT_REPO", "teamup-tech/FST-AI-Music-Detection-checkpoints"))
    repo_type: str = field(default_factory=lambda: _env("LABS_CKPT_REPO_TYPE", "model"))
    # Pin to a commit SHA so a change upstream cannot silently swap the model
    # under a running deployment. None means "whatever main points at today".
    revision: Optional[str] = field(
        default_factory=lambda: os.environ.get("LABS_CKPT_REVISION") or None)
    stage1_filename: str = field(default_factory=lambda: _env(
        "LABS_STAGE1_FILENAME", "Stage-1.ckpt"))
    stage2_filename: str = field(default_factory=lambda: _env(
        "LABS_STAGE2_FILENAME", "Stage-2.ckpt"))
    stage1_path: Optional[str] = field(default_factory=lambda: os.environ.get("LABS_STAGE1_PATH"))
    stage2_path: Optional[str] = field(default_factory=lambda: os.environ.get("LABS_STAGE2_PATH"))
    token: Optional[str] = field(default_factory=lambda: (
        os.environ.get("LABS_HF_TOKEN") or os.environ.get("HF_TOKEN")))
    # Refuse to reach Hugging Face at all. Set this in production once the
    # weights are mirrored, so a missing file fails loudly instead of quietly
    # pulling 1.7 GB over the public internet during a deploy.
    offline: bool = field(default_factory=lambda: _env_bool("LABS_OFFLINE", False))
    # beat_this tracker: "final0" resolves via its own bundled downloader.
    beat_checkpoint: str = field(default_factory=lambda: _env("LABS_BEAT_CKPT", "final0"))

    # Expected SHA-256 of each checkpoint. Unset means "do not check".
    #
    # WHY THIS IS NOT THE SAME AS PINNING A REVISION. `revision` pins what we
    # ASK FOR; these pin what we GOT. They cover different failures: a revision
    # cannot detect a corrupted download, a truncated S3 copy, a stale file
    # left on a reused volume, or a mirror bucket somebody else can write to.
    #
    # A verdict is only reproducible if the weights behind it are identical,
    # and a silently different checkpoint produces confident wrong answers
    # rather than an error - which is the failure mode worth paying to detect.
    #
    # Get them with:  shasum -a 256 checkpoints/Stage-*.ckpt
    stage1_sha256: Optional[str] = field(
        default_factory=lambda: (os.environ.get("LABS_STAGE1_SHA256") or "").strip().lower() or None)
    stage2_sha256: Optional[str] = field(
        default_factory=lambda: (os.environ.get("LABS_STAGE2_SHA256") or "").strip().lower() or None)
    # Refuse to start on a mismatch, rather than logging and carrying on.
    # On by default: a checkpoint that is not the one you pinned is not a
    # degraded service, it is a different model answering in your name.
    enforce_digest: bool = field(default_factory=lambda: _env_bool(
        "LABS_ENFORCE_CKPT_DIGEST", True))


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 24000
    # 240000 samples @ 24 kHz = 10 s per segment.
    fixed_samples: int = 240000
    # 48 is not an arbitrary ceiling: Stage-2 was trained on sequences padded or
    # cropped to exactly this length, so raising it feeds the SSM a sequence
    # longer than anything it saw in training. Coverage of long tracks is fixed
    # by SPREADING 48 windows across the whole track (see
    # `SegmentPlanConfig.spread`), not by asking for more of them.
    max_segments: int = field(default_factory=lambda: _env_int("LABS_MAX_SEGMENTS", 48))
    min_duration_s: float = field(default_factory=lambda: float(
        _env("LABS_MIN_DURATION_S", "2.0")))
    max_duration_s: float = field(default_factory=lambda: float(
        _env("LABS_MAX_DURATION_S", "900")))
    max_upload_bytes: int = field(default_factory=lambda: _env_int(
        "LABS_MAX_UPLOAD_MB", 50) * 1024 * 1024)
    allowed_suffixes: Tuple[str, ...] = field(default_factory=lambda: _env_list(
        "LABS_ALLOWED_SUFFIXES", ".wav,.mp3,.flac,.m4a,.aac,.ogg,.opus"))
    # Per-segment zero-mean/unit-variance before MERT.
    #
    # The HF Space does NOT do this, but it pairs with different checkpoints.
    # Measured on the Google-Drive/teamup-tech pair this repo actually loads
    # (logit per track, ON vs OFF):
    #     1.mp3   +7.67 / -2.56     22.mp3  +0.39 / -3.21
    #     3.mp3   -7.21 / -7.21      9.mp3  -7.21 / -7.21
    # With it OFF every track tested lands on "Real", so ON is the default.
    # See TESTING.md - confirm against labelled audio before changing.
    normalize_waveform: bool = field(default_factory=lambda: _env_bool(
        "LABS_NORMALIZE_WAVEFORM", True))


@dataclass(frozen=True)
class ModelConfig:
    device: str = field(default_factory=lambda: _env("LABS_DEVICE", "auto"))
    # Segments per backbone forward. 0 means "pick from the device", which is
    # the right default because the correct value is INVERTED between CPU and
    # GPU and a single number is wrong on one of them.
    #
    # CORRECTION: an earlier version of this comment claimed MERT runs eager
    # torch.bmm attention and materialises a [B*12, 750, 750] score tensor.
    # That is wrong on this pinned stack. MERT's remote code defers to the
    # installed library, and on transformers 4.44.2 it resolves to
    # HubertSdpaAttention with config._attn_implementation == "sdpa":
    #     uses scaled_dot_product_attention: True
    #     uses torch.bmm: False
    # So there is no eager-attention lever left to pull here. The batch-size
    # curve below is still U-shaped and still measured, it is just SDPA
    # workspace and per-call overhead rather than a giant score tensor.
    #
    # Measured on the real Stage-1 checkpoint, M1, 4 threads, 48 segments:
    #     batch 1   ->  61.4 s/track
    #     batch 2   ->  51.5 s/track
    #     batch 4   ->  51.3 s/track   <- flat optimum
    #     batch 8   ->  56.2 s/track
    #     batch 16  -> 100.6 s/track
    # The win over the previous default of 8 is only ~1.10x; this is not a
    # large lever, it is just free. On a GPU the opposite holds: the tensor
    # lives in HBM and one large launch beats many small ones, so the whole
    # sequence goes at once.
    backbone_batch_size: int = field(default_factory=lambda: _env_int(
        "LABS_BACKBONE_BATCH", 0))
    input_dim: int = 768
    # Upstream inference calls scaled_sigmoid(logits, 1.0, 0.0) explicitly,
    # overriding the function's own 0.2/0.3 defaults.
    sigmoid_scale_factor: float = field(default_factory=lambda: float(
        _env("LABS_SIGMOID_SCALE", "1.0")))
    sigmoid_linear_property: float = field(default_factory=lambda: float(
        _env("LABS_SIGMOID_LINEAR", "0.0")))
    # Which column of Stage-1's 2-class head means "AI-generated".
    # Measured, not assumed: with index 1 the per-window scores are perfectly
    # ANTI-correlated with Stage-2 (Fake track -> 0.0006, Real track -> 0.9999),
    # so Stage-1 orders its classes [fake, real]. See TESTING.md.
    stage1_fake_index: int = field(default_factory=lambda: _env_int(
        "LABS_STAGE1_FAKE_INDEX", 0))
    # DSP/musicological analysis alongside the neural verdict. Adds a few
    # seconds per request; disable for a verdict-only deployment.
    signal_analysis: bool = field(default_factory=lambda: _env_bool(
        "LABS_SIGNAL_ANALYSIS", True))
    # Torch intra-op threads. 0 leaves torch's own default in place.
    #
    # Torch defaults to one thread per *logical* CPU. The backbone is
    # GEMM-bound, and two hyperthreads sharing one core's vector units contend
    # rather than scale, so on a hyperthreaded box the right value is the
    # physical core count - half the vCPU count on every current EC2 x86
    # instance type. Left at 0 here because the correct number is a property
    # of the host, not of the code; the deployment sets it.
    torch_threads: int = field(default_factory=lambda: _env_int(
        "LABS_TORCH_THREADS", 0))
    # Autocast dtype for inference: "" (off), "bfloat16" or "float16".
    #
    # OFF BY DEFAULT, AND CHANGING IT CHANGES VERDICTS. On Intel Sapphire
    # Rapids (c7i) and newer, bfloat16 matmuls dispatch to AMX and the backbone
    # runs substantially faster. bfloat16 has the same exponent range as
    # float32 and loses mantissa bits, so the arithmetic is close but not
    # identical, and this model's output is a calibrated probability that a
    # threshold is applied to - a small shift near the boundary flips the
    # answer for tracks that sit there.
    #
    # Treat enabling this as a model change: run the labelled set in
    # TESTING.md, compare verdicts and logits against fp32, and only then turn
    # it on. float16 is offered for completeness and is the worse choice on
    # CPU - narrower exponent range, no AMX path, and it overflows where
    # bfloat16 does not.
    autocast_dtype: str = field(default_factory=lambda: _env(
        "LABS_AUTOCAST_DTYPE", "").strip().lower())
    # Concurrent inferences allowed. Each holds the model plus its activations,
    # so unbounded concurrency is an OOM risk rather than a throughput win.
    max_concurrency: int = field(default_factory=lambda: _env_int(
        "LABS_MAX_CONCURRENCY", 2))


@dataclass(frozen=True)
class SegmentPlanConfig:
    """How the 48 Stage-1 windows are chosen from the downbeat grid.

    THE PROBLEM THIS SOLVES
    -----------------------
    Upstream cuts a fixed 10 s window at EVERY surviving downbeat and stops at
    48. beat_this emits downbeats every ~2.5 s, so consecutive windows overlap
    by ~75% and the 48 slots are exhausted long before the track ends.
    Measured over this repo's audio/ folder:

        mean redundancy  3.77x   (480 s of MERT input per ~127 s of unique audio)
        mean coverage    0.77    (the last ~23% of a track is never seen)
        at the 48 cap    7 of 10 tracks

    For 1.mp3 the 48th window ends at 144 s of a 186 s track: Stage-1 renders a
    verdict having never heard the final 42 s.

    THE FIX
    -------
    `spread` picks every Nth eligible downbeat, with N chosen so the 48 windows
    span the WHOLE track. Same tensor shape Stage-2 was trained on, same cost,
    but the model now sees the entire song instead of its opening. This is
    strictly better than upstream on both axes, which is why it is the default.

    Setting `spread=False` restores the exact upstream behaviour for A/B work.
    """
    spread: bool = field(default_factory=lambda: _env_bool("LABS_SEGMENT_SPREAD", True))
    # Extra decimation applied on top of `spread`, for experiments. 1 = none.
    # Raising this trades accuracy for speed and is NOT a free win: borderline
    # tracks move. See CascadeConfig for the safe way to buy the same speed.
    stride: int = field(default_factory=lambda: _env_int("LABS_SEGMENT_STRIDE", 1))


@dataclass(frozen=True)
class CascadeConfig:
    """Two-pass Stage-1: decimated first, full density only when it matters.

    WHY THIS IS SAFE AND BLANKET DECIMATION IS NOT
    ----------------------------------------------
    Measured on the real checkpoints across 10 tracks at strides 1-4, the
    sensitivity to segment count is entirely a function of confidence:

        |logit| >= 5   immune.      1.mp3 moves 0.004 between stride 1 and 4;
                                    ai2 0.010; ai5 0.007. Six of ten tracks sit
                                    at a saturated +-7.21 and do not care.
        |logit| <  2   unstable.    ai1 FLIPS Fake->Real at stride 2. ai3 swings
                                    +1.39 -> -0.54 -> +0.47. ai4 collapses
                                    +4.574 -> +0.937.

    So we decimate first, and escalate exactly the tracks that cannot take it.

    Escalation is free of recompute because Stage-1 embeddings are per-segment
    and INDEPENDENT - nothing couples them until Stage-2's SSM - so the second
    pass computes only the segments the first pass skipped and reuses the rest.
    Expected cost with stride 3 and this threshold, on the same 10 tracks:

        0.6 * 0.40 (early exit) + 0.4 * 1.02 (escalated) = 0.65  ->  ~1.55x

    and every verdict identical to a full-density run. That "identical" is the
    point: the speedup is bought from work that provably did not change the
    answer, not from accuracy.
    """
    enabled: bool = field(default_factory=lambda: _env_bool("LABS_CASCADE", True))
    # Decimation of the first pass, on top of the coverage spread.
    first_pass_stride: int = field(default_factory=lambda: _env_int(
        "LABS_CASCADE_STRIDE", 3))
    # |raw_logit| at or above which the first pass is trusted and we stop.
    # 5.0 sits inside the immune band with margin; the saturation point is 7.21.
    escalate_below: float = field(default_factory=lambda: float(
        _env("LABS_CASCADE_ESCALATE_BELOW", "5.0")))
    # Never bother cascading a track with few enough segments that the first
    # pass would not save anything.
    min_segments: int = field(default_factory=lambda: _env_int(
        "LABS_CASCADE_MIN_SEGMENTS", 12))


@dataclass(frozen=True)
class DeepPolicyConfig:
    """How Level 2's raw logit becomes a published verdict.

    WHY THIS EXISTS
    ---------------
    Stage-2 emits an unbounded logit that saturates around +-7.21, and until
    now anything above 0 was published as "Fake" and anything below as "Real".
    That is a hard threshold on a continuous score with no middle, so a track
    the model has no opinion about is reported with the same grammar as one it
    is certain of.

    ai1.mp3 is the case that forces the issue. It sits at |logit| 0.4 - a coin
    flip - and it lands on opposite sides of zero depending on which windows
    Stage-1 was given: +0.393 on upstream's first-48 plan, -0.421 once the
    windows are spread across the whole track. Both readings are honest and
    neither is a verdict, but the old output turned that into "Fake" one day
    and "Real" the next.

    Banding it is the fix. Below `inconclusive_below` the service says it does
    not know, which is both true and stable, and the flip stops being a change
    in any published claim.

    `prediction` is left untouched for existing callers. `verdict` is the
    banded field, and it uses the same vocabulary as Level 1 so a frontend can
    render either tier with one code path.
    """
    # |raw_logit| below which no verdict is published. 2.0 is where the
    # existing reliability heuristic already docks 20 points for sitting "near
    # the decision boundary"; this makes that judgement binding instead of
    # advisory. Six of ten measured tracks saturate past |7.2|, so the band
    # costs very little coverage.
    inconclusive_below: float = field(default_factory=lambda: float(
        _env("LABS_INCONCLUSIVE_BELOW", "2.0")))
    enabled: bool = field(default_factory=lambda: _env_bool(
        "LABS_DEEP_BANDING", True))
    # Mirrors ScreenConfig.uncertain_margin so `assessment.band` means the same
    # thing whichever tier answered. Reporting only; never gates a verdict.
    uncertain_margin: float = field(default_factory=lambda: float(
        _env("LABS_UNCERTAIN_MARGIN", "0.15")))


@dataclass(frozen=True)
class ScreenConfig:
    """Level 1: the cheap screen that runs before the MERT backbone.

    Two small ONNX models over two different representations, ~1.2 MB of
    weights total against Stage-1's 1.29 GB:

      fakeprint  averaged linear spectrum 1-8 kHz, lower-hull residue ->
                 exact peak LOCATIONS. Sharp on clean generator output,
                 fragile to anything that shifts frequencies.
      cepstrum   CQT / DCT on a log-frequency axis -> learned TEXTURE.
                 Survives pitch shifting (a translation on a log axis), less
                 sharp on subtle artifacts.

    Complementary failure modes are the entire point. They are fused so that
    agreement raises confidence and disagreement lowers it - never max(), which
    is an OR gate that compounds both models' false positives.

    This tier is free and ungated, so its cost is a product decision, not just
    an engineering one. Everything expensive (test-time augmentation, the
    region map, stems) belongs to the deep and full tiers.
    """
    enabled: bool = field(default_factory=lambda: _env_bool("LABS_SCREEN", True))
    # Empty means "the weights that ship inside the package". They are 1.2 MB
    # of package data under labs/screen/weights/, deliberately NOT an external
    # directory: the free tier must be usable from a bare `pip install` and
    # from a container with no volume mounted, and a path that has to be
    # provisioned is a path that can be missing.
    models_dir: str = field(default_factory=lambda: _env("LABS_SCREEN_MODELS_DIR", ""))
    fakeprint_onnx: str = field(default_factory=lambda: _env(
        "LABS_SCREEN_FAKEPRINT_ONNX", "fakeprint.onnx"))
    cepstrum_onnx: str = field(default_factory=lambda: _env(
        "LABS_SCREEN_CEPSTRUM_ONNX", "cepstrum_cnn.onnx"))

    # -- shared front end -------------------------------------------------
    # BOTH models were trained at 16 kHz, so one decode serves both. The
    # reference implementation decoded the file separately for each model and
    # again for the bandwidth screen; collapsing that to a single pass is the
    # largest single saving in this tier.
    sample_rate: int = 16000
    max_duration_s: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_MAX_DURATION_S", "300")))

    # -- fakeprint (must match training exactly; do not tune) -------------
    n_fft: int = 8192
    freq_min: float = 1000.0
    freq_max: float = 8000.0
    hull_bins: int = 10
    max_db: float = 5.0
    min_db: float = -45.0
    n_features: int = 3585

    # -- cepstrum CNN (must match training exactly; do not tune) ----------
    use_cnn: bool = field(default_factory=lambda: _env_bool("LABS_SCREEN_CNN", True))
    cqt_fmin: float = 500.0
    cqt_n_bins: int = 48
    cqt_bins_per_octave: int = 12
    cqt_hop: int = 512
    n_coeffs: int = 24
    segment_seconds: float = 10.0
    # Windows the CNN median-pools over. The reference used 40. Pooling is a
    # MEDIAN, which converges quickly: 16 windows spread across the track give
    # the same verdict for a fraction of the CQT cost, and CQT is the dominant
    # term in this tier. Raise it if you would rather have the tighter estimate.
    cnn_segments: int = field(default_factory=lambda: _env_int(
        "LABS_SCREEN_CNN_SEGMENTS", 16))

    # -- fusion -----------------------------------------------------------
    fakeprint_weight: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_FAKEPRINT_WEIGHT", "0.55")))
    cnn_weight: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_CNN_WEIGHT", "0.45")))
    high_band: float = 0.6
    low_band: float = 0.4
    agreement_boost: float = 1.08
    agreement_confidence: float = 1.15
    disagreement_confidence: float = 0.55
    single_model_confidence: float = 0.8

    # -- policy -----------------------------------------------------------
    # Deliberately asymmetric and deliberately wide in the middle. On a
    # marketplace, wrongly flagging a human producer costs a customer and a
    # public complaint; missing one AI track costs very little. Both bars are
    # high and the gap between them is reported as `inconclusive` rather than
    # rounded to whichever side is nearer.
    ai_threshold: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_AI_THRESHOLD", "0.80")))
    human_threshold: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_HUMAN_THRESHOLD", "0.20")))
    min_confidence: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_MIN_CONFIDENCE", "0.45")))
    # Half-width of the `uncertain` band around 0.5 in `assessment.band`.
    # Purely a reporting tag - it never changes `verdict`, and it never
    # suppresses `label`. 0.15 puts the boundary at 0.35/0.65, which is where
    # the fused score stops being dominated by whichever detector happened to
    # be louder. Shared with the deep tier so one legend fits both.
    uncertain_margin: float = field(default_factory=lambda: float(
        _env("LABS_UNCERTAIN_MARGIN", "0.15")))

    # -- early exit -------------------------------------------------------
    # When Level 1 is certain a track is AI, Level 2 cannot change the verdict,
    # only describe it - so `ai` mode stops here and never loads the backbone.
    # This is the whole economic argument for the tier: it removes the 60-90 s
    # of CPU from the requests that need it least.
    #
    # Only the AI side exits early, and that asymmetry is on purpose. A
    # confident "human" from two small models is exactly the case where the
    # large model earns its cost, because a generator neither model has seen is
    # indistinguishable from human audio TO THEM.
    exit_on_ai: bool = field(default_factory=lambda: _env_bool(
        "LABS_SCREEN_EXIT_ON_AI", True))
    exit_confidence: float = field(default_factory=lambda: float(
        _env("LABS_SCREEN_EXIT_CONFIDENCE", "0.85")))

    # -- deterministic screens --------------------------------------------
    c2pa_enabled: bool = field(default_factory=lambda: _env_bool("LABS_SCREEN_C2PA", True))
    container_enabled: bool = field(default_factory=lambda: _env_bool(
        "LABS_SCREEN_CONTAINER", True))
    bandwidth_enabled: bool = field(default_factory=lambda: _env_bool(
        "LABS_SCREEN_BANDWIDTH", True))

    # -- appeals ----------------------------------------------------------
    # Retain the result and the audio when Level 1 ENDS a request on its own,
    # so the caller can ask for the deep model without re-uploading.
    #
    # Only the early-exit path is retained. That is the case where the caller
    # was cut short and has no deep result to look at; everything else already
    # escalates, and retaining every screen on an ungated endpoint would be an
    # unbounded storage bill and a standing pile of strangers' audio.
    #
    # Inert unless MongoDB and an audio bucket are both configured.
    retain_for_appeal: bool = field(default_factory=lambda: _env_bool(
        "LABS_SCREEN_RETAIN", True))
    # Seconds a retained Level-1 result is kept before a TTL index drops it.
    # 0 means KEEP FOREVER, and is the default.
    #
    # A retained screen is one half of a labelled example: the machine's
    # verdict, waiting for a human to agree or disagree with it. Expiring it
    # throws away the appeal window AND the training signal, and an appeal that
    # arrives after a short window reads to the user as the system refusing to
    # hear them. Set a positive value only if a retention policy requires it.
    retain_seconds: int = field(default_factory=lambda: _env_int(
        "LABS_SCREEN_RETAIN_S", 0))

    # ONNX Runtime intra-op threads. 0 leaves ORT's own default (one per core).
    # Level 1 is short and usually runs one-per-container, so the default is
    # normally right; set it when packing several workers onto one box.
    onnx_threads: int = field(default_factory=lambda: _env_int(
        "LABS_SCREEN_ONNX_THREADS", 0))


@dataclass(frozen=True)
class ServerConfig:
    # Static keys from the environment. Still honoured, and used as the
    # bootstrap path before any key exists in MongoDB. Database-backed keys
    # (see app/auth.py) are preferred because they carry an owner, scopes,
    # quotas and revocation.
    api_keys: Tuple[str, ...] = field(default_factory=lambda: _env_list("LABS_API_KEYS", ""))
    cors_origins: Tuple[str, ...] = field(default_factory=lambda: _env_list(
        "LABS_CORS_ORIGINS", ""))
    eager_load: bool = field(default_factory=lambda: _env_bool("LABS_EAGER_LOAD", True))
    require_auth: bool = field(default_factory=lambda: _env_bool("LABS_REQUIRE_AUTH", False))
    # Requests per minute per key. 0 disables the limiter.
    #
    # PER CONTAINER, not per fleet. The window lives in process memory, so
    # behind a load balancer a key's real allowance is this value times the
    # number of API replicas: at 2 replicas, 60 here admits 120/min. Measured,
    # not theoretical - 90 rapid reads against a 60/min limit produced zero
    # 429s because they split across two containers.
    #
    # Set it to the fleet-wide target DIVIDED by the replica count, or move the
    # window into Redis for a limit that means what it says. The in-flight cap
    # below is enforced fleet-wide against MongoDB instead, because queue
    # flooding is the abuse that actually costs money.
    rate_limit_per_min: int = field(default_factory=lambda: _env_int("LABS_RATE_LIMIT_PER_MIN", 60))
    # Concurrent in-flight analyses a single key may hold. Enforced fleet-wide
    # on the distributed path by counting queued/running jobs in MongoDB; the
    # in-process slot is a fast local guard in front of that.
    max_inflight_per_key: int = field(default_factory=lambda: _env_int("LABS_MAX_INFLIGHT_PER_KEY", 4))
    # Signs outbound webhooks so the receiver can verify authenticity.
    webhook_secret: Optional[str] = field(
        default_factory=lambda: os.environ.get("LABS_WEBHOOK_SECRET") or None)
    webhook_retries: int = field(default_factory=lambda: _env_int("LABS_WEBHOOK_RETRIES", 3))
    # Default secondary-verification policy when a caller does not say.
    #
    # "never" is the historical default and means the local model alone
    # decides. A deployment that has configured a verification provider almost
    # certainly wants "auto" - the provider is a catalogue lookup, so a
    # positive match is stronger evidence than any amount of statistical
    # inference, and "never" leaves that on the table.
    verify_default: str = field(default_factory=lambda: _env(
        "LABS_VERIFY_DEFAULT", "never").strip().lower())

    # Let the consensus verdict replace the headline `prediction`.
    #
    # Without this the consensus block is decorative: it can conclude that
    # verification overrode the local model while `report["prediction"]` still
    # says what the local model thought, and every caller reads `prediction`.
    promote_consensus: bool = field(default_factory=lambda: _env_bool(
        "LABS_PROMOTE_CONSENSUS", True))

    # Reported in every response so a verdict can be traced to a build.
    api_version: str = field(default_factory=lambda: _env("LABS_API_VERSION", "1.1.0"))

    # Strict-Transport-Security max-age. 0 omits the header, which is correct
    # for any deployment reachable over plain HTTP; a year is the usual value
    # once TLS is terminated in front of the service.
    hsts_seconds: int = field(default_factory=lambda: _env_int("LABS_HSTS_SECONDS", 0))

    # Serve /docs, /redoc and /openapi.json. The schema enumerates every route,
    # parameter and error code, which is a map for anyone probing the service,
    # so a public deployment should publish the documentation rather than the
    # live endpoint.
    expose_docs: bool = field(default_factory=lambda: _env_bool("LABS_EXPOSE_DOCS", True))

    # How long to let running analyses finish on shutdown before the pool is
    # dropped. Must be under the orchestrator's own termination grace period,
    # or the container is killed mid-drain and the wait bought nothing.
    shutdown_drain_seconds: float = field(
        default_factory=lambda: float(_env("LABS_SHUTDOWN_DRAIN_S", "30")))


@dataclass(frozen=True)
class StorageConfig:
    """MongoDB for records, S3 for the audio itself.

    Both are optional. With neither configured the service runs with in-memory
    jobs and no persistence, writing nothing anywhere.

    Note that content deduplication depends on MongoDB. Without it every
    submission runs the full pipeline, including byte-identical repeats.
    """
    mongo_uri: Optional[str] = field(
        default_factory=lambda: os.environ.get("LABS_MONGO_URI") or None)
    mongo_db: str = field(default_factory=lambda: _env("LABS_MONGO_DB", "labs"))
    # Days an analysis is kept before a TTL index drops it.
    #
    # 0 means KEEP FOREVER, and is the default. A report is the expensive
    # artifact this system exists to produce - 60-90 s of CPU, and the only
    # record of what the models said about a track. Expiring it silently
    # destroys the corpus that makes dedup, trend analysis and any future
    # retraining possible, and the storage is measured in kilobytes per track.
    # Set a positive value only if a retention policy requires deletion.
    result_ttl_days: int = field(default_factory=lambda: _env_int("LABS_RESULT_TTL_DAYS", 0))

    # Audio storage. Empty means uploaded audio is never persisted.
    audio_bucket: Optional[str] = field(
        default_factory=lambda: os.environ.get("LABS_AUDIO_BUCKET") or None)
    audio_prefix: str = field(default_factory=lambda: _env("LABS_AUDIO_PREFIX", "uploads"))
    # Days before an uploaded file is removed by the bucket lifecycle rule.
    # 0 means KEEP FOREVER, and is the default: the audio and its features are
    # the dataset. Terraform reads this to decide whether to create an
    # expiration rule on the bucket at all.
    audio_ttl_days: int = field(default_factory=lambda: _env_int("LABS_AUDIO_TTL_DAYS", 0))
    s3_region: Optional[str] = field(
        default_factory=lambda: (os.environ.get("LABS_S3_REGION")
                                 or os.environ.get("AWS_REGION")))
    # On by default: every analysed track is kept alongside its record. It is
    # still inert until LABS_AUDIO_BUCKET is set, so a deployment without a
    # bucket configured simply stores nothing.
    store_audio: bool = field(default_factory=lambda: _env_bool("LABS_STORE_AUDIO", True))


@dataclass(frozen=True)
class Settings:
    checkpoints: CheckpointConfig = field(default_factory=CheckpointConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    segments: SegmentPlanConfig = field(default_factory=SegmentPlanConfig)
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
    deep_policy: DeepPolicyConfig = field(default_factory=DeepPolicyConfig)


def get_settings() -> Settings:
    """Built fresh from the environment; callers hold onto the result."""
    return Settings()


def resolve_device(requested: str) -> str:
    import torch

    if requested and requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
