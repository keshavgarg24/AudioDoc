"""The cross-tier decision envelope, and the two bugs it was built over.

Three properties are load-bearing here and each has a test that fails loudly if
someone "simplifies" it away:

  1. `label` is total. Every scored track gets a side of 0.5, always.
  2. `verdict` never contradicts its own confidence gate. This is the ai1.mp3
     bug: a verdict published at confidence 0.231 against a bar of 0.45.
  3. The Level-1 fused score has no step in it. It used to jump 0.048 at the
     agreement band edge for a one-thousandth change in detector output.
"""
from __future__ import annotations

import dataclasses

import pytest

from labs.assessment import DECISION_BOUNDARY, band_for, build, confidence_for, label_for
from labs.core.config import ScreenConfig
from labs.screen import ensemble, policy
from labs.screen.models import ModelScore
from labs.verdicts import (
    BAND_LIKELY_AI,
    BAND_LIKELY_HUMAN,
    BAND_STRONG_AI,
    BAND_STRONG_HUMAN,
    BAND_UNCERTAIN,
    VERDICT_AI,
    VERDICT_HUMAN,
    VERDICT_INCONCLUSIVE,
)


@pytest.fixture()
def cfg() -> ScreenConfig:
    return ScreenConfig()


def _score(name: str, p, available: bool = True) -> ModelScore:
    return ModelScore(available=available, name=name, probability=p)


L1_BOUNDS = {"ai_decisive": 0.80, "human_decisive": 0.20,
             "uncertain_margin": 0.15}


# ------------------------------------------------------------------ label ---
class TestLabel:
    def test_label_is_total_over_every_score(self):
        """The whole point of `label`: it never abstains.

        A caller running a bulk triage queue with its own review step needs a
        side for every row. `verdict` is allowed to say `inconclusive`;
        `label` is not.
        """
        for i in range(0, 101):
            s = i / 100.0
            assert label_for(s) in (VERDICT_AI, VERDICT_HUMAN)

    def test_the_boundary_belongs_to_ai(self):
        """Exactly 0.5 has to go somewhere, and the choice must be documented
        rather than emergent. `>=` puts it on the AI side."""
        assert label_for(DECISION_BOUNDARY) == VERDICT_AI
        assert label_for(DECISION_BOUNDARY - 1e-9) == VERDICT_HUMAN

    def test_no_score_means_no_label(self):
        assert label_for(None) is None


# ------------------------------------------------------------- confidence ---
class TestConfidence:
    def test_the_boundary_scores_zero_not_half(self):
        """The bug this replaces: Level 2 reported max(p, 1-p), so a track it
        had no opinion about came back as '50% confident'."""
        assert confidence_for(0.5) == 0.0

    def test_saturation_scores_one(self):
        assert confidence_for(1.0) == 1.0
        assert confidence_for(0.0) == 1.0

    def test_is_symmetric_about_the_boundary(self):
        for d in (0.05, 0.2, 0.37, 0.5):
            assert confidence_for(0.5 + d) == pytest.approx(confidence_for(0.5 - d))

    def test_multiplier_cannot_push_past_one(self):
        assert confidence_for(0.99, multiplier=5.0) == 1.0

    def test_multiplier_cannot_go_negative(self):
        assert confidence_for(0.99, multiplier=-3.0) == 0.0


# ------------------------------------------------------------------- band ---
class TestBand:
    def test_grades_across_the_range(self):
        assert band_for(0.95, **L1_BOUNDS) == BAND_STRONG_AI
        assert band_for(0.70, **L1_BOUNDS) == BAND_LIKELY_AI
        assert band_for(0.50, **L1_BOUNDS) == BAND_UNCERTAIN
        assert band_for(0.30, **L1_BOUNDS) == BAND_LIKELY_HUMAN
        assert band_for(0.05, **L1_BOUNDS) == BAND_STRONG_HUMAN

    def test_uncertain_wins_over_strong(self):
        """If the margin is ever widened past the decisive threshold, the
        honest tag has to be the one that survives."""
        wide = {"ai_decisive": 0.55, "human_decisive": 0.45,
                "uncertain_margin": 0.3}
        assert band_for(0.60, **wide) == BAND_UNCERTAIN

    def test_band_tracks_the_tier_threshold_it_is_given(self):
        """Level 2's decisive point is 0.88, Level 1's is 0.80. The same score
        is therefore graded differently by each - which is correct, and is why
        the bound is an argument rather than a constant."""
        l2 = {"ai_decisive": 0.8808, "human_decisive": 0.1192,
              "uncertain_margin": 0.15}
        assert band_for(0.85, **L1_BOUNDS) == BAND_STRONG_AI
        assert band_for(0.85, **l2) == BAND_LIKELY_AI


# --------------------------------------------------------------- envelope ---
class TestEnvelope:
    def test_always_carries_the_full_shape(self):
        env = build(score=0.9, verdict=VERDICT_AI, confidence=0.8,
                    decided_by="test", **L1_BOUNDS)
        for key in ("score", "label", "verdict", "band", "confidence",
                    "margin", "threshold", "decided_by"):
            assert key in env, f"envelope is missing '{key}'"

    def test_shape_survives_a_null_score(self):
        """An unscored track must not make a caller branch on key presence."""
        env = build(score=None, verdict="unavailable", confidence=0.0,
                    decided_by="none", **L1_BOUNDS)
        assert env["score"] is None
        assert env["label"] is None
        assert env["band"] == BAND_UNCERTAIN

    def test_label_and_verdict_may_legitimately_differ(self):
        """The design, stated as a test: a track can score clearly on one side
        and still not clear the bar for a verdict."""
        env = build(score=0.8005, verdict=VERDICT_INCONCLUSIVE, confidence=0.6,
                    decided_by="level_2_deep", ai_decisive=0.8808,
                    human_decisive=0.1192, uncertain_margin=0.15)
        assert env["label"] == VERDICT_AI
        assert env["verdict"] == VERDICT_INCONCLUSIVE
        assert env["band"] == BAND_LIKELY_AI


# ------------------------------------------------------- the ai1.mp3 bug ----
class TestVerdictNeverOutrunsItsConfidence:
    def test_reband_demotes_a_verdict_that_lost_its_confidence(self, cfg):
        """THE regression test.

        The robustness gate runs after the policy and multiplies confidence
        down. Before `reband`, the verdict stayed put - ai1.mp3 shipped as
        `ai-generated` at confidence 0.231 against a `min_confidence` of 0.45.
        """
        d = policy.ScreenDecision(verdict=VERDICT_AI, probability=0.97,
                                  confidence=0.231)
        policy.reband(d, cfg)
        assert d.verdict == VERDICT_INCONCLUSIVE
        assert d.review_recommended
        assert any("confidence fell" in r for r in d.reasons)

    def test_reband_leaves_a_verdict_that_kept_its_confidence(self, cfg):
        d = policy.ScreenDecision(verdict=VERDICT_AI, probability=0.97,
                                  confidence=0.91)
        policy.reband(d, cfg)
        assert d.verdict == VERDICT_AI
        assert not d.reasons

    def test_reband_does_not_promote_an_inconclusive(self, cfg):
        """It is a demotion gate only. Re-running it must never manufacture a
        verdict out of one the policy declined to give."""
        d = policy.ScreenDecision(verdict=VERDICT_INCONCLUSIVE,
                                  probability=0.97, confidence=0.99)
        policy.reband(d, cfg)
        assert d.verdict == VERDICT_INCONCLUSIVE

    def test_reband_preserves_the_score(self, cfg):
        """The evidence does not change just because the bar was not cleared."""
        d = policy.ScreenDecision(verdict=VERDICT_AI, probability=0.97,
                                  confidence=0.1)
        policy.reband(d, cfg)
        assert d.probability == 0.97

    @pytest.mark.parametrize("p_fp,p_cp", [(0.99, 0.98), (0.001, 0.001),
                                           (0.5, 0.5), (0.85, 0.2),
                                           (0.6, 0.6), (0.4, 0.4)])
    def test_every_published_verdict_clears_its_own_gate(self, cfg, p_fp, p_cp):
        """The invariant the bug violated, checked across the whole surface."""
        fused = ensemble.combine({"fakeprint": _score("fakeprint", p_fp),
                                  "cepstrum": _score("cepstrum", p_cp)}, cfg)
        d = policy.decide(ensemble=fused, c2pa=None, container=None,
                          bandwidth=None, cfg=cfg)
        if d.verdict in (VERDICT_AI, VERDICT_HUMAN):
            assert d.confidence >= cfg.min_confidence, (
                f"{d.verdict} published at confidence {d.confidence} "
                f"against a bar of {cfg.min_confidence}")


# ------------------------------------------------ the fusion discontinuity ---
class TestFusedScoreIsContinuous:
    """The agreement bonus used to switch on, not ramp on.

    At high_band (0.6) the fused score jumped from 0.600 to 0.648 and the
    confidence multiplier from 0.55 to 1.15, for a one-thousandth change in
    detector output. Whichever side of that cliff a track landed on was a
    property of the band edge, not of the audio.
    """

    def _combine(self, cfg, p_fp, p_cp):
        return ensemble.combine({"fakeprint": _score("fakeprint", p_fp),
                                 "cepstrum": _score("cepstrum", p_cp)}, cfg)

    def test_no_step_at_the_ai_band_edge(self, cfg):
        hi = cfg.high_band
        inside = self._combine(cfg, hi, hi)
        outside = self._combine(cfg, hi, hi - 0.001)
        assert abs(inside.probability - outside.probability) < 0.01
        assert abs(inside.confidence_multiplier
                   - outside.confidence_multiplier) < 0.01

    def test_no_step_at_the_human_band_edge(self, cfg):
        lo = cfg.low_band
        inside = self._combine(cfg, lo, lo)
        outside = self._combine(cfg, lo, lo + 0.001)
        assert abs(inside.probability - outside.probability) < 0.01
        assert abs(inside.confidence_multiplier
                   - outside.confidence_multiplier) < 0.01

    def test_the_bonus_is_zero_exactly_at_the_edge(self, cfg):
        """At the edge the weaker detector is not yet evidence of anything, so
        the bonus it earns must be nothing."""
        r = self._combine(cfg, 0.9, cfg.high_band)
        assert r.agreement_strength == 0.0
        assert r.confidence_multiplier == pytest.approx(
            cfg.disagreement_confidence, abs=1e-4)

    def test_the_bonus_is_full_at_saturation(self, cfg):
        r = self._combine(cfg, 1.0, 1.0)
        assert r.agreement_strength == pytest.approx(1.0)
        assert r.confidence_multiplier == pytest.approx(
            cfg.agreement_confidence, abs=1e-4)

    def test_score_stays_monotone_across_the_edge(self, cfg):
        """Sweep both detectors together through the band edge; the fused score
        must never go backwards."""
        prev = -1.0
        p = 0.50
        while p <= 0.999:
            got = self._combine(cfg, p, p).probability
            assert got >= prev - 1e-9, f"fused score fell at p={p:.3f}"
            prev = got
            p += 0.001

    def test_the_bonus_is_still_applied(self, cfg):
        """Continuity is not an excuse to delete the behaviour. Two detectors
        deep in agreement must still sharpen the score."""
        agreed = self._combine(cfg, 0.95, 0.95)
        assert agreed.probability > 0.95

    def test_disagreement_region_is_unchanged(self, cfg):
        """The fix ramps the agreement bonus only. Inside the disagreement
        region the multiplier must still be the configured floor."""
        r = self._combine(cfg, 0.99, 0.30)
        assert r.agreement == ensemble.DISAGREE
        assert r.confidence_multiplier == pytest.approx(
            cfg.disagreement_confidence)


# ------------------------------------------------------ tier consistency ----
class TestBothTiersAgreeOnVocabulary:
    def test_level_1_publishes_an_envelope(self, cfg):
        """Whatever Level 1 concludes, the envelope is present and coherent."""
        fused = ensemble.combine({"fakeprint": _score("fakeprint", 0.95),
                                  "cepstrum": _score("cepstrum", 0.93)}, cfg)
        d = policy.decide(ensemble=fused, c2pa=None, container=None,
                          bandwidth=None, cfg=cfg)
        env = build(score=d.probability, verdict=d.verdict,
                    confidence=d.confidence, decided_by="level_1",
                    ai_decisive=cfg.ai_threshold,
                    human_decisive=cfg.human_threshold,
                    uncertain_margin=cfg.uncertain_margin)
        assert env["label"] == VERDICT_AI
        assert env["band"] == BAND_STRONG_AI
        assert env["threshold"] == 0.5

    def test_a_decisive_verdict_always_matches_its_label(self, cfg):
        """A published `ai-generated` verdict on a score below 0.5 would mean
        the band and the boundary had drifted apart."""
        for p in (0.999, 0.9, 0.85, 0.15, 0.05, 0.001):
            fused = ensemble.combine({"fakeprint": _score("fakeprint", p),
                                      "cepstrum": _score("cepstrum", p)}, cfg)
            d = policy.decide(ensemble=fused, c2pa=None, container=None,
                              bandwidth=None, cfg=cfg)
            if d.verdict in (VERDICT_AI, VERDICT_HUMAN):
                assert d.verdict == label_for(d.probability)

    def test_uncertain_margin_is_shared_by_both_tiers(self):
        """One legend has to fit both tiers, so the knob is one knob."""
        from labs.core.config import DeepPolicyConfig

        assert ScreenConfig().uncertain_margin == DeepPolicyConfig().uncertain_margin
