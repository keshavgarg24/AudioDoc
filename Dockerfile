# Three targets, because the two tiers have wildly different dependency
# footprints and an autoscaling fleet pays for that difference on every
# cold start.
#
#   --target screen    Level 1 only.  ~350 MB.  No torch.
#   --target api       Level 1 + 2 served in one process. The single-container
#                      deployment, and the default.
#   --target worker    Level 1 + 2, consuming SQS instead of serving HTTP.
#
# CPU only throughout. The torch CPU wheel is ~200 MB against ~2.5 GB for the
# CUDA build, and pip resolves to the CUDA one by default even on a machine
# with no GPU, so the index is pinned explicitly.

# ====================================================================== #
# build stages
# ====================================================================== #
FROM python:3.11-slim AS build-base

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential for any sdist that still needs compiling; git because the
# beat tracker installs from an archive URL.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip wheel

# ---------------------------------------------------------- deep deps ------
FROM build-base AS build-deep
COPY requirements.txt .
# The CPU index applies to torch and torchaudio; everything else resolves from
# PyPI as usual, which is what --extra-index-url preserves.
RUN /opt/venv/bin/pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt
# Whole-second mtimes on every source file, see NUMBA_CACHE_DIR below: the
# compiled-kernel cache is only valid if the build and the running container
# agree on them, and image export rounds them.
RUN /opt/venv/bin/python -c "import os; [os.utime(p, (int(os.stat(p).st_atime), int(os.stat(p).st_mtime))) for d, _, fs in os.walk('/opt/venv') for f in fs if f.endswith('.py') for p in [os.path.join(d, f)]]"

# -------------------------------------------------------- screen deps ------
FROM build-base AS build-screen
COPY requirements-screen.txt .
RUN /opt/venv/bin/pip install -r requirements-screen.txt
# Same as the deep stage above.
RUN /opt/venv/bin/python -c "import os; [os.utime(p, (int(os.stat(p).st_atime), int(os.stat(p).st_mtime))) for d, _, fs in os.walk('/opt/venv') for f in fs if f.endswith('.py') for p in [os.path.join(d, f)]]"


# ====================================================================== #
# runtime base
# ====================================================================== #
FROM python:3.11-slim AS runtime-base

# Every model cache must live under /models, the mounted volume. TORCH_HOME
# matters: without it torch.hub writes the beat-tracker checkpoint to
# /root/.cache/torch, outside the volume, and re-downloads ~77 MB every time
# the container is recreated.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/srv/src \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache \
    LABS_MODELS_DIR=/models \
    LABS_CKPT_DIR=/models/checkpoints \
    NUMBA_CACHE_DIR=/opt/numba-cache \
    NUMBA_CPU_NAME=generic

# The two NUMBA_ settings make the compiled-kernel cache baked below actually
# get used at runtime, and both are load-bearing.
#
# NUMBA_CACHE_DIR: numba's default is __pycache__ beside the installed package,
# but it only uses that location if it can write to it, and /opt/venv is
# root-owned while the process runs as `labs`. Without an explicit directory
# the runtime process would silently fall back to a user cache under
# XDG_CACHE_HOME, find it empty, and recompile - the exact 20-second first
# request the bake exists to remove. Kept outside /models because that is a
# volume: anything written there at build time is discarded.
#
# NUMBA_CPU_NAME=generic: cache entries are keyed on the CPU model and feature
# set of the machine that compiled them. The images are built on a developer
# Mac (see deploy/aws/04-images.sh) and run on Fargate Graviton and EC2 x86,
# so a host-specific cache would never match and would recompile anyway.
# `generic` targets the architecture baseline, which numba documents as the
# way to share a cache across machines. Measured cost: none (vocal-lab,
# tempo-lab and master-check within noise of the host-tuned build), because
# librosa's kernels are scalar dynamic-programming loops that gain nothing
# from CPU-specific SIMD.
#
# A third condition is enforced in the build stages: every cache entry also
# records the source file's modification time, and it must match exactly at
# runtime. The build step sees the venv with sub-second mtimes, but exporting
# the image rounds them to whole seconds, so without the `os.utime` pass
# above every entry would mismatch and every kernel would silently recompile.
# This was observed, not theorised: 24 of 24 entries missed before the fix.

# libsndfile for soundfile, ffmpeg for the mp3/m4a decode paths.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
# The Level-1 weights (1.2 MB) live inside the package, at
# labs/screen/weights/, so they arrive with this copy. Nothing about the free
# tier depends on a volume or a network fetch: a screen-only container is
# useful the moment it starts.
COPY src ./src

# Non-root. This process handles untrusted uploads and hands them to ffmpeg and
# libsndfile, which are large C parsers; if one is ever exploited the blast
# radius should not include the container filesystem or the ability to install
# packages. /models is chowned because the startup mirror writes there.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin labs \
    && mkdir -p /models \
    && chown -R labs:labs /models /srv

VOLUME ["/models"]
# Deliberately no USER here. Each final stage copies its virtualenv in - which
# needs root - and drops privileges itself immediately afterwards.


# ====================================================================== #
# screen: Level 1 only, no torch
# ====================================================================== #
FROM runtime-base AS screen
COPY --from=build-screen /opt/venv /opt/venv

# Bake librosa's numba cache into the image layer. See core/warmup.py: without
# this the first caller to reach a fresh container pays the LLVM compile - 11 s
# on the decode path alone - and on ECS that is every scale-out and every
# rolling deploy, not a one-off. Logging is switched on so the build output
# shows the per-step compile times; the cache is handed to `labs` because
# numba refuses to read from a directory it cannot also write to; and the
# build fails outright if no kernel was written, because a bake that silently
# produced nothing is exactly the failure that is otherwise invisible until
# the first slow request in production.
RUN python -c "import logging; logging.basicConfig(level=logging.INFO); \
        from labs.core.warmup import warm_signal_paths; \
        warm_signal_paths(force=True)" \
    && chown -R labs:labs "$NUMBA_CACHE_DIR" \
    && n=$(find "$NUMBA_CACHE_DIR" -name '*.nbi' | wc -l) \
    && echo "numba cache: $n compiled kernels" \
    && [ "$n" -gt 0 ]

USER labs

# No weights to load, so readiness is immediate and the probe can be strict.
ENV LABS_EAGER_LOAD=false
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Level 1 is short and CPU-bound and admission is bounded inside the
# application (LABS_SCREEN_CONCURRENCY), so one uvicorn worker is right here
# for the same reason as below: the models are the bottleneck, not the loop.
CMD ["uvicorn", "labs.application:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "75", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]


# ====================================================================== #
# deep runtime: shared by worker and api, which differ only in entrypoint
# ====================================================================== #
FROM runtime-base AS deep-runtime
COPY --from=build-deep /opt/venv /opt/venv

# Same bake and same gate as the screen stage, done once here rather than
# once per final stage: the venv is identical, so the compiled kernels are too.
RUN python -c "import logging; logging.basicConfig(level=logging.INFO); \
        from labs.core.warmup import warm_signal_paths; \
        warm_signal_paths(force=True)" \
    && chown -R labs:labs "$NUMBA_CACHE_DIR" \
    && n=$(find "$NUMBA_CACHE_DIR" -name '*.nbi' | wc -l) \
    && echo "numba cache: $n compiled kernels" \
    && [ "$n" -gt 0 ]


# ====================================================================== #
# worker: consumes SQS, serves nothing
# ====================================================================== #
FROM deep-runtime AS worker
USER labs

# No port and no HTTP health check: this container is not in a target group.
# ECS judges it by whether the process is alive, and the queue's own age-of-
# oldest-message metric is the real health signal for the fleet.
#
# Deliberately no HEALTHCHECK: a curl against a process that serves no HTTP
# would fail forever and mark a working worker unhealthy.
CMD ["python", "-m", "labs.worker"]


# ====================================================================== #
# api: both tiers in one process (single-container deployment)
# ====================================================================== #
FROM deep-runtime AS api
USER labs

EXPOSE 8000

# start-period is generous: a cold start with no mirrored volume pulls well
# over a gigabyte of weights before the first probe can succeed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# One worker on purpose. Each would hold its own copy of the weights, so a
# second process doubles resident memory for no throughput gain: the model is
# the bottleneck, not the event loop, and concurrency is bounded inside the
# application by LABS_MAX_CONCURRENCY and the job pool.
CMD ["uvicorn", "labs.application:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "75", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
