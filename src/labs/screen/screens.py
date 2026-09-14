"""Deterministic screens: signed credentials, container forensics, bandwidth.

None of these is a model. They are cheap, explainable and run before anything
expensive. Two of the three are weak priors that the policy weights lightly -
their real value is EXPLANATORY, so that when a model flags a track a human
reviewer can see why it might be right.

The C2PA screen is the exception and is decisive on its own: a signed manifest
naming a generative tool is a declaration, not an inference.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

# --------------------------------------------------------------- C2PA ------
AI_TOOL_HINTS = (
    "suno", "udio", "sonauto", "musicgen", "audiocraft", "stable audio",
    "stableaudio", "riffusion", "elevenlabs", "eleven labs", "mureka",
    "minimax", "seed-music", "seedmusic", "lyria", "mubert", "soundraw",
    "boomy", "aiva", "generative", "text-to-music", "text-to-audio",
)


@dataclass
class C2PAResult:
    available: bool
    present: bool = False
    generator: Optional[str] = None
    ai_declared: bool = False
    manifests: List[dict] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_manifest(c2pa, path: str):
    """(manifest, error). manifest None with error None means genuinely none.

    The c2pa-python API moved between releases: `Reader.from_file` went away
    around 0.3x in favour of `Reader.try_create`, which returns None rather
    than raising when there is no manifest. Trying all three in order keeps
    this working across versions. Supporting only the removed method is how
    this silently reported every file as unsigned.
    """
    reader = None
    try_create = getattr(c2pa.Reader, "try_create", None)
    if callable(try_create):
        try:
            reader = try_create(path)
            if reader is None:
                return None, None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    if reader is None:
        from_file = getattr(c2pa.Reader, "from_file", None)
        if callable(from_file):
            try:
                reader = from_file(path)
            except Exception as exc:  # noqa: BLE001
                return None, f"{type(exc).__name__}: {exc}"
    if reader is None:
        try:
            reader = c2pa.Reader(path)
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"

    try:
        return json.loads(reader.json()), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        close = getattr(reader, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                log.debug("C2PA reader close failed", exc_info=True)


def c2pa(path: str) -> C2PAResult:
    """Read content credentials, if the optional dependency is installed."""
    try:
        import c2pa as _c2pa
    except Exception:
        return C2PAResult(available=False,
                          note="c2pa-python is not installed; content "
                               "credentials were not checked.")

    manifest, error = _read_manifest(_c2pa, path)
    if error is not None:
        return C2PAResult(available=True,
                          note=f"manifest could not be read ({error}).")
    if manifest is None:
        return C2PAResult(
            available=True,
            note="No content credentials in this file. Most files have none "
                 "and they are trivially stripped, so this is not evidence "
                 "either way.")

    store = manifest.get("manifests", {}) or {}
    if not store:
        return C2PAResult(available=True, note="No manifests in the file.")

    active = manifest.get("active_manifest")
    entries: List[dict] = []
    generator = None
    for key, m in store.items():
        tool = m.get("claim_generator")
        info = m.get("claim_generator_info")
        if not tool and isinstance(info, list) and info:
            tool = info[0].get("name") if isinstance(info[0], dict) else str(info[0])
        entry = {"id": key, "active": key == active, "claim_generator": tool,
                 "title": m.get("title"), "actions": [], "software": []}
        for a in m.get("assertions", []) or []:
            if "actions" in (a.get("label") or ""):
                for act in (a.get("data", {}) or {}).get("actions", []) or []:
                    if act.get("action"):
                        entry["actions"].append(act["action"])
                    if act.get("softwareAgent"):
                        entry["software"].append(str(act["softwareAgent"]))
        entries.append(entry)
        blob = json.dumps(entry).lower()
        for hint in AI_TOOL_HINTS:
            if hint in blob:
                generator = tool or hint
                break

    declared = generator is not None
    return C2PAResult(
        available=True, present=True, generator=generator,
        ai_declared=declared, manifests=entries,
        note=(f"Signed manifest names a generative tool ({generator}). This "
              "is a declaration of AI origin, not an inference."
              if declared else
              "Signed manifest present, but no generative tool is named."))


# ---------------------------------------------------------- container ------
ENCODER_HINTS = {
    "lavf": "FFmpeg/libavformat - common in automated export pipelines",
    "lame": "LAME - typical of a desktop DAW or a manual MP3 export",
    "itunes": "Apple encoder - consumer export",
    "fraunhofer": "Fraunhofer - professional or broadcast chain",
    "sonic": "Sonic Foundry / Sound Forge",
}

GENERATOR_DURATION_CLUSTERS = [
    (118.0, 124.0, "~2 min - common single-generation length"),
    (238.0, 244.0, "~4 min - common extended-generation length"),
    (28.0, 32.0, "~30 s - short-form generation"),
]


@dataclass
class ContainerResult:
    available: bool
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_rate: Optional[int] = None
    duration_s: Optional[float] = None
    encoder: Optional[str] = None
    encoder_note: Optional[str] = None
    duration_cluster: Optional[str] = None
    metadata_sparse: bool = False
    signals: List[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def container(meta: Dict) -> ContainerResult:
    """Encoder strings, duration clustering and tag sparsity.

    Explicitly weak. A producer can export at 44.1 kHz through LAME too, and
    the measurement that proves the point is in this repo's own audio/ folder:
    most files named ai*.mp3 carry encoded_by="LAME in FL Studio", a DAW.
    """
    if not meta:
        return ContainerResult(available=False, note="ffprobe returned nothing.")

    signals: List[str] = []
    enc = (meta.get("encoder") or "").strip()
    enc_note = None
    if enc:
        low = enc.lower()
        for key, desc in ENCODER_HINTS.items():
            if key in low:
                enc_note = desc
                break
        if enc_note is None:
            enc_note = "unrecognised encoder string"
    else:
        signals.append("no encoder string - stripped or programmatically written")

    dur = meta.get("duration")
    cluster = None
    if dur:
        for lo, hi, label in GENERATOR_DURATION_CLUSTERS:
            if lo <= dur <= hi:
                cluster = label
                signals.append(f"duration sits in a generator output cluster ({label})")
                break

    tags = meta.get("tags") or {}
    meaningful = [k for k in tags
                  if k.lower() not in ("encoder", "tsse", "tlen", "tenc")]
    sparse = not meaningful
    if sparse:
        signals.append("no descriptive metadata tags")

    if meta.get("sample_rate") == 32000:
        signals.append("32 kHz sample rate - unusual for a DAW export, common "
                       "for some generator outputs")

    return ContainerResult(
        available=True, codec=meta.get("codec_name"),
        sample_rate=meta.get("sample_rate"), channels=meta.get("channels"),
        bit_rate=meta.get("bit_rate"), duration_s=dur, encoder=enc or None,
        encoder_note=enc_note, duration_cluster=cluster,
        metadata_sparse=sparse, signals=signals,
        note=("Weak priors only. Each of these also occurs in legitimate "
              "exports; they are recorded to explain a verdict, never to "
              "drive one." if signals else "Nothing unusual in the container."))


# ---------------------------------------------------------- bandwidth ------
CUTOFF_SIGNATURES = [
    (10500, 11600, "~64 kbps lossy or heavily degraded source"),
    (14500, 15600, "~112-128 kbps MP3"),
    (15600, 16600, "~128 kbps MP3"),
    (16600, 17600, "~160-192 kbps MP3"),
    (17600, 18600, "~192-256 kbps MP3 / 128 kbps AAC"),
    (18600, 19600, "~256-320 kbps MP3 / 192 kbps AAC"),
    (19600, 20600, "~320 kbps MP3 / 256 kbps AAC"),
]


@dataclass
class BandwidthResult:
    available: bool
    cutoff_hz: Optional[float] = None
    drop_db: Optional[float] = None
    edge_sharpness: Optional[float] = None
    inferred_chain: Optional[str] = None
    unusually_sharp_edge: bool = False
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def bandwidth(y: np.ndarray, sr: int) -> BandwidthResult:
    """Where the energy stops, and how abruptly.

    Generators tend to leave a cleaner, more consistent ceiling than a mastered
    human record, because nothing after the vocoder adds high-frequency
    content. Needs a wideband signal: run on the 16 kHz model decode every file
    would appear to stop at Nyquist.
    """
    try:
        import librosa

        S = np.abs(librosa.stft(y, n_fft=4096, hop_length=2048))
        ltas = S.mean(axis=1)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    except Exception as exc:  # noqa: BLE001
        return BandwidthResult(available=False, note=f"analysis failed: {exc}")

    mag = 20.0 * np.log10(np.maximum(ltas, 1e-12))
    mask = freqs > 2000
    if mask.sum() < 32:
        return BandwidthResult(available=False, note="insufficient bandwidth")
    f, m = freqs[mask], mag[mask]
    ref = float(np.percentile(m, 75))

    below = m < ref - 25.0
    if not below.any():
        return BandwidthResult(
            available=True,
            inferred_chain="full bandwidth - lossless or near-lossless",
            note="No lossy ceiling detected.")

    # Smoothed so a single dipping bin is not mistaken for a codec lowpass;
    # the cutoff has to be SUSTAINED to count.
    smooth = np.convolve(below.astype(float), np.ones(15) / 15, mode="same")
    idx_arr = np.where(smooth > 0.8)[0]
    if idx_arr.size == 0:
        return BandwidthResult(available=True, note="No sustained cutoff found.")
    idx = int(idx_arr[0])
    cutoff = float(f[idx])

    tail = m[idx:idx + 40]
    drop = float(ref - tail.mean()) if tail.size else float(ref - m[idx])

    # dB lost per 100 Hz across the edge. A codec lowpass is steep; a natural
    # rolloff is gradual.
    lo = max(idx - 10, 0)
    hi = min(idx + 10, len(m) - 1)
    span = max(f[hi] - f[lo], 1.0)
    sharp = float(abs(m[hi] - m[lo]) / span * 100.0)

    chain = next((label for lo_hz, hi_hz, label in CUTOFF_SIGNATURES
                  if lo_hz <= cutoff <= hi_hz), None)
    if chain is None:
        chain = f"bandwidth limited at {cutoff:.0f} Hz"

    return BandwidthResult(
        available=True, cutoff_hz=round(cutoff, 1), drop_db=round(drop, 2),
        edge_sharpness=round(sharp, 3), inferred_chain=chain,
        unusually_sharp_edge=sharp > 8.0,
        note=("Very sharp spectral edge - consistent with a synthetic ceiling "
              "rather than a mastered mix." if sharp > 8.0 else
              "Edge shape is consistent with ordinary lossy encoding."))
