"""Level-1 feature extraction: the fakeprint and the CQT cepstrum.

EVERY CONSTANT HERE IS A TRAINED-IN PROPERTY OF A CHECKPOINT, NOT A TUNABLE.
The two ONNX models were fitted against these exact transforms. Change an
n_fft, a hop, a frequency bound or a window and the features land in a
different space from the weights, and the model does not fail - it returns a
confident wrong number. Anything genuinely adjustable lives in ScreenConfig.

The reference implementation built these with torch/torchaudio. They are
rewritten here on numpy/scipy/librosa so Level 1 carries no torch dependency;
the transforms are matched deliberately rather than approximately, and
`scripts/validate_screen_port.py` checks the resulting probabilities against
the reference on labelled audio.
"""
from __future__ import annotations

import logging
from typing import List

import numpy as np

from ..core.config import ScreenConfig

log = logging.getLogger(__name__)


def mean_spectrum_db(y: np.ndarray, cfg: ScreenConfig) -> np.ndarray:
    """Time-averaged power spectrum in dB, the basis of the fakeprint.

    Matched to torchaudio.transforms.Spectrogram(n_fft, power=2,
    normalized=False), whose defaults are hop = n_fft // 2, a PERIODIC Hann
    window, center=True and reflect padding. librosa's defaults differ on hop
    (n_fft // 4) and, on recent versions, on pad_mode, so all four are passed
    explicitly. Getting the hop wrong still produces a plausible-looking
    spectrum, which is what makes it dangerous.
    """
    import librosa

    spec = np.abs(librosa.stft(
        y, n_fft=cfg.n_fft, hop_length=cfg.n_fft // 2, window="hann",
        center=True, pad_mode="reflect")) ** 2
    spec_db = 10.0 * np.log10(np.clip(spec, 1e-10, 1e6))
    return spec_db.mean(axis=1)


def frequency_mask(cfg: ScreenConfig) -> np.ndarray:
    """Bins inside the artifact band, on the same grid the training used."""
    bins = np.linspace(0.0, cfg.sample_rate / 2.0, num=(cfg.n_fft // 2) + 1)
    return (bins >= cfg.freq_min) & (bins <= cfg.freq_max)


def fakeprint(y: np.ndarray, cfg: ScreenConfig) -> np.ndarray:
    """The 3585-dim fakeprint: residue of the spectrum above its own lower hull.

    A vocoder or a neural upsampler leaves a periodic comb of narrow peaks in
    the 1-8 kHz band. Subtracting a running minimum (the "lower hull") removes
    the broadband spectral shape - which is a property of the MUSIC - and
    leaves only that narrowband residue, which is a property of the SIGNAL
    CHAIN. Normalising by the peak makes it level-invariant.
    """
    spectrum = mean_spectrum_db(y, cfg)[frequency_mask(cfg)]
    return fakeprint_from_band(spectrum, cfg)


def fakeprint_from_band(spectrum: np.ndarray, cfg: ScreenConfig) -> np.ndarray:
    """The hull/residue half of the fakeprint, split out so it is testable."""
    from scipy.ndimage import minimum_filter1d

    hull = minimum_filter1d(spectrum, size=cfg.hull_bins, mode="nearest")
    hull = np.clip(hull, cfg.min_db, None)
    residue = np.clip(spectrum - hull, 0.0, cfg.max_db)
    # +1e-6 guards a digital-silence input, where the residue is all zeros.
    return (residue / (np.max(residue) + 1e-6)).astype(np.float32)


def fit_features(vec: np.ndarray, n_features: int) -> np.ndarray:
    """Resample the fakeprint onto the model's input width if they disagree.

    They agree for the shipped checkpoint (3585 bins from n_fft=8192 over
    1-8 kHz at 16 kHz). This exists so a model exported with a different band
    still runs rather than raising a shape error deep inside ORT.
    """
    if vec.size == n_features:
        return vec
    old = np.linspace(0.0, 1.0, vec.size)
    new = np.linspace(0.0, 1.0, n_features)
    return np.interp(new, old, vec).astype(np.float32)


def plan_windows(n_samples: int, cfg: ScreenConfig) -> List[int]:
    """Start offsets for the CNN's windows, spread across the track.

    The first and last 5 s are skipped when the track is long enough to afford
    it: intros and outros are commonly near-silence, a fade, or a spoken tag,
    and a window of any of those reads as neither human nor generated. They
    were a measurable source of outlier window scores, which is also why the
    pooling downstream is a median rather than a mean.
    """
    seg = int(cfg.segment_seconds * cfg.sample_rate)
    skip = 5 * cfg.sample_rate

    if n_samples > seg + 2 * skip:
        start, end = skip, n_samples - skip
    else:
        start, end = 0, n_samples

    usable = end - start
    if usable <= seg:
        # Too short to place a window inside the trimmed range: centre one.
        if n_samples <= seg:
            return [0]
        return [max(0, n_samples // 2 - seg // 2)]

    available = usable - seg
    n = max(1, cfg.cnn_segments)
    if n == 1:
        return [start + available // 2]
    step = available / (n - 1)
    return [start + int(i * step) for i in range(n)]


def windows(y: np.ndarray, cfg: ScreenConfig) -> List[np.ndarray]:
    """Zero-padded fixed-length windows at the planned offsets."""
    seg = int(cfg.segment_seconds * cfg.sample_rate)
    out: List[np.ndarray] = []
    for start in plan_windows(y.size, cfg):
        chunk = y[start:start + seg]
        if chunk.size < seg:
            padded = np.zeros(seg, dtype=np.float32)
            padded[:chunk.size] = chunk
            chunk = padded
        out.append(chunk)
    return out


def cepstrum(window: np.ndarray, cfg: ScreenConfig) -> np.ndarray:
    """One window -> [n_coeffs, frames] CQT cepstrum.

    CQT puts frequency on a log axis, so a pitch shift becomes a translation
    rather than a rescaling, and the DCT then turns the periodic comb into a
    localised peak at the cepstral index matching its spacing. That is what
    makes this model survive processing the fakeprint model cannot.
    """
    import librosa
    from scipy.fft import dct

    cqt = np.abs(librosa.cqt(
        window, sr=cfg.sample_rate, fmin=cfg.cqt_fmin, n_bins=cfg.cqt_n_bins,
        bins_per_octave=cfg.cqt_bins_per_octave, hop_length=cfg.cqt_hop))
    log_cqt = np.log(cqt + 1e-10)
    ceps = dct(log_cqt, type=2, axis=0, norm="ortho")
    return ceps[:cfg.n_coeffs, :].astype(np.float32)


def cepstrum_batch(y: np.ndarray, cfg: ScreenConfig) -> np.ndarray:
    """[N, 1, n_coeffs, frames] batch for the CNN, ready for ORT.

    Windows are a fixed sample count so every cepstrum has the same frame
    count and the stack is rectangular; the model's time axis is dynamic
    anyway, so a short final window would still run, just not batch.
    """
    mats = [cepstrum(w, cfg) for w in windows(y, cfg)]
    if not mats:
        raise ValueError("No analysis windows could be extracted.")
    frames = min(m.shape[1] for m in mats)
    return np.stack([m[:, :frames][np.newaxis, :, :] for m in mats],
                    axis=0).astype(np.float32)
