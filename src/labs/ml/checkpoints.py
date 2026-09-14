"""Checkpoint resolution, S3 mirroring and cache warming.

Resolution order for the two detection checkpoints, first hit wins:

  1. LABS_STAGE1_PATH / LABS_STAGE2_PATH  explicit local file
  2. <LABS_CKPT_DIR>/<filename>          already on disk
  3. the S3 mirror                      LABS_MODELS_S3_URI
  4. Hugging Face Hub                   pinned by LABS_CKPT_REVISION

Two things beyond the detection checkpoints also download at first run and are the
reason a cold start is slow even when Stage-1 and Stage-2 are present:

  * MERT-v1-95M (~400 MB) via transformers, cached under HF_HOME
  * beat_this final0.ckpt (~77 MB) via torch.hub, cached under TORCH_HOME

Both must point inside the mounted volume or they are re-fetched every time
the container is recreated. `sync_models_dir()` mirrors the whole tree from S3
in one pass so all three caches are warm before the model loads.

Nothing here is ever called from a request path.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from ..core.config import CheckpointConfig

log = logging.getLogger(__name__)


class CheckpointError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# S3 mirror
# --------------------------------------------------------------------------
def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    """s3://bucket/some/prefix -> ("bucket", "some/prefix")"""
    if not uri.startswith("s3://"):
        raise CheckpointError(f"Not an S3 URI: {uri}")
    rest = uri[5:]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise CheckpointError(f"S3 URI has no bucket: {uri}")
    return bucket, prefix.strip("/")


def sync_models_dir(cfg: CheckpointConfig) -> Dict:
    """Mirror the S3 model tree into the local models directory.

    Only downloads objects that are missing or whose size differs, so a warm
    volume costs one LIST call and nothing else. Never raises: if S3 is
    unreachable the caller falls through to the Hugging Face path.
    """
    uri = cfg.models_s3_uri
    if not uri:
        return {"enabled": False, "reason": "LABS_MODELS_S3_URI not set"}

    started = time.time()
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        log.warning("boto3 is not installed; skipping the S3 model mirror")
        return {"enabled": False, "reason": "boto3 not installed"}

    try:
        bucket, prefix = _parse_s3_uri(uri)
    except CheckpointError as exc:
        log.warning("%s", exc)
        return {"enabled": False, "reason": str(exc)}

    root = cfg.models_root
    os.makedirs(root, exist_ok=True)

    downloaded: List[str] = []
    skipped = 0
    total_bytes = 0

    try:
        s3 = boto3.client("s3", region_name=cfg.s3_region or None)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key, size = obj["Key"], obj["Size"]
                if key.endswith("/") or size == 0:
                    continue
                rel = key[len(prefix):].lstrip("/") if prefix else key
                if not rel:
                    continue
                dest = os.path.join(root, rel)
                if os.path.isfile(dest) and os.path.getsize(dest) == size:
                    skipped += 1
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                log.info("Fetching s3://%s/%s (%.1f MB)", bucket, key, size / 1e6)
                s3.download_file(bucket, key, dest)
                downloaded.append(rel)
                total_bytes += size
    except (BotoCoreError, ClientError, OSError) as exc:
        log.warning("S3 model mirror failed (%s); falling back to Hugging Face", exc)
        return {"enabled": True, "ok": False, "error": str(exc),
                "downloaded": len(downloaded), "skipped": skipped}

    elapsed = round(time.time() - started, 2)
    if downloaded:
        log.info("S3 mirror: pulled %d file(s), %.1f MB in %.1fs (%d already current)",
                 len(downloaded), total_bytes / 1e6, elapsed, skipped)
    else:
        log.info("S3 mirror: all %d file(s) already current (%.2fs)", skipped, elapsed)
    return {"enabled": True, "ok": True, "source": uri,
            "downloaded": len(downloaded), "skipped": skipped,
            "bytes": total_bytes, "seconds": elapsed}


# --------------------------------------------------------------------------
# Hugging Face fallback
# --------------------------------------------------------------------------
def _download(cfg: CheckpointConfig, filename: str) -> str:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CheckpointError(
            "huggingface_hub is required to fetch checkpoints; install it or set "
            "LABS_STAGE1_PATH / LABS_STAGE2_PATH to local files."
        ) from exc

    log.warning(
        "Falling back to Hugging Face for %s (repo=%s revision=%s). This is the "
        "slow path; mirror the weights to S3 and set LABS_MODELS_S3_URI.",
        filename, cfg.repo_id, cfg.revision)
    try:
        return hf_hub_download(
            repo_id=cfg.repo_id,
            filename=filename,
            repo_type=cfg.repo_type,
            revision=cfg.revision,
            local_dir=cfg.dir,
            token=cfg.token,
        )
    except Exception as exc:
        raise CheckpointError(
            f"Could not fetch '{filename}' from '{cfg.repo_id}'"
            f"@{cfg.revision or 'main'}: {exc}"
        ) from exc


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def _search_paths(cfg: CheckpointConfig, filename: str) -> List[str]:
    """Every local location checked, in order, before any network call.

    Covers the container layout (/models/checkpoints), a repo checkout
    (backend/checkpoints), and the process working directory.
    """
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))          # .../backend
    return [
        os.path.join(cfg.dir, filename),
        os.path.join(cfg.models_root, "checkpoints", filename),
        os.path.join(here, "checkpoints", filename),
        os.path.join(os.getcwd(), "checkpoints", filename),
        os.path.join(os.getcwd(), filename),
    ]


def resolve(cfg: CheckpointConfig, filename: str,
            explicit: Optional[str]) -> Tuple[str, str]:
    """Return (path, source). Hugging Face is the last resort, never the first.

    Order: explicit path, then every local directory, then S3 (already
    mirrored into cfg.dir by sync_models_dir), then Hugging Face.
    """
    if explicit:
        if not os.path.isfile(explicit):
            raise CheckpointError(
                f"Configured checkpoint path does not exist: {explicit}")
        return explicit, "explicit_path"

    for candidate in _search_paths(cfg, filename):
        if os.path.isfile(candidate):
            log.info("Checkpoint %s: found locally at %s", filename, candidate)
            return candidate, "local"

    if cfg.offline:
        raise CheckpointError(
            f"'{filename}' was not found locally and LABS_OFFLINE is set. "
            f"Looked in: {', '.join(_search_paths(cfg, filename))}")

    os.makedirs(cfg.dir, exist_ok=True)
    return _download(cfg, filename), "huggingface"


def _all_present_locally(cfg: CheckpointConfig) -> bool:
    return all(
        any(os.path.isfile(p) for p in _search_paths(cfg, name))
        for name in (cfg.stage1_filename, cfg.stage2_filename))


class CheckpointDigestError(RuntimeError):
    """A checkpoint is not the one that was pinned."""


def file_sha256(path: str, chunk: int = 1 << 20) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_digest(path: str, expected: Optional[str], label: str,
                  enforce: bool) -> Optional[Dict]:
    """Check a checkpoint against its pinned digest.

    Returns a small report, or None when no digest was pinned. Raises when
    enforcement is on and the file does not match.

    Hashing 1.29 GB costs a few seconds and happens once at startup, against
    a process that then serves for days - and the thing it catches is a
    different model answering in your name, which produces confident wrong
    verdicts rather than an error.
    """
    if not expected:
        return None

    actual = file_sha256(path)
    ok = actual == expected
    if not ok:
        message = (f"{label} digest mismatch: expected {expected[:16]}..., "
                   f"got {actual[:16]}... ({path})")
        if enforce:
            raise CheckpointDigestError(message)
        log.error("%s. Continuing because LABS_ENFORCE_CKPT_DIGEST is off, "
                  "but verdicts from this process are not reproducible.",
                  message)
    else:
        log.info("%s digest verified (%s...)", label, actual[:16])
    return {"expected": expected, "actual": actual, "ok": ok}


def resolve_all(cfg: CheckpointConfig) -> Dict:
    """Resolve both checkpoints, touching the network as little as possible.

    The S3 mirror is skipped entirely when both checkpoints are already on
    disk, so a warm instance does not even make a ListObjects call.
    """
    if _all_present_locally(cfg):
        log.info("Both checkpoints present locally; skipping the S3 mirror")
        mirror = {"enabled": bool(cfg.models_s3_uri), "skipped": True,
                  "reason": "checkpoints already present locally"}
    else:
        mirror = sync_models_dir(cfg)

    s1, src1 = resolve(cfg, cfg.stage1_filename, cfg.stage1_path)
    s2, src2 = resolve(cfg, cfg.stage2_filename, cfg.stage2_path)

    if "huggingface" in (src1, src2):
        log.warning(
            "At least one checkpoint came from Hugging Face at runtime. For "
            "reproducible verdicts, mirror to S3 and pin LABS_CKPT_REVISION.")

    digests = {
        "stage1": verify_digest(s1, cfg.stage1_sha256, "Stage-1",
                                cfg.enforce_digest),
        "stage2": verify_digest(s2, cfg.stage2_sha256, "Stage-2",
                                cfg.enforce_digest),
    }
    if not any(digests.values()):
        log.warning(
            "No checkpoint digests are pinned. Set LABS_STAGE1_SHA256 and "
            "LABS_STAGE2_SHA256 so a corrupted, truncated or swapped "
            "checkpoint fails loudly instead of answering in your name.")

    return {
        "stage1": s1, "stage2": s2,
        "stage1_source": src1, "stage2_source": src2,
        "revision": cfg.revision,
        "digests": digests,
        "mirror": mirror,
    }


def cache_report(cfg: CheckpointConfig) -> Dict:
    """What is present on disk, for /v1/health and for debugging cold starts."""
    def present(p: str) -> bool:
        return os.path.isdir(p) and bool(os.listdir(p))

    return {
        "models_root": cfg.models_root,
        "checkpoint_dir": cfg.dir,
        "stage1_present": os.path.isfile(os.path.join(cfg.dir, cfg.stage1_filename)),
        "stage2_present": os.path.isfile(os.path.join(cfg.dir, cfg.stage2_filename)),
        "hf_cache_present": present(os.environ.get("HF_HOME", "")),
        "torch_cache_present": present(os.environ.get("TORCH_HOME", "")),
        "s3_mirror": cfg.models_s3_uri or None,
        "revision": cfg.revision,
    }
