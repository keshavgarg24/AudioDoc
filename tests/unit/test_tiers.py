"""Tier routing: which levels run, when a request stops early, and why.

The routing rules are a product contract as much as an engineering one - which
mode is free, which is billable, and which is allowed to skip the expensive
model - so they are pinned here rather than left to be re-derived from the
orchestrator.
"""
from __future__ import annotations

import dataclasses

import pytest

from labs import tiers
from labs.core.config import Settings
from labs.screen import policy
from labs.screen.pipeline import ScreenResult


def _screen(verdict: str, next_step: str, prob: float = 0.9,
            confidence: float = 0.95) -> ScreenResult:
    return ScreenResult(
        tier="screen", verdict=verdict, probability=prob,
        confidence=confidence, next_step=next_step, duration_s=120.0,
        elapsed_s=1.4, models={"fakeprint": {}, "cepstrum": {}},
        decision={"reasons": ["r"], "vetoes": [], "decided_by": "policy",
                  "review_recommended": False, "caveat": "c"})


class _FakeDetector:
    """Records what it was asked for, so skipping it is observable."""

    def __init__(self):
        self.calls = []

    def predict(self, path, mode="ai", display_name=None, target_genre=None):
        self.calls.append(mode)
        # `verdict` is the banded field the orchestrator compares on;
        # `prediction` is the raw sign of the logit, kept for older callers.
        return {"prediction": "Fake", "verdict": "ai-generated",
                "decisive": True, "fake_probability": 0.97,
                "filename": display_name or "x", "mode": mode}


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def patched(monkeypatch):
    """Replace Level 1 with a scripted result."""
    def _apply(result):
        from labs.screen import pipeline

        monkeypatch.setattr(pipeline, "run",
                            lambda path, cfg, progress=None: result)
    return _apply


class TestModeScreen:
    def test_returns_level_1_only(self, settings, patched):
        patched(_screen(policy.VERDICT_AI, policy.NEXT_RETURN))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="screen", settings=settings,
                            detector=det)

        assert out["tier"] == "screen"
        assert out["levels_run"] == [tiers.LEVEL_1]
        assert det.calls == [], "mode=screen must never touch the backbone"
        assert out["detection"]["level_2"] is None

    def test_maps_verdict_to_the_shared_prediction_field(self, settings, patched):
        """A frontend should not have to branch on which tier answered."""
        patched(_screen(policy.VERDICT_AI, policy.NEXT_RETURN))
        out = tiers.analyse("f.mp3", mode="screen", settings=settings,
                            detector=_FakeDetector())
        assert out["prediction"] == "Fake"

        patched(_screen(policy.VERDICT_HUMAN, policy.NEXT_ESCALATE, prob=0.01))
        out = tiers.analyse("f.mp3", mode="screen", settings=settings,
                            detector=_FakeDetector())
        assert out["prediction"] == "Real"

    def test_inconclusive_has_no_prediction(self, settings, patched):
        """None is the honest answer; "Real" would be an exoneration."""
        patched(_screen(policy.VERDICT_INCONCLUSIVE, policy.NEXT_ESCALATE,
                        prob=0.5, confidence=0.1))
        out = tiers.analyse("f.mp3", mode="screen", settings=settings,
                            detector=_FakeDetector())
        assert out["prediction"] is None

    def test_escalation_is_advertised(self, settings, patched):
        patched(_screen(policy.VERDICT_HUMAN, policy.NEXT_ESCALATE, prob=0.01))
        out = tiers.analyse("f.mp3", mode="screen", settings=settings,
                            detector=_FakeDetector())
        assert out["next_step"] == policy.NEXT_ESCALATE
        assert "exoneration" in out["escalation_note"]


class TestModeAi:
    def test_decisive_level_1_skips_the_backbone(self, settings, patched):
        """The entire economic argument for the tier."""
        patched(_screen(policy.VERDICT_AI, policy.NEXT_RETURN))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="ai", settings=settings,
                            detector=det)

        assert det.calls == []
        assert out["early_exit"]["exited_at"] == tiers.LEVEL_1
        assert out["levels_run"] == [tiers.LEVEL_1]

    def test_escalating_level_1_runs_the_backbone(self, settings, patched):
        patched(_screen(policy.VERDICT_INCONCLUSIVE, policy.NEXT_ESCALATE,
                        prob=0.5, confidence=0.1))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="ai", settings=settings,
                            detector=det)

        assert det.calls == ["ai"]
        assert out["levels_run"] == [tiers.LEVEL_1, tiers.LEVEL_2]
        assert out["detection"]["level_1"]["verdict"] == policy.VERDICT_INCONCLUSIVE

    def test_confident_human_still_runs_the_backbone(self, settings, patched):
        """Level 1 never publishes an exoneration on its own."""
        patched(_screen(policy.VERDICT_HUMAN, policy.NEXT_ESCALATE, prob=0.001))
        det = _FakeDetector()
        tiers.analyse("f.mp3", mode="ai", settings=settings, detector=det)
        assert det.calls == ["ai"]


class TestModeFull:
    def test_never_short_circuits(self, settings, patched):
        """A full report is bought for its evidence. Omitting the deep layers
        because a cheap model was confident delivers a thinner document than
        the one requested."""
        patched(_screen(policy.VERDICT_AI, policy.NEXT_RETURN))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="full", settings=settings,
                            detector=det)

        assert det.calls == ["full"]
        assert out["levels_run"] == [tiers.LEVEL_1, tiers.LEVEL_2]
        assert "early_exit" not in out


class TestModeAudio:
    def test_skips_level_1_entirely(self, settings, patched):
        """mode=audio asks for production analysis, not a verdict, so a
        detector opinion would answer a different question."""
        patched(_screen(policy.VERDICT_AI, policy.NEXT_RETURN))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="audio", settings=settings,
                            detector=det)

        assert det.calls == ["audio"]
        assert out["levels_run"] == []
        assert "level_1" not in out.get("detection", {})


class TestDisabledScreen:
    def test_ai_falls_through_to_level_2(self, settings, patched):
        off = dataclasses.replace(
            settings, screen=dataclasses.replace(settings.screen, enabled=False))
        det = _FakeDetector()

        out = tiers.analyse("f.mp3", mode="ai", settings=off, detector=det)

        assert det.calls == ["ai"]
        assert out["levels_run"] == [tiers.LEVEL_2]

    def test_screen_mode_raises_rather_than_lying(self, settings):
        """An empty result would read as "no artifacts found"."""
        off = dataclasses.replace(
            settings, screen=dataclasses.replace(settings.screen, enabled=False))
        with pytest.raises(RuntimeError, match="disabled"):
            tiers.analyse("f.mp3", mode="screen", settings=off,
                          detector=_FakeDetector())


class TestValidation:
    @pytest.mark.parametrize("bad", ["deep", "AI", "", "verdict", None])
    def test_unknown_mode_is_rejected(self, settings, bad):
        with pytest.raises(ValueError, match="Unknown analysis mode"):
            tiers.analyse("f.mp3", mode=bad, settings=settings,
                          detector=_FakeDetector())

    def test_deep_modes_are_exactly_the_backbone_modes(self):
        """The auth rule reads this tuple, so it must not include the free
        tier - and must not include `audio` either. `audio` runs the DSP and
        musicological passes and never loads Stage-1 or Stage-2, so gating it
        behind the deep scope would charge for a tier it does not use."""
        assert set(tiers.DEEP_MODES) == {"ai", "full"}
        assert "screen" not in tiers.DEEP_MODES
        assert "audio" not in tiers.DEEP_MODES


class TestLevelAgreement:
    def _run(self, settings, patched, screen_verdict, deep_verdict):
        patched(_screen(screen_verdict, policy.NEXT_ESCALATE))

        class _Det(_FakeDetector):
            def predict(self, path, mode="ai", **kw):
                self.calls.append(mode)
                return {"verdict": deep_verdict,
                        "prediction": "Fake" if deep_verdict == "ai-generated"
                        else "Real",
                        "mode": mode}

        out = tiers.analyse("f.mp3", mode="ai", settings=settings,
                            detector=_Det())
        return out["detection"]["level_agreement"]

    def test_both_flag_is_agreement(self, settings, patched):
        a = self._run(settings, patched, policy.VERDICT_AI, "ai-generated")
        assert a["state"] == "agree"

    def test_both_clear_is_agreement(self, settings, patched):
        a = self._run(settings, patched, policy.VERDICT_HUMAN, "human-made")
        assert a["state"] == "agree"

    def test_deep_flags_and_screen_does_not(self, settings, patched):
        """The case the deep tier exists for: a generator whose signal chain
        leaves no artifact the small models know."""
        a = self._run(settings, patched, policy.VERDICT_HUMAN, "ai-generated")
        assert a["state"] == "disagree"
        assert "no artifact" in a["note"]

    def test_screen_flags_and_deep_does_not(self, settings, patched):
        a = self._run(settings, patched, policy.VERDICT_AI, "human-made")
        assert a["state"] == "disagree"
        assert "processed human track" in a["note"].lower()

    def test_level_1_inconclusive_is_not_a_disagreement(self, settings, patched):
        """Most traffic lands here, and it is not a conflict."""
        a = self._run(settings, patched, policy.VERDICT_INCONCLUSIVE,
                      "ai-generated")
        assert a["state"] == "level-1-inconclusive"

    def test_level_2_inconclusive_is_not_a_disagreement(self, settings, patched):
        """The ai1.mp3 case. A deep logit inside its band is not a position,
        so comparing against it would manufacture a conflict out of silence."""
        a = self._run(settings, patched, policy.VERDICT_AI, "inconclusive")
        assert a["state"] == "level-2-inconclusive"

    def test_disagreement_is_recorded_not_resolved(self, settings, patched):
        """No field here collapses the two levels into one number: the levels
        read different things, so a disagreement is a fact about the track.

        `labels` reports each tier's own side of 0.5 and whether they match. It
        is a comparison, not a fusion - there is deliberately no combined score
        or tie-break anywhere in this block, and that absence is the test.
        """
        a = self._run(settings, patched, policy.VERDICT_AI, "human-made")
        assert set(a) == {"state", "note", "labels"}
        assert set(a["labels"]) == {"level_1", "level_2", "match", "note"}
        # Nothing here may be a fused verdict, score or probability.
        assert not {"score", "probability", "fused", "combined",
                    "resolved"} & set(a)
        assert not {"score", "probability", "fused"} & set(a["labels"])
