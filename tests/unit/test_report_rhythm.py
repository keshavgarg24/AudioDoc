"""One response must not carry two different answers to "what tempo is this".

The report used to publish a top-level `rhythm.bpm` derived from the
detection segmenter's bar length, with no half/double-time correction, beside
a `musical.rhythm.bpm` that came from a real beat tracker with that
correction applied. On a 140 BPM track tracked at 70 the same JSON body said
both 71 and 140, and the prose summary quoted the 71.
"""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")  # analysis.report imports torch at module level

from labs.analysis.musical import notated_tempo
from labs.analysis.report import analyse_rhythm

# 140 BPM => one bar is 4 * 60/140 s, and a segment is four bars.
BAR_140 = 4 * 60.0 / 140.0
SEGMENT_140 = BAR_140 * 4
# A tracker locked to the half-time pulse, as it is on dense material.
TRACKED_HALF = {"bpm": 140, "bpm_tracked": 70, "beat_source": "beat_this"}


def _downbeats(n: int = 16, step: float = BAR_140) -> np.ndarray:
    return np.arange(n, dtype=float) * step


class TestNotatedTempo:
    def test_slow_pulse_is_doubled(self):
        bpm, level, _ = notated_tempo(70.0)
        assert bpm == 140.0
        assert level == "half-time"

    def test_fast_pulse_is_halved(self):
        bpm, level, _ = notated_tempo(200.0)
        assert bpm == 100.0
        assert level == "double-time"

    def test_ordinary_pulse_is_left_alone(self):
        bpm, level, _ = notated_tempo(128.0)
        assert bpm == 128.0
        assert level == "as tracked"

    @pytest.mark.parametrize("edge", [95.0, 190.0])
    def test_boundaries_are_not_rescaled(self, edge):
        """95 and 190 are inside the "as tracked" band, not on the wrong side."""
        bpm, level, _ = notated_tempo(edge)
        assert bpm == edge
        assert level == "as tracked"


class TestAnalyseRhythm:
    def test_tracker_tempo_wins_when_musical_analysis_ran(self):
        """This is the regression: the published tempo is the tracked one."""
        out = analyse_rhythm(_downbeats(), _downbeats(), SEGMENT_140,
                             musical_rhythm=TRACKED_HALF)
        assert out["bpm"] == 140
        assert out["bpm_source"] == "beat tracker"

    def test_grid_derivation_is_kept_for_audit(self):
        """The detection reasoning is derived from the grid, so it stays visible."""
        out = analyse_rhythm(_downbeats(), _downbeats(), SEGMENT_140,
                             musical_rhythm=TRACKED_HALF)
        assert out["grid_bpm"] == 140

    def test_grid_tempo_is_corrected_when_musical_analysis_is_absent(self):
        """A half-time grid must not be published raw as the tempo."""
        half_segment = SEGMENT_140 * 2  # grid lands on 70 BPM
        out = analyse_rhythm(_downbeats(step=BAR_140 * 2),
                             _downbeats(step=BAR_140 * 2), half_segment)
        assert out["grid_bpm"] == 70
        assert out["bpm"] == 140
        assert out["bpm_source"] == "detection grid"

    def test_missing_musical_bpm_falls_back_rather_than_crashing(self):
        out = analyse_rhythm(_downbeats(), _downbeats(), SEGMENT_140,
                             musical_rhythm={"bpm": None})
        assert out["bpm_source"] == "detection grid"
        assert out["bpm"] == 140

    def test_no_segment_length_yields_zero_not_an_exception(self):
        out = analyse_rhythm(np.array([]), np.array([]), 0.0)
        assert out["bpm"] == 0
        assert out["grid_bpm"] == 0

    def test_timing_statistics_still_describe_the_downbeats(self):
        """Tempo changed source; the regularity measures must not have."""
        jittered = _downbeats() + np.array([0, 0.01] * 8)
        out = analyse_rhythm(jittered, jittered, SEGMENT_140,
                             musical_rhythm=TRACKED_HALF)
        assert out["downbeats_detected"] == 16
        assert out["tempo_variation"] > 0
        assert 0.0 <= out["tempo_stability"] <= 1.0
