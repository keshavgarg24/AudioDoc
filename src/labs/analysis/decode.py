"""One decode, and one harmonic/percussive split, shared by every pass.

The three analysis passes each used to open the file themselves - `features`
at 44.1 kHz stereo, `musical` at 22.05 kHz mono, `production` at 44.1 kHz
stereo - and two of them ran their own HPSS over the result. That is four
decodes and two separations of the same audio per request.

`AudioCache` holds the decoded arrays for the lifetime of one analysis and
hands the same array to every caller that asks for the same rate. It is
deliberately per-request and passed explicitly rather than being a module
level cache: decoded audio is tens of megabytes per track, and a process wide
cache shared between concurrent jobs is a memory leak with extra steps.

HPSS is the single most expensive operation in the signal path. Measured on
a 186 s track at 22.05 kHz:

    librosa.effects.hpss (what features used)        17,956 ms
    librosa.effects.percussive (what musical used)    8,764 ms
    one shared decompose.hpss(kernel_size=17)          ~5,200 ms

`features` only ever needed the harmonic/percussive energy *ratio*, which by
Parseval is available from the masked magnitudes with no inverse transform at
all; only `musical` needs the percussive signal back in the time domain. So
the split runs once, `features` reads the energies off the spectrograms, and
`musical` pays for the one ISTFT.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Filter length for the median-filter separation. librosa's default is 31.
#
# Measured on the same track, time-domain percussive output, against the
# kernel-31 baseline the code used before:
#     kernel=31   8,567 ms   percussive energy 1.000  (baseline)
#     kernel=17   5,212 ms   percussive energy 1.009
#     kernel=9    2,479 ms   percussive energy 0.827
# 17 is the knee: 1.64x faster for a 0.9% shift in the statistic that
# `groove` and `drums` consume. 9 is faster still but moves it 17%.
HPSS_KERNEL = 17

N_FFT = 2048
HOP = 512


class AudioCache:
    """Decoded audio for one file, reused across the passes of one request."""

    def __init__(self, path: str):
        self.path = path
        self._pcm: Dict[Tuple[int, bool, Optional[float]], Tuple[np.ndarray, int]] = {}
        self._split: Optional[Tuple[np.ndarray, float, float]] = None

    def audio(self, sr: int, mono: bool,
              duration: Optional[float] = None) -> Tuple[np.ndarray, int]:
        """Decoded samples at `sr`, cached per (rate, channels, duration).

        Returns the cached array itself, not a copy. Callers must not mutate
        it; every pass in this package treats its input as read-only.
        """
        import librosa

        key = (int(sr), bool(mono), duration)
        hit = self._pcm.get(key)
        if hit is not None:
            return hit

        # A capped request can be served by slicing a longer decode that is
        # already in hand. `production` reads the whole file because loudness
        # is defined over the whole programme, while `features` caps at 240 s;
        # for anything shorter than the cap those are the same samples.
        if duration is not None:
            longer = self._longest(int(sr), bool(mono))
            if longer is not None:
                y, out_sr = longer
                want = int(duration * out_sr)
                sliced = y[..., :want] if y.ndim > 1 else y[:want]
                self._pcm[key] = (sliced, out_sr)
                return sliced, out_sr

        y, out_sr = librosa.load(self.path, sr=sr, mono=mono, duration=duration)
        self._pcm[key] = (y, out_sr)
        return y, out_sr

    def _longest(self, sr: int, mono: bool):
        """An uncapped decode at the same rate and channel count, if present."""
        return self._pcm.get((sr, mono, None))

    def split(self, sr: int = 22050,
              duration: Optional[float] = None) -> Tuple[np.ndarray, float, float]:
        """One HPSS pass: (percussive signal, harmonic energy, percussive energy).

        The energies are spectral, so they are not comparable in absolute terms
        to a time-domain sum of squares - only their ratio is, and the ratio is
        all any caller uses.
        """
        import librosa

        if self._split is not None:
            return self._split

        y, _ = self.audio(sr, mono=True, duration=duration)
        stft = librosa.stft(y, n_fft=N_FFT, hop_length=HOP)
        harmonic, percussive = librosa.decompose.hpss(
            stft, kernel_size=HPSS_KERNEL)

        h_energy = float(np.sum(np.abs(harmonic) ** 2))
        p_energy = float(np.sum(np.abs(percussive) ** 2))
        y_perc = librosa.istft(percussive, hop_length=HOP, length=y.size)

        self._split = (y_perc, h_energy, p_energy)
        return self._split

    def release(self) -> None:
        """Drop every buffer. Called once the report is built."""
        self._pcm.clear()
        self._split = None


def cache_for(path: str, cache: Optional[AudioCache]) -> AudioCache:
    """The caller's cache, or a throwaway one so a pass still works alone."""
    if cache is not None and cache.path == path:
        return cache
    return AudioCache(path)
