"""Compile librosa's JIT paths before a real request needs them.

librosa decorates its hot loops with `@numba.jit(cache=True)`. The cache is
written the first time each function runs with a given argument signature, so
the process that triggers it pays the full LLVM compile - measured at 11.3 s
for the decode path and 8.1 s for Key Lab's transform on an unloaded developer
machine, and longer on a smaller instance.

On ECS that cost is not paid once. Every task is a fresh container with its own
writable layer, so a rolling deploy, a scale-out event and a task replacement
each hand the bill to whichever caller happens to arrive first. It shows up as
a tool run taking 20-plus seconds that takes two seconds an hour later, which
reads like an intermittent infrastructure fault rather than a compile.

Two places call this, and they solve different halves of the problem:

  * The Dockerfile runs it at build time. The cache files land in the image
    layer (NUMBA_CACHE_DIR, see the Dockerfile for why that must be explicit),
    so every container starts with the work already done. This is the fix.
  * `application.lifespan` and `Worker.warm` call it at startup. With the
    cache baked in that call sees the compiled kernels and returns at once;
    it only runs the sweep when the cache is missing, which covers an image
    built without the build step and turns a slow first caller into a slow
    startup, logged at WARNING so the bad build is visible.

The sweep does not list librosa primitives by hand. It writes a short
synthetic file and pushes it through the same entry points a request uses -
`decode`, every registered tool, and the three analysis passes the report is
built from - so whatever those paths call today is what gets compiled, and a
new tool is covered the day it is registered. Numba specialises on the
argument *signature* (dtype and dimensionality), not on array length, so a
few seconds of audio compiles exactly the code a five-minute track would.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Long enough for the beat tracker to see several onsets and for pYIN to
# produce a run of frames; short enough that the whole sweep is a couple of
# seconds once the cache is warm.
_SECONDS = 4.0
_SR = 44100


def _signal() -> np.ndarray:
    """Tone plus noise plus a click train, stereo, as float32.

    A pure sine gives the beat tracker no onsets and pYIN a trivial answer;
    pure noise gives the CQT no harmonic structure. Together they exercise the
    same branches real audio does. Stereo so the width and correlation
    measurements in Master Check take their real path rather than the mono
    shortcut.
    """
    n = int(_SR * _SECONDS)
    t = np.arange(n, dtype=np.float32) / _SR
    rng = np.random.default_rng(0)
    tone = 0.3 * np.sin(2 * np.pi * 220.0 * t, dtype=np.float32)
    noise = (0.1 * rng.standard_normal(n)).astype(np.float32)
    clicks = np.zeros(n, dtype=np.float32)
    clicks[:: int(_SR * 0.5)] = 1.0
    mono = (tone + noise + clicks).astype(np.float32)
    # A slight channel difference so stereo width is not exactly zero.
    right = (0.9 * mono + 0.05 * noise).astype(np.float32)
    return np.stack([mono, right], axis=1)


def _write_wav(directory: str) -> str:
    import soundfile as sf

    path = os.path.join(directory, "warmup.wav")
    sf.write(path, _signal(), _SR, subtype="PCM_16")
    return path


def _steps(path: str) -> List[Tuple[str, Callable[[], object]]]:
    """Every entry point a request can reach, as named closures.

    Named so a failure reports which path failed rather than aborting the
    rest of the sweep, and lazily imported so this module stays importable in
    a container that lacks some of them.
    """
    from ..tools import REGISTRY, decode

    steps: List[Tuple[str, Callable[[], object]]] = []
    decoded: Dict[str, object] = {}

    def do_decode():
        decoded["audio"] = decode(path)
        return decoded["audio"]

    steps.append(("decode", do_decode))

    # The tools take their inputs positionally, one Decoded per SPEC input.
    # Feeding the same file to every slot is fine: the two-file tools compare
    # a track against itself, which still runs every measurement.
    for slug, module in REGISTRY.items():
        def run_tool(module=module):
            audio = decoded["audio"]
            return module.run(*([audio] * len(module.SPEC.inputs)))
        steps.append((slug, run_tool))

    # The report's three analysis passes. They share a decode through the
    # AudioCache, exactly as the worker calls them.
    from ..analysis.decode import cache_for
    from ..analysis.features import extract as extract_features
    from ..analysis.musical import analyse as analyse_musical
    from ..analysis.production import analyse as analyse_production

    cache = cache_for(path, None)
    steps.append(("features", lambda: extract_features(path, cache=cache)))
    steps.append(("musical", lambda: analyse_musical(path, cache=cache)))
    steps.append(("production", lambda: analyse_production(path, cache=cache)))
    return steps


def baked_entries() -> Optional[int]:
    """How many compiled kernels the configured cache directory holds.

    None when no explicit directory is configured, because numba then picks
    a location per source file and there is nothing cheap to inspect.
    """
    directory = os.environ.get("NUMBA_CACHE_DIR")
    if not directory or not os.path.isdir(directory):
        return None
    count = 0
    for _root, _dirs, files in os.walk(directory):
        count += sum(1 for name in files if name.endswith(".nbi"))
    return count


def warm_signal_paths(force: bool = False) -> dict:
    """Run every signal entry point once. Returns per-step seconds.

    With a baked cache present the sweep is skipped unless `force` is set:
    the build step already proved the kernels compile, and re-running every
    tool at startup would be several seconds of CPU on a one-vCPU task,
    contending with the model loads and the first health check for nothing.
    The Dockerfile passes `force=True` because at build time the directory
    is, by definition, empty or stale.

    Never raises. A warm-up failure must not stop a container from serving:
    the cost of getting this wrong is a slow first request, and the cost of
    letting it propagate is a crash loop.
    """
    started = time.time()
    timings: dict = {}

    if not force:
        entries = baked_entries()
        if entries:
            log.info("Signal path warm-up skipped: %d compiled kernels "
                     "already cached in %s", entries,
                     os.environ.get("NUMBA_CACHE_DIR"))
            return {"cached_entries": entries, "total": 0.0}
        if entries == 0:
            # An explicit directory with nothing in it means the image was
            # built without the bake step, or the step produced nothing.
            # Worth a warning: every container from this image compiles.
            log.warning("NUMBA_CACHE_DIR is set but holds no compiled "
                        "kernels; compiling now. Check the image build.")

    try:
        import librosa  # noqa: F401
    except Exception:
        # A deployment without librosa is valid (it would serve nothing that
        # needs it), so this is not a warning.
        log.debug("Signal warm-up skipped: librosa is not installed")
        return {}

    try:
        with tempfile.TemporaryDirectory(prefix="labs-warmup-") as directory:
            path = _write_wav(directory)
            for name, step in _steps(path):
                step_started = time.time()
                try:
                    step()
                    timings[name] = round(time.time() - step_started, 2)
                except Exception:
                    timings[name] = None
                    log.warning("Signal warm-up step %r failed", name,
                                exc_info=True)
    except Exception:
        log.warning("Signal warm-up could not run", exc_info=True)

    total = round(time.time() - started, 2)
    timings["total"] = total
    # INFO rather than DEBUG: this number is how an operator tells a container
    # that started with a baked cache from one that is compiling on the spot.
    log.info("Signal path warm-up finished in %.2fs %s", total, timings)
    return timings


def warm_in_background() -> threading.Thread:
    """Warm on a daemon thread so startup does not block on it.

    Blocking would delay the load balancer's first successful health check by
    however long compilation takes, and a container that is slow to answer
    /v1/health is a container ECS may replace - turning a cold cache into a
    restart loop. Daemon so an interpreter shutdown mid-warm cannot hang the
    process.
    """
    thread = threading.Thread(target=warm_signal_paths,
                              name="signal-warmup", daemon=True)
    thread.start()
    return thread
