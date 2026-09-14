"""Checkpoint digest pinning.

Pinning a revision says what we ASKED FOR. This says what we GOT, and they
cover different failures: a revision cannot detect a corrupted download, a
truncated S3 copy, a stale file on a reused volume, or a mirror bucket
somebody else can write to.

The failure being defended against is not a crash — it is a different model
answering confidently in your name.
"""
from __future__ import annotations

import hashlib

import pytest

from labs.ml.checkpoints import (
    CheckpointDigestError,
    file_sha256,
    verify_digest,
)


@pytest.fixture()
def ckpt(tmp_path):
    p = tmp_path / "Stage-1.ckpt"
    p.write_bytes(b"pretend weights" * 1000)
    return str(p)


@pytest.fixture()
def digest(ckpt):
    return hashlib.sha256(open(ckpt, "rb").read()).hexdigest()


class TestHashing:
    def test_matches_hashlib(self, ckpt, digest):
        assert file_sha256(ckpt) == digest

    def test_chunking_does_not_change_the_result(self, ckpt, digest):
        """It reads in 1 MB blocks so a 1.29 GB checkpoint does not have to
        fit in memory; the boundary must not affect the digest."""
        assert file_sha256(ckpt, chunk=7) == digest


class TestVerification:
    def test_a_match_reports_ok(self, ckpt, digest):
        out = verify_digest(ckpt, digest, "Stage-1", enforce=True)
        assert out["ok"] is True
        assert out["actual"] == digest

    def test_a_mismatch_raises_when_enforced(self, ckpt):
        """A checkpoint that is not the one you pinned is not a degraded
        service, it is a different model."""
        with pytest.raises(CheckpointDigestError, match="digest mismatch"):
            verify_digest(ckpt, "0" * 64, "Stage-1", enforce=True)

    def test_a_mismatch_is_survivable_when_not_enforced(self, ckpt):
        out = verify_digest(ckpt, "0" * 64, "Stage-1", enforce=False)
        assert out["ok"] is False
        assert out["actual"] != out["expected"]

    def test_no_pin_means_no_check(self, ckpt):
        """Unset must not become a hash of empty string, or every unpinned
        deployment fails to start."""
        assert verify_digest(ckpt, None, "Stage-1", enforce=True) is None
        assert verify_digest(ckpt, "", "Stage-1", enforce=True) is None

    def test_the_error_names_the_stage_and_the_path(self, ckpt):
        """An operator reading this at 3am needs to know WHICH checkpoint."""
        with pytest.raises(CheckpointDigestError) as exc:
            verify_digest(ckpt, "0" * 64, "Stage-2", enforce=True)
        assert "Stage-2" in str(exc.value)
        assert ckpt in str(exc.value)

    def test_the_error_does_not_print_whole_digests(self, ckpt, digest):
        """Truncated on both sides. A full 64-char pair in a log line is
        noise, and the first 16 characters already identify a mismatch."""
        with pytest.raises(CheckpointDigestError) as exc:
            verify_digest(ckpt, "0" * 64, "Stage-1", enforce=True)
        assert digest not in str(exc.value)


class TestConfig:
    def test_digests_default_to_unset(self, monkeypatch):
        from labs.core.config import CheckpointConfig

        monkeypatch.delenv("LABS_STAGE1_SHA256", raising=False)
        monkeypatch.delenv("LABS_STAGE2_SHA256", raising=False)
        cfg = CheckpointConfig()
        assert cfg.stage1_sha256 is None
        assert cfg.stage2_sha256 is None

    def test_enforcement_is_on_by_default(self, monkeypatch):
        from labs.core.config import CheckpointConfig

        monkeypatch.delenv("LABS_ENFORCE_CKPT_DIGEST", raising=False)
        assert CheckpointConfig().enforce_digest is True

    def test_digests_are_normalised(self, monkeypatch):
        """`shasum` output pasted with whitespace or in upper case must still
        match a lower-case hexdigest."""
        from labs.core.config import CheckpointConfig

        monkeypatch.setenv("LABS_STAGE1_SHA256", "  ABCDEF0123  ")
        assert CheckpointConfig().stage1_sha256 == "abcdef0123"

    def test_blank_is_treated_as_unset(self, monkeypatch):
        """An empty env var is how a template renders "not configured", and
        it must not become a digest that can never match."""
        from labs.core.config import CheckpointConfig

        monkeypatch.setenv("LABS_STAGE1_SHA256", "   ")
        assert CheckpointConfig().stage1_sha256 is None
