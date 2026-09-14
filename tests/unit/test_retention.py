"""Retention defaults to keeping everything.

The audio, the reports and the features derived from them are the dataset.
They are what deduplication reads, what an appeal is re-analysed from, and the
only corpus that exists if these models are ever retrained. Expiring them is a
policy decision somebody has to make deliberately, not a default that quietly
deletes the expensive artifact sixty seconds of CPU produced.

The mechanism is worth stating because it is not obvious: a MongoDB TTL index
ignores documents with no `expires_at` field. So the index can stay in place
permanently and simply never match, which means turning retention on later is
a config change rather than an online index build on a large collection.
"""
from __future__ import annotations

from datetime import datetime

from labs.services.storage import _expires_at, _with_expiry


class TestExpiryHelper:
    def test_zero_means_forever(self):
        assert _expires_at(0) is None

    def test_negative_means_forever(self):
        """A negative TTL is a misconfiguration. Treating it as "already
        expired" would delete every document on write."""
        assert _expires_at(-1) is None

    def test_none_means_forever(self):
        assert _expires_at(None) is None

    def test_a_positive_ttl_produces_a_future_date(self):
        at = _expires_at(3600)
        assert isinstance(at, datetime)
        assert at > datetime.now(tz=at.tzinfo)


class TestDocumentShaping:
    def test_no_expires_at_key_at_all_when_forever(self):
        """Absent, not null. A TTL index treats a null the same as a missing
        field today, but relying on that is relying on an implementation
        detail - and a null reads to a human as "expiry unknown"."""
        doc = _with_expiry({"_id": "a"}, 0)
        assert "expires_at" not in doc

    def test_expires_at_is_set_when_configured(self):
        doc = _with_expiry({"_id": "a"}, 60)
        assert isinstance(doc["expires_at"], datetime)

    def test_the_original_document_survives(self):
        doc = _with_expiry({"_id": "a", "result": {"verdict": "ai-generated"}}, 0)
        assert doc["_id"] == "a"
        assert doc["result"] == {"verdict": "ai-generated"}


class TestConfigDefaults:
    def test_analyses_are_kept_forever(self, monkeypatch):
        from labs.core.config import StorageConfig

        monkeypatch.delenv("LABS_RESULT_TTL_DAYS", raising=False)
        assert StorageConfig().result_ttl_days == 0

    def test_audio_is_kept_forever(self, monkeypatch):
        from labs.core.config import StorageConfig

        monkeypatch.delenv("LABS_AUDIO_TTL_DAYS", raising=False)
        assert StorageConfig().audio_ttl_days == 0

    def test_retained_screens_are_kept_forever(self, monkeypatch):
        from labs.core.config import ScreenConfig

        monkeypatch.delenv("LABS_SCREEN_RETAIN_S", raising=False)
        assert ScreenConfig().retain_seconds == 0

    def test_retention_can_still_be_turned_on(self, monkeypatch):
        """The lever has to work, or a deployment with a real deletion
        obligation cannot meet it."""
        from labs.core.config import StorageConfig

        monkeypatch.setenv("LABS_RESULT_TTL_DAYS", "90")
        assert StorageConfig().result_ttl_days == 90


class TestPersistedDocuments:
    """Against a real (mocked) Mongo, so the shape written is the shape read."""

    def test_a_screen_is_stored_without_an_expiry(self, mongo):
        mongo.save_screen("scr_keep", result={"verdict": "ai-generated"},
                          audio={"bucket": "b", "key": "k", "sha256": "d"},
                          meta={"filename": "t.mp3"}, ttl_seconds=0)

        doc = mongo.db().screens.find_one({"_id": "scr_keep"})
        assert doc is not None
        assert "expires_at" not in doc

    def test_a_screen_can_still_be_given_one(self, mongo):
        mongo.save_screen("scr_temp", result={"verdict": "ai-generated"},
                          audio=None, meta={}, ttl_seconds=3600)

        doc = mongo.db().screens.find_one({"_id": "scr_temp"})
        assert isinstance(doc["expires_at"], datetime)

    def test_a_retained_screen_is_still_readable(self, mongo):
        """The point of keeping it. An appeal arriving a month later must
        still find the verdict it is disputing."""
        mongo.save_screen("scr_old", result={"verdict": "ai-generated"},
                          audio={"bucket": "b", "key": "k", "sha256": "d"},
                          meta={}, ttl_seconds=0)

        assert mongo.get_screen("scr_old")["result"]["verdict"] == "ai-generated"

    def test_a_job_is_stored_without_an_expiry_when_told_to(self, mongo):
        mongo.create_job("job_keep", meta={"mode": "full"}, ttl_seconds=0)

        doc = mongo.db().jobs.find_one({"_id": "job_keep"})
        assert "expires_at" not in doc

    def test_an_analysis_is_stored_without_an_expiry(self, mongo):
        """The default path. A report is the expensive artifact - 60-90 s of
        CPU - and the only record of what the models said about a track."""
        assert mongo.cfg.result_ttl_days == 0

        mongo.save_analysis("an_1", report={"verdict": "human-made",
                                            "fake_probability": 0.02},
                            meta={"filename": "t.mp3"})

        doc = mongo.db().analyses.find_one({"_id": "an_1"})
        assert doc is not None
        assert "expires_at" not in doc


class TestIdempotencyIsStillBounded:
    def test_the_dedup_guard_keeps_its_expiry(self, mongo):
        """Deliberately NOT permanent. An idempotency key is a short-lived
        guard against a double-submit, not data - keeping it forever grows a
        collection without bound for no recoverable value."""
        mongo.claim_idempotency("key-abc", "an_1")

        doc = mongo.db().idempotency.find_one({"key": "key-abc"})
        assert isinstance(doc["expires_at"], datetime)
