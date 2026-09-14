"""Level 1: features, fusion, policy and the exit gate.

The fusion and policy tests are the load-bearing ones. They pin behaviour that
is easy to "simplify" into something that looks equivalent and is not - most
importantly that fusion is never an OR gate over the two models, and that a
confident `human-made` never ends a request.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from labs.core.config import ScreenConfig
from labs.screen import ensemble, features, policy
from labs.screen.models import ModelScore
from tests.conftest import requires_screen_models


@pytest.fixture()
def cfg() -> ScreenConfig:
    return ScreenConfig()


def _score(name: str, p, available: bool = True) -> ModelScore:
    return ModelScore(available=available, name=name, probability=p)


# ---------------------------------------------------------------- features --
class TestFakeprint:
    def test_shape_matches_the_model_input(self, cfg):
        """3585 is not a round number - it is (8192//2)+1 bins masked to
        1-8 kHz at 16 kHz. If this drifts, ORT gets a shape error or, worse,
        fit_features silently interpolates into the wrong space."""
        mask = features.frequency_mask(cfg)
        assert int(mask.sum()) == cfg.n_features

    def test_is_level_invariant_above_the_floor(self, cfg):
        """A gain change must not move the fakeprint. A detector that responds
        to volume is a loudness meter.

        "Above the floor" is load bearing - see the next test.
        """
        rng = np.random.default_rng(0)
        band = rng.random(cfg.n_features).astype(np.float32) * 30.0 + 10.0
        quiet = features.fakeprint_from_band(band, cfg)
        loud = features.fakeprint_from_band(band + 12.0, cfg)
        np.testing.assert_allclose(quiet, loud, atol=1e-5)

    def test_the_min_db_floor_breaks_invariance_for_quiet_content(self, cfg):
        """A genuine property of the trained transform, pinned deliberately.

        `hull = clip(hull, min_db, None)` is an ABSOLUTE -45 dB floor, so it is
        the one part of the chain that is not level-relative. Content near or
        below it changes shape under a gain change, because the gain moves the
        spectrum across the clip while the clip stays put.

        This is inherited from training and must not be "fixed": the weights
        were fitted against exactly this behaviour. It is recorded here so
        nobody later reads the invariance test above, assumes it holds
        everywhere, and removes the clip as redundant.
        """
        rng = np.random.default_rng(0)
        band = rng.random(cfg.n_features).astype(np.float32) * 40.0 - 50.0
        quiet = features.fakeprint_from_band(band, cfg)
        loud = features.fakeprint_from_band(band + 12.0, cfg)
        assert not np.allclose(quiet, loud, atol=1e-5)

    def test_output_is_bounded(self, cfg):
        rng = np.random.default_rng(1)
        band = rng.random(cfg.n_features).astype(np.float32) * 200.0 - 100.0
        out = features.fakeprint_from_band(band, cfg)
        assert out.min() >= 0.0
        assert out.max() <= 1.0 + 1e-6

    def test_silence_does_not_divide_by_zero(self, cfg):
        """Digital silence gives an all-zero residue; the epsilon has to hold."""
        out = features.fakeprint_from_band(
            np.full(cfg.n_features, -80.0, dtype=np.float32), cfg)
        assert np.all(np.isfinite(out))

    def test_fit_features_is_identity_at_the_right_width(self, cfg):
        vec = np.linspace(0, 1, cfg.n_features).astype(np.float32)
        assert features.fit_features(vec, cfg.n_features) is vec

    def test_fit_features_resamples_a_mismatch(self, cfg):
        vec = np.linspace(0, 1, 100).astype(np.float32)
        out = features.fit_features(vec, cfg.n_features)
        assert out.size == cfg.n_features


class TestWindowPlan:
    def test_skips_intro_and_outro_when_it_can_afford_to(self, cfg):
        """Intros and outros are commonly near-silence, a fade or a spoken tag,
        and were a measured source of outlier window scores."""
        n = int(180 * cfg.sample_rate)
        starts = features.plan_windows(n, cfg)
        assert starts[0] >= 5 * cfg.sample_rate
        last_end = starts[-1] + int(cfg.segment_seconds * cfg.sample_rate)
        assert last_end <= n - 5 * cfg.sample_rate + 1

    def test_uses_all_audio_when_the_track_is_short(self, cfg):
        """Trimming 10 s off a 12 s clip would leave nothing to analyse."""
        n = int(12 * cfg.sample_rate)
        starts = features.plan_windows(n, cfg)
        assert starts
        assert starts[0] == 0 or len(starts) == 1

    def test_returns_one_window_for_a_clip_shorter_than_a_window(self, cfg):
        starts = features.plan_windows(int(3 * cfg.sample_rate), cfg)
        assert starts == [0]

    def test_honours_the_window_count(self, cfg):
        n = int(240 * cfg.sample_rate)
        for want in (1, 4, 16, 40):
            c = dataclasses.replace(cfg, cnn_segments=want)
            assert len(features.plan_windows(n, c)) == want

    def test_windows_are_padded_to_a_fixed_length(self, cfg):
        """A ragged batch cannot be stacked for the CNN."""
        y = np.zeros(int(4 * cfg.sample_rate), dtype=np.float32)
        wins = features.windows(y, cfg)
        want = int(cfg.segment_seconds * cfg.sample_rate)
        assert all(w.size == want for w in wins)


# ---------------------------------------------------------------- fusion ----
class TestEnsemble:
    def test_agreement_on_ai_raises_confidence(self, cfg):
        r = ensemble.combine({"fakeprint": _score("fakeprint", 0.95),
                              "cepstrum": _score("cepstrum", 0.92)}, cfg)
        assert r.agreement == ensemble.AGREE_AI
        assert r.confidence_multiplier > 1.0
        assert r.probability >= 0.92

    def test_agreement_on_human_raises_confidence(self, cfg):
        r = ensemble.combine({"fakeprint": _score("fakeprint", 0.02),
                              "cepstrum": _score("cepstrum", 0.05)}, cfg)
        assert r.agreement == ensemble.AGREE_HUMAN
        assert r.confidence_multiplier > 1.0
        assert r.probability <= 0.05

    def test_is_not_an_or_gate(self, cfg):
        """THE test for this module.

        One model at 0.99 and the other at 0.01 must NOT yield ~0.99. max()
        would, and that is how two detectors at 2% FPR each become an ensemble
        at ~4% while feeling like an improvement.
        """
        r = ensemble.combine({"fakeprint": _score("fakeprint", 0.99),
                              "cepstrum": _score("cepstrum", 0.01)}, cfg)
        assert r.agreement == ensemble.DISAGREE
        assert r.probability < 0.7
        assert r.confidence_multiplier < 1.0

    def test_disagreement_records_which_model_dissented(self, cfg):
        """Which model flagged says which failure mode you are in, and a
        single fused number throws that away."""
        cnn_only = ensemble.combine({"fakeprint": _score("fakeprint", 0.1),
                                     "cepstrum": _score("cepstrum", 0.9)}, cfg)
        reg_only = ensemble.combine({"fakeprint": _score("fakeprint", 0.9),
                                     "cepstrum": _score("cepstrum", 0.1)}, cfg)
        assert cnn_only.interpretation != reg_only.interpretation
        assert "PROCESSED" in cnn_only.note
        assert "HUMAN" in reg_only.note

    def test_single_model_is_marked_degraded(self, cfg):
        r = ensemble.combine(
            {"fakeprint": _score("fakeprint", 0.9),
             "cepstrum": _score("cepstrum", None, available=False)}, cfg)
        assert r.agreement == ensemble.SINGLE
        assert r.confidence_multiplier < 1.0
        assert r.probability == 0.9

    def test_no_models_is_unavailable_not_zero(self, cfg):
        """Reporting 0.0 here would read as a confident 'human'."""
        r = ensemble.combine(
            {"fakeprint": _score("fakeprint", None, available=False),
             "cepstrum": _score("cepstrum", None, available=False)}, cfg)
        assert not r.available
        assert r.probability is None

    def test_agreement_score_measures_distance(self, cfg):
        near = ensemble.combine({"fakeprint": _score("fakeprint", 0.90),
                                 "cepstrum": _score("cepstrum", 0.88)}, cfg)
        far = ensemble.combine({"fakeprint": _score("fakeprint", 0.99),
                                "cepstrum": _score("cepstrum", 0.01)}, cfg)
        assert near.agreement_score > far.agreement_score


# ---------------------------------------------------------------- policy ----
class _C2PA:
    def __init__(self, declared=False, generator=None):
        self.ai_declared = declared
        self.generator = generator


class TestPolicy:
    def _decide(self, cfg, p_fp, p_cp, **kw):
        fused = ensemble.combine({"fakeprint": _score("fakeprint", p_fp),
                                  "cepstrum": _score("cepstrum", p_cp)}, cfg)
        return policy.decide(ensemble=fused, c2pa=kw.get("c2pa"),
                             container=None, bandwidth=None, cfg=cfg)

    def test_c2pa_declaration_is_decisive_and_free(self, cfg):
        d = policy.decide(ensemble=None, c2pa=_C2PA(True, "Suno"),
                          container=None, bandwidth=None, cfg=cfg)
        assert d.verdict == policy.VERDICT_AI
        assert d.next_step == policy.NEXT_RETURN
        assert d.decided_by == "c2pa"

    def test_confident_ai_may_exit_early(self, cfg):
        d = self._decide(cfg, 0.99, 0.98)
        assert d.verdict == policy.VERDICT_AI
        assert d.next_step == policy.NEXT_RETURN

    def test_confident_human_never_exits_early(self, cfg):
        """THE asymmetry.

        Both Level-1 models only recognise generators they were trained on, so
        their silence is not evidence. Level 1 must never publish an
        exoneration on its own, however confident it looks.
        """
        d = self._decide(cfg, 0.001, 0.001)
        assert d.verdict == policy.VERDICT_HUMAN
        assert d.confidence > 0.9
        assert d.next_step == policy.NEXT_ESCALATE

    def test_middle_band_is_inconclusive_not_rounded(self, cfg):
        """0.5 is not 'slightly AI'. It is no answer, and must say so."""
        d = self._decide(cfg, 0.5, 0.5)
        assert d.verdict == policy.VERDICT_INCONCLUSIVE
        assert d.review_recommended

    def test_disagreement_blocks_an_early_exit(self, cfg):
        """A veto must stop the shortcut even when the fused score is high."""
        d = self._decide(cfg, 0.99, 0.30)
        assert d.vetoes
        assert d.next_step == policy.NEXT_ESCALATE

    def test_unavailable_ensemble_escalates(self, cfg):
        d = policy.decide(ensemble=None, c2pa=None, container=None,
                          bandwidth=None, cfg=cfg)
        assert d.verdict == policy.VERDICT_UNAVAILABLE
        assert d.next_step == policy.NEXT_ESCALATE
        assert d.review_recommended

    def test_exit_can_be_disabled(self, cfg):
        """An operator who wants every request to reach Level 2 can say so."""
        off = dataclasses.replace(cfg, exit_on_ai=False)
        d = self._decide(off, 0.99, 0.98)
        assert d.verdict == policy.VERDICT_AI
        assert d.next_step == policy.NEXT_ESCALATE

    def test_thresholds_are_asymmetric_by_default(self, cfg):
        """Wrongly flagging a human is the expensive error, so the AI bar sits
        higher than the mirror of the human bar."""
        assert cfg.ai_threshold >= 0.8
        assert cfg.human_threshold <= 0.2
        assert cfg.ai_threshold > 1.0 - cfg.ai_threshold


# ------------------------------------------------------- real weights -------
@requires_screen_models
class TestAgainstRealModels:
    """End-to-end on synthetic audio, through the real ONNX graphs."""

    def test_screens_a_tone_without_raising(self, tone_file, cfg):
        from labs.screen import pipeline

        r = pipeline.run(tone_file, cfg)
        assert r.tier == "screen"
        assert r.verdict in (policy.VERDICT_AI, policy.VERDICT_HUMAN,
                             policy.VERDICT_INCONCLUSIVE,
                             policy.VERDICT_UNAVAILABLE)
        assert r.next_step in (policy.NEXT_RETURN, policy.NEXT_ESCALATE)
        assert r.elapsed_s > 0

    def test_both_models_produce_a_score(self, tone_file, cfg):
        from labs.screen import pipeline

        r = pipeline.run(tone_file, cfg)
        assert r.models["fakeprint"]["available"], r.models["fakeprint"]["error"]
        assert r.models["cepstrum"]["available"], r.models["cepstrum"]["error"]
        for name in ("fakeprint", "cepstrum"):
            p = r.models[name]["probability"]
            assert 0.0 <= p <= 1.0

    def test_is_deterministic(self, tone_file, cfg):
        """A forensic verdict that varies between identical requests is
        indefensible, so nothing in this path may be seeded randomly."""
        from labs.screen import pipeline

        a = pipeline.run(tone_file, cfg)
        b = pipeline.run(tone_file, cfg)
        assert a.probability == b.probability
        assert a.verdict == b.verdict

    def test_window_count_is_honoured_end_to_end(self, tone_file, cfg):
        from labs.screen import decode
        from labs.screen import models as screen_models

        audio = decode.load(tone_file, cfg)
        s = screen_models.score_cepstrum(audio.y, cfg, n_windows=4)
        assert s.available
        assert s.detail["n_windows"] == 4
        assert len(s.windows) == 4

    def test_missing_weights_degrade_rather_than_raise(self, tone_file, cfg):
        """An absent model file must be reported per-model, not crash the tier."""
        from labs.screen import models as screen_models
        from labs.screen import pipeline

        broken = dataclasses.replace(cfg, fakeprint_onnx="does-not-exist.onnx")
        screen_models.reset()
        try:
            r = pipeline.run(tone_file, broken)
            assert not r.models["fakeprint"]["available"]
            # The cepstrum model alone still answers, flagged as degraded.
            assert r.ensemble["agreement"] == ensemble.SINGLE
        finally:
            screen_models.reset()
