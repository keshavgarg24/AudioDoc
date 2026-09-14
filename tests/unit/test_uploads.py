"""Upload staging: what is accepted, what is refused, and what is left behind.

This is the boundary every untrusted byte crosses, so the tests care as much
about the rejection paths leaving no file on disk as they do about the
rejections happening at all.
"""
from __future__ import annotations

import io
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from labs.core.config import Settings
from labs.core.uploads import (
    STAGE_PREFIX,
    looks_like_audio,
    stage_prefix,
    stage_upload,
    sweep_orphans,
    unlink_quietly,
)
from tests.conftest import make_wav


def _upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


def _staged_files() -> list:
    return list(Path(tempfile.gettempdir()).glob(f"{STAGE_PREFIX}*"))


# ------------------------------------------------------------ magic bytes --
@pytest.mark.parametrize("head,expected", [
    (b"RIFF....WAVE", True),
    (b"fLaC\x00\x00\x00\x22", True),
    (b"OggS\x00\x02\x00\x00", True),
    (b"ID3\x04\x00\x00\x00", True),
    (b"\x00\x00\x00\x20ftypM4A ", True),
    (b"\xff\xfb\x90\x00", True),
    (b"\xff\xf1\x50\x80", True),
])
def test_accepts_known_audio_containers(head, expected):
    assert looks_like_audio(head) is expected


@pytest.mark.parametrize("head", [
    b"PK\x03\x04",                  # zip
    b"\x7fELF\x02\x01\x01\x00",     # elf binary
    b"<!DOCTYPE html>",             # html
    b"<?php echo 1; ?>",            # php
    b"#!/bin/sh\nrm -rf /",         # shell script
    b"%PDF-1.7",                    # pdf
    b"\x89PNG\r\n\x1a\n",           # png
    b"",                            # empty
])
def test_rejects_everything_that_is_not_audio(head):
    assert looks_like_audio(head) is False


def test_ftyp_offset_is_enforced():
    """'ftyp' at offset 0 is not a valid ISO-BMFF header, only at offset 4."""
    assert looks_like_audio(b"ftypM4A \x00\x00\x00\x00") is False


# ------------------------------------------------------------- rejections --
def test_unsupported_extension_is_refused():
    with pytest.raises(HTTPException) as exc:
        stage_upload(_upload(make_wav(), "track.exe"), prefix=stage_prefix("t"))
    assert exc.value.status_code == 415
    assert exc.value.detail["error"]["code"] == "unsupported_media_type"


def test_extension_lie_is_caught_by_content():
    """A .wav that is actually a zip must not reach the decoder."""
    before = len(_staged_files())
    with pytest.raises(HTTPException) as exc:
        stage_upload(_upload(b"PK\x03\x04" + b"\x00" * 512, "track.wav"),
                     prefix=stage_prefix("t"))
    assert exc.value.status_code == 415
    assert len(_staged_files()) == before, "rejected upload left a file behind"


def test_empty_file_is_refused():
    with pytest.raises(HTTPException) as exc:
        stage_upload(_upload(b"", "track.wav"), prefix=stage_prefix("t"))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "empty_file"


def test_oversized_upload_is_refused_and_cleaned_up(monkeypatch):
    settings = Settings()
    object.__setattr__(settings.audio, "max_upload_bytes", 1024)

    before = len(_staged_files())
    big = make_wav(seconds=4.0, sample_rate=8000)
    assert len(big) > 1024

    with pytest.raises(HTTPException) as exc:
        stage_upload(_upload(big, "track.wav"), prefix=stage_prefix("t"),
                     settings=settings)
    assert exc.value.status_code == 413
    assert len(_staged_files()) == before, "oversized upload left a file behind"


def test_supplied_settings_are_authoritative():
    """A caller's Settings must not be silently replaced by the environment.

    The size cap is read from the injected instance; if stage_upload fell back
    to a fresh get_settings() this would stage successfully at the default
    50 MB limit instead of refusing at 1 KB.
    """
    settings = Settings()
    object.__setattr__(settings.audio, "max_upload_bytes", 1024)
    with pytest.raises(HTTPException) as exc:
        stage_upload(_upload(make_wav(seconds=4.0), "track.wav"),
                     prefix=stage_prefix("t"), settings=settings)
    assert exc.value.status_code == 413


# ------------------------------------------------------------ happy path ---
def test_valid_wav_is_staged_to_disk():
    path = stage_upload(_upload(make_wav(), "track.wav"),
                        prefix=stage_prefix("ok"))
    try:
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
        assert os.path.basename(path).startswith(STAGE_PREFIX)
        assert path.endswith(".wav")
    finally:
        unlink_quietly(path)


def test_unlink_quietly_tolerates_a_missing_file():
    unlink_quietly("/nonexistent/path/does/not/exist.wav")


# ---------------------------------------------------------------- sweeper --
def test_prefix_helper_matches_the_sweeper_glob():
    """The sweeper only finds what stage_prefix produces. Pin that."""
    assert stage_prefix("abc").startswith(STAGE_PREFIX)
    assert stage_prefix("tool", "xyz").startswith(STAGE_PREFIX)
    assert stage_prefix() == STAGE_PREFIX


def test_sweeper_removes_old_orphans_and_spares_fresh_ones():
    old = tempfile.NamedTemporaryFile(
        prefix=stage_prefix("old"), suffix=".wav", delete=False)
    old.write(b"x")
    old.close()
    # Backdate well past the cutoff used below.
    os.utime(old.name, (time.time() - 7200, time.time() - 7200))

    fresh = tempfile.NamedTemporaryFile(
        prefix=stage_prefix("fresh"), suffix=".wav", delete=False)
    fresh.write(b"x")
    fresh.close()

    try:
        removed = sweep_orphans(max_age_s=3600)
        assert removed >= 1
        assert not os.path.exists(old.name), "stale orphan was not swept"
        assert os.path.exists(fresh.name), "in-flight upload was swept"
    finally:
        unlink_quietly(old.name)
        unlink_quietly(fresh.name)
