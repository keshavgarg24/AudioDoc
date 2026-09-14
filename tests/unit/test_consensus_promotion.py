"""Verification priority, and whether it actually reaches the caller.

`build_consensus` has always treated verification as authoritative. The bug
this covers is that the conclusion lived only inside `detection.consensus`
while the top-level `prediction` still carried the local model's answer — and
every caller reads the top-level field, so the override was real in the report
and invisible in practice.
"""
from __future__ import annotations

import pytest

from labs.api.v1.analyses import _promote
from labs.services.consensus import build_consensus
from labs.verdicts import VERDICT_AI, VERDICT_HUMAN


def _report(prediction="Real", verdict=VERDICT_HUMAN, logit=-7.0):
    return {"prediction": prediction, "verdict": verdict,
            "fake_probability": 0.01, "raw_logit": logit,
            "reliability": {"score": 90}}


def _verification(prediction):
    return {"prediction": prediction, "ai_probability": 91.0}


def _apply(report, verification):
    decision = {"escalate": True, "reason": "requested", "detail": ""}
    consensus = build_consensus(report, verification, decision)
    _promote(report, consensus, verification)
    return report, consensus


class TestPriority:
    def test_verification_ai_overrides_a_local_human(self):
        """THE case. A catalogue match is stronger evidence than statistical
        inference, so it wins - and the caller has to see that in the field
        they actually read."""
        report, consensus = _apply(_report(), _verification("ai_generated"))

        assert consensus["verdict"] == "ai_generated"
        assert report["prediction"] == "Fake"
        assert report["verdict"] == VERDICT_AI
        assert report["verdict_source"] == "verification"

    def test_verification_human_overrides_a_local_ai(self):
        report, _ = _apply(
            _report(prediction="Fake", verdict=VERDICT_AI, logit=7.0),
            _verification("human"))

        assert report["prediction"] == "Real"
        assert report["verdict"] == VERDICT_HUMAN

    def test_an_override_is_recorded_not_silent(self):
        """A verdict that changed under the caller's feet, with no trace of
        what changed it, is not something anyone can debug or dispute."""
        report, _ = _apply(_report(), _verification("ai_generated"))

        changed = report["verdict_changed"]
        assert changed["from"] == VERDICT_HUMAN
        assert changed["to"] == VERDICT_AI
        assert changed["by"] == "verification"
        assert changed["note"]

    def test_agreement_does_not_record_a_change(self):
        report, _ = _apply(
            _report(prediction="Fake", verdict=VERDICT_AI, logit=7.0),
            _verification("ai_generated"))

        assert report["verdict"] == VERDICT_AI
        assert "verdict_changed" not in report

    def test_the_primary_result_survives_verbatim(self):
        """Overriding the headline must not destroy the evidence behind it."""
        report = _report()
        original_logit = report["raw_logit"]
        _apply(report, _verification("ai_generated"))

        assert report["raw_logit"] == original_logit
        assert report["fake_probability"] == 0.01


class TestRestraint:
    def test_no_verification_leaves_the_verdict_alone(self):
        report = _report()
        _apply(report, None)

        assert report["prediction"] == "Real"
        assert "verdict_source" not in report

    def test_promotion_can_be_disabled(self, monkeypatch):
        """An integration that has already built around the local verdict can
        keep it, and read the override out of detection.consensus."""
        import dataclasses

        from labs.api.v1 import analyses

        off = dataclasses.replace(
            analyses.settings,
            server=dataclasses.replace(analyses.settings.server,
                                       promote_consensus=False))
        monkeypatch.setattr(analyses, "settings", off)

        report = _report()
        _apply(report, _verification("ai_generated"))
        assert report["prediction"] == "Real"

    def test_an_unusable_consensus_verdict_is_ignored(self):
        """"unknown" is not a verdict, and writing it over a real one would
        lose information rather than correct it."""
        report = _report()
        _promote(report, {"verdict": "unknown"}, _verification("ai_generated"))
        assert report["prediction"] == "Real"


class TestVerifyDefault:
    def test_the_default_is_configurable(self, monkeypatch):
        """"never" leaves a configured provider entirely unused, which is the
        wrong default for a deployment that pays for one."""
        from labs.core.config import ServerConfig

        monkeypatch.setenv("LABS_VERIFY_DEFAULT", "auto")
        assert ServerConfig().verify_default == "auto"

    def test_it_defaults_to_never_for_compatibility(self, monkeypatch):
        from labs.core.config import ServerConfig

        monkeypatch.delenv("LABS_VERIFY_DEFAULT", raising=False)
        assert ServerConfig().verify_default == "never"


class TestAcrThreshold:
    def test_the_threshold_is_acrclouds_documented_default(self):
        """Unconfigured, the service must behave exactly as their docs say."""
        import importlib

        from labs.services import acrcloud

        importlib.reload(acrcloud)
        assert acrcloud.ACR_THRESHOLD == pytest.approx(50.0)

    def test_it_can_be_overridden(self, monkeypatch):
        import importlib

        from labs.services import acrcloud

        monkeypatch.setenv("LABS_ACR_THRESHOLD", "75")
        importlib.reload(acrcloud)
        try:
            assert acrcloud.ACR_THRESHOLD == pytest.approx(75.0)
        finally:
            monkeypatch.delenv("LABS_ACR_THRESHOLD", raising=False)
            importlib.reload(acrcloud)
