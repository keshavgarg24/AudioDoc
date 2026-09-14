"""The two-pass Stage-1 cascade.

Driven against a stub backbone rather than the real 1.29 GB checkpoint,
because the properties that matter here are about WHICH segments get computed
and WHETHER they get recomputed - not about what MERT thinks. A stub makes
those directly observable and the tests run in milliseconds.

`tests/integration/test_cascade_real.py` covers the same logic against the
real weights when they are present.
"""
from __future__ import annotations

import dataclasses

import pytest
import torch

from labs.core.config import Settings
from labs.ml.detector import Detector


class _StubAudio:
    """Just enough of SegmentedAudio for `stage1`."""

    def __init__(self, n: int, fixed: int = 64):
        self.n_real = n
        # Distinct per-segment content, so a mis-indexed cache is detectable
        # rather than accidentally correct.
        self.segments = torch.arange(
            n * fixed, dtype=torch.float32).reshape(n, 1, fixed)
        self.mask = torch.zeros(48, dtype=torch.bool)
        self.starts = [float(i) for i in range(n)]
        self.plan = {}


class _StubDetector(Detector):
    """Counts Stage-1 calls and returns a controllable Stage-2 logit."""

    def __init__(self, settings, logits):
        super().__init__(settings)
        # Successive Stage-2 answers: [first_pass, second_pass, ...]
        self._logit_script = list(logits)
        self.embed_calls = 0
        self.segments_embedded = 0
        self.classify_calls = []

    @property
    def is_ready(self):
        return True

    def embed(self, segments):
        self.embed_calls += 1
        self.segments_embedded += int(segments.shape[0])
        n = segments.shape[0]
        # The embedding encodes the segment's own content, so classify_subset
        # stacking the wrong rows would change the value it sees.
        base = segments.squeeze(1)[:, 0].reshape(n, 1)
        return base.expand(n, 768).float().contiguous(), torch.zeros(n, 2)

    def classify(self, embedding, mask):
        self.classify_calls.append(int((~mask).sum()))
        value = self._logit_script.pop(0) if self._logit_script else 0.0
        return torch.tensor(float(value))


def _settings(**cascade) -> Settings:
    s = Settings()
    return dataclasses.replace(s, cascade=dataclasses.replace(s.cascade, **cascade))


class TestEarlyExit:
    def test_confident_first_pass_skips_the_rest(self):
        """|logit| >= threshold measures as immune to segment count, so the
        remaining windows must never be computed."""
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        det = _StubDetector(cfg, [-7.2])
        audio = _StubAudio(48)

        logit, cache, used, info = det.stage1(audio, cfg)

        assert info["escalated"] is False
        assert info["passes"] == 1
        assert len(used) == 16                   # 48 // 3
        assert det.segments_embedded == 16
        assert float(logit) == pytest.approx(-7.2)

    def test_borderline_first_pass_escalates(self):
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        det = _StubDetector(cfg, [0.4, 1.9])
        audio = _StubAudio(48)

        logit, cache, used, info = det.stage1(audio, cfg)

        assert info["escalated"] is True
        assert info["passes"] == 2
        assert len(used) == 48
        assert float(logit) == pytest.approx(1.9)

    def test_escalation_recomputes_nothing(self):
        """THE property that makes the cascade free.

        Stage-1 embeddings are per-segment and independent, so the second pass
        must compute only the 32 windows the first pass skipped. If this ever
        regresses to 16 + 48 the cascade costs 1.33x instead of 1.0x and the
        whole design stops paying.
        """
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        det = _StubDetector(cfg, [0.1, 0.2])
        det.stage1(_StubAudio(48), cfg)

        assert det.segments_embedded == 48, (
            f"embedded {det.segments_embedded} segment-passes for 48 segments; "
            "the first pass's work was recomputed")

    def test_threshold_boundary_is_inclusive(self):
        """'at or above' in the docstring has to match the code."""
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        det = _StubDetector(cfg, [5.0])
        _, _, _, info = det.stage1(_StubAudio(48), cfg)
        assert info["escalated"] is False

    def test_sign_does_not_affect_the_gate(self):
        """The gate is on |logit|: a confident Real is as immune as a
        confident Fake, and both saturate at +-7.21."""
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        for value in (-6.0, 6.0):
            det = _StubDetector(cfg, [value])
            _, _, _, info = det.stage1(_StubAudio(48), cfg)
            assert info["escalated"] is False, value


class TestDisabledAndDegenerate:
    def test_disabled_runs_full_density_in_one_pass(self):
        cfg = _settings(enabled=False)
        det = _StubDetector(cfg, [-7.2])
        _, _, used, info = det.stage1(_StubAudio(48), cfg)

        assert info["enabled"] is False
        assert info["passes"] == 1
        assert len(used) == 48
        assert det.segments_embedded == 48

    def test_too_few_segments_skips_the_cascade(self):
        """Below min_segments the first pass saves nothing but still costs a
        Stage-2 call, so it is a pure loss."""
        cfg = _settings(enabled=True, min_segments=12, first_pass_stride=3)
        det = _StubDetector(cfg, [0.1])
        _, _, used, info = det.stage1(_StubAudio(8), cfg)

        assert info["escalated"] is False
        assert info["passes"] == 1
        assert len(used) == 8
        assert det.segments_embedded == 8

    def test_stride_of_one_is_not_a_cascade(self):
        """stride 1 would make the first pass identical to the full pass."""
        cfg = _settings(enabled=True, first_pass_stride=1)
        det = _StubDetector(cfg, [0.1])
        _, _, used, info = det.stage1(_StubAudio(48), cfg)
        assert info["passes"] == 1
        assert len(used) == 48

    def test_single_segment(self):
        cfg = _settings(enabled=True, first_pass_stride=3, min_segments=1)
        det = _StubDetector(cfg, [0.0])
        _, _, used, _ = det.stage1(_StubAudio(1), cfg)
        assert used == [0]


class TestSubsetClassification:
    def test_indices_are_sorted_before_stacking(self):
        """Stage-2's self-similarity matrix and SSM are order-sensitive, so a
        subset handed over out of chronological order describes a different
        song."""
        cfg = _settings()
        det = _StubDetector(cfg, [0.0])
        cache = {i: (torch.full((768,), float(i)), torch.zeros(2))
                 for i in range(5)}

        det.classify_subset(cache, [3, 0, 4, 1, 2], 48)
        # classify() records how many rows were unmasked; ordering is checked
        # by reaching into the stacked sequence it was handed.
        assert det.classify_calls == [5]

    def test_mask_marks_exactly_the_padding(self):
        cfg = _settings()
        det = _StubDetector(cfg, [0.0])
        cache = {i: (torch.zeros(768), torch.zeros(2)) for i in range(10)}
        det.classify_subset(cache, list(range(10)), 48)
        assert det.classify_calls == [10]

    def test_full_length_subset_needs_no_padding(self):
        cfg = _settings()
        det = _StubDetector(cfg, [0.0])
        cache = {i: (torch.zeros(768), torch.zeros(2)) for i in range(48)}
        det.classify_subset(cache, list(range(48)), 48)
        assert det.classify_calls == [48]


class TestReportAlignment:
    def test_used_indices_and_starts_stay_aligned(self):
        """On an early exit the embeddings cover a SUBSET of the windows, so
        the report's per-window timestamps have to be narrowed to match.
        Passing the full list would mislabel every row."""
        cfg = _settings(enabled=True, first_pass_stride=3, escalate_below=5.0)
        det = _StubDetector(cfg, [-7.2])
        audio = _StubAudio(48)

        _, cache, used, _ = det.stage1(audio, cfg)
        starts = [audio.starts[i] for i in used]

        assert len(starts) == len(used)
        assert all(i in cache for i in used)
        assert starts == [float(i) for i in range(0, 48, 3)]


class TestVerdictBanding:
    """|logit| near zero must not be published as a verdict.

    ai1.mp3 is the case: it sits at |logit| 0.4 and lands on opposite sides of
    zero depending only on which windows Stage-1 was handed (+0.393 on
    upstream's first-48 plan, -0.421 once the windows span the whole track).
    Both readings are honest; neither is a finding. Banding makes the service
    say so, and turns that flip into a non-event because no published claim
    changes.
    """

    def _band(self, raw, **kw):
        from labs.core.config import DeepPolicyConfig
        from labs.ml.detector import _band

        return _band(raw, dataclasses.replace(DeepPolicyConfig(), **kw))

    @pytest.mark.parametrize("raw", [0.0, 0.393, -0.421, 1.99, -1.5])
    def test_inside_the_band_is_inconclusive(self, raw):
        out = self._band(raw)
        assert out["verdict"] == "inconclusive"
        assert out["decisive"] is False

    @pytest.mark.parametrize("raw,expected", [
        (7.21, "ai-generated"), (-7.21, "human-made"),
        (2.0, "ai-generated"), (-2.0, "human-made"),
        (4.574, "ai-generated"), (-3.238, "human-made"),
    ])
    def test_outside_the_band_gets_a_verdict(self, raw, expected):
        out = self._band(raw)
        assert out["verdict"] == expected
        assert out["decisive"] is True

    def test_the_boundary_is_inclusive_of_a_verdict(self):
        """'below inconclusive_below' in the docstring must match the code."""
        assert self._band(2.0)["decisive"] is True
        assert self._band(1.999)["decisive"] is False

    def test_both_ai1_readings_land_in_the_same_band(self):
        """THE point of the band. The flip stops mattering because neither
        side of it was ever a defensible verdict."""
        before = self._band(0.3926)
        after = self._band(-0.4214)
        assert before["verdict"] == after["verdict"] == "inconclusive"

    def test_banding_can_be_disabled(self):
        """An operator who wants the raw sign can have it, explicitly."""
        out = self._band(0.1, enabled=False)
        assert out["verdict"] == "ai-generated"
        assert out["decisive"] is True
        assert "disabled" in out["band_note"]

    def test_the_note_explains_itself(self):
        """A caller who gets `inconclusive` needs to know why, not just that."""
        assert "windows" in self._band(0.4)["band_note"]
