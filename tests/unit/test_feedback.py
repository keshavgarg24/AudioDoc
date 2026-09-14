"""The human feedback loop: appeals and their outcomes.

The classification rules are the load-bearing part. An appeal is only worth
recording if `overturned` really means "the detector was wrong here" — so the
cases that must NOT be counted as an overturn are tested as carefully as the
one that must.
"""
from __future__ import annotations

import pytest

from labs.services import feedback
from labs.verdicts import VERDICT_AI, VERDICT_HUMAN, VERDICT_INCONCLUSIVE


class TestClassify:
    def test_agreement_is_upheld(self):
        assert feedback.classify(VERDICT_AI, VERDICT_AI) == feedback.UPHELD
        assert feedback.classify(VERDICT_HUMAN, VERDICT_HUMAN) == feedback.UPHELD

    def test_disagreement_is_overturned(self):
        """THE data point. A confirmed Level-1 false positive with the audio
        and the disputing human's reason attached."""
        assert feedback.classify(VERDICT_AI, VERDICT_HUMAN) == feedback.OVERTURNED
        assert feedback.classify(VERDICT_HUMAN, VERDICT_AI) == feedback.OVERTURNED

    def test_a_deep_inconclusive_is_not_an_overturn(self):
        """Level 2 landing in its own uncertainty band neither confirms nor
        clears the screen. Counting it as an overturn would inflate the
        measured error rate with tracks nobody has actually decided."""
        assert feedback.classify(
            VERDICT_AI, VERDICT_INCONCLUSIVE) == feedback.INCONCLUSIVE

    def test_a_missing_deep_verdict_is_not_an_overturn(self):
        assert feedback.classify(VERDICT_AI, None) == feedback.INCONCLUSIVE

    def test_a_non_verdict_screen_is_not_classifiable(self):
        """Only a decisive Level-1 verdict can be upheld or overturned; the
        other states never ended a request in the first place."""
        for screen in (VERDICT_INCONCLUSIVE, "unavailable", None):
            assert feedback.classify(
                screen, VERDICT_AI) == feedback.INCONCLUSIVE


class TestResolve:
    def test_writes_the_outcome_back(self, mongo):
        mongo.save_screen("scr_1", result={"verdict": VERDICT_AI},
                          audio=None, meta={}, ttl_seconds=600)
        mongo.save_feedback({"kind": "escalation", "screen_id": "scr_1",
                             "outcome": "pending"})

        out = feedback.resolve("scr_1", {"verdict": VERDICT_HUMAN,
                                         "raw_logit": -7.2})

        assert out == feedback.OVERTURNED
        doc = mongo.db().feedback.find_one({"screen_id": "scr_1"})
        assert doc["outcome"] == feedback.OVERTURNED
        assert doc["deep"]["verdict"] == VERDICT_HUMAN
        assert doc["deep"]["raw_logit"] == -7.2

    def test_upheld_when_the_deep_model_agrees(self, mongo):
        mongo.save_screen("scr_2", result={"verdict": VERDICT_AI},
                          audio=None, meta={}, ttl_seconds=600)
        mongo.save_feedback({"kind": "escalation", "screen_id": "scr_2",
                             "outcome": "pending"})

        assert feedback.resolve(
            "scr_2", {"verdict": VERDICT_AI}) == feedback.UPHELD

    def test_no_screen_id_is_a_no_op(self):
        assert feedback.resolve(None, {"verdict": VERDICT_AI}) is None

    def test_no_report_is_a_no_op(self):
        assert feedback.resolve("scr_x", None) is None

    def test_never_raises_when_storage_is_down(self, monkeypatch):
        """Called from a job's completion path. A bookkeeping failure must not
        turn a finished analysis into a failed one — the caller already has
        the result they asked for."""
        from labs.services import storage

        def _boom(*a, **kw):
            raise RuntimeError("mongo is gone")

        monkeypatch.setattr(storage, "get_mongo", _boom)
        assert feedback.resolve("scr_1", {"verdict": VERDICT_AI}) is None

    def test_unknown_screen_resolves_as_inconclusive(self, mongo):
        """A retention window that expired between the appeal and the deep
        result. There is nothing to compare against, so it is not an overturn.
        """
        mongo.save_feedback({"kind": "escalation", "screen_id": "scr_gone",
                             "outcome": "pending"})
        assert feedback.resolve(
            "scr_gone", {"verdict": VERDICT_AI}) == feedback.INCONCLUSIVE


class TestSummary:
    def _seed(self, mongo, rows):
        for outcome, n in rows.items():
            for _ in range(n):
                mongo.save_feedback({"kind": "escalation", "outcome": outcome})

    def test_overturn_rate_counts_only_decided_appeals(self, mongo):
        """Pending and inconclusive appeals are not evidence either way, so
        including them in the denominator would understate the error rate."""
        self._seed(mongo, {"overturned": 2, "upheld": 6,
                           "pending": 5, "inconclusive": 3})

        s = mongo.feedback_summary(days=30)

        assert s["decided"] == 8
        assert s["overturn_rate"] == pytest.approx(0.25)

    def test_rate_is_none_before_anything_is_decided(self, mongo):
        """A rate computed from zero samples is not a rate, and rendering it
        as 0% reads as "the detector is never wrong"."""
        self._seed(mongo, {"pending": 4})

        s = mongo.feedback_summary(days=30)

        assert s["decided"] == 0
        assert s["overturn_rate"] is None

    def test_disabled_storage_says_so(self):
        from labs.core.config import StorageConfig
        from labs.services.storage import MongoStore

        assert MongoStore(StorageConfig()).feedback_summary()["enabled"] is False


class TestRetention:
    def test_a_screen_round_trips(self, mongo):
        mongo.save_screen("scr_r", result={"verdict": VERDICT_AI},
                          audio={"bucket": "b", "key": "k", "sha256": "abc"},
                          meta={"filename": "t.mp3", "owner": "k1"},
                          ttl_seconds=600)

        doc = mongo.get_screen("scr_r")
        assert doc["result"]["verdict"] == VERDICT_AI
        assert doc["audio"]["key"] == "k"
        assert doc["filename"] == "t.mp3"
        assert doc["owner"] == "k1"
        assert doc["escalated"] is False
        assert doc["sha256"] == "abc"

    def test_marking_escalated_is_visible(self, mongo):
        """This is what makes a double-clicked "I disagree" button idempotent
        rather than two 60 s analyses of the same track."""
        mongo.save_screen("scr_e", result={}, audio=None, meta={},
                          ttl_seconds=600)
        mongo.mark_screen_escalated("scr_e", "job123")

        doc = mongo.get_screen("scr_e")
        assert doc["escalated"] is True
        assert doc["analysis_id"] == "job123"

    def test_an_expiry_is_always_set(self, mongo):
        """The TTL index is what stops an ungated endpoint accumulating
        strangers' audio indefinitely, and it only fires on documents that
        carry the field."""
        mongo.save_screen("scr_t", result={}, audio=None, meta={},
                          ttl_seconds=600)
        doc = mongo.get_screen("scr_t")
        assert doc["expires_at"] > doc["created_at"]

    def test_missing_screen_is_none_not_an_error(self, mongo):
        assert mongo.get_screen("scr_nope") is None
