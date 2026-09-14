"""The Stage-1 window plan.

These tests exist because the bug they pin was silent. Upstream took the first
48 eligible downbeats; downbeats arrive every ~2.5 s and the windows are 10 s,
so on any track over ~145 s the plan ran out of slots before the song ended and
Stage-1 rendered a verdict on the opening two minutes. Nothing failed, nothing
logged, and the report still said coverage 1.0 because that field measured a
different pipeline.
"""
from __future__ import annotations

import pytest

from labs.analysis.audio import plan_starts


def _grid(n: int, step: float = 2.5) -> list:
    """`n` downbeats at a fixed spacing, like a real modal beat grid."""
    return [round(i * step, 3) for i in range(n)]


class TestSpread:
    def test_short_track_selects_everything(self):
        """Under the cap there is nothing to choose, so nothing is dropped."""
        eligible = _grid(20)
        idx, plan = plan_starts(eligible, max_segments=48)
        assert idx == list(range(20))
        assert plan["selected"] == 20

    def test_long_track_spans_the_whole_track(self):
        """The decisive property: the last window is near the END.

        With upstream's prefix strategy the 48th of 200 downbeats sits at
        index 47 of 200 - under a quarter of the way in. Spreading has to put
        the final selection in the last stride of the grid instead.
        """
        eligible = _grid(200)
        idx, plan = plan_starts(eligible, max_segments=48)

        assert len(idx) <= 48
        assert plan["strategy"] == "spread"
        # Endpoints included: the final selection IS the final downbeat.
        assert idx[0] == 0
        assert idx[-1] == len(eligible) - 1

    def test_upstream_prefix_is_still_reachable(self):
        """spread=False must reproduce the old behaviour exactly, for A/B work."""
        eligible = _grid(200)
        idx, plan = plan_starts(eligible, max_segments=48, spread=False)
        assert idx == list(range(48))
        assert plan["strategy"] == "upstream-prefix"

    def test_never_exceeds_the_cap(self):
        """Stage-2 was trained on exactly max_segments; more is out of
        distribution, not merely slower."""
        for n in (49, 97, 145, 1000):
            idx, _ = plan_starts(_grid(n), max_segments=48)
            assert len(idx) <= 48, f"{n} downbeats produced {len(idx)} windows"

    def test_the_budget_is_actually_spent(self):
        """REGRESSION.

        A constant integer step wastes the budget: for 54 downbeats into 48
        slots the step rounds to 2 and only 27 windows are selected. That is
        not a cosmetic loss - ai1.mp3 sits in the segment-count-sensitive band
        and flipped its verdict when run on 27 windows instead of 48.
        """
        for n in (49, 54, 70, 95, 100, 200):
            idx, plan = plan_starts(_grid(n), max_segments=48)
            assert len(idx) == 48, (
                f"{n} downbeats selected {len(idx)} windows, not 48")
            assert plan["selected"] == 48

    def test_indices_are_ascending(self):
        """Stage-2's SSM is order-sensitive; an unsorted plan is a different song."""
        idx, _ = plan_starts(_grid(137), max_segments=48)
        assert idx == sorted(idx)
        assert len(set(idx)) == len(idx)


class TestStride:
    def test_stride_multiplies_the_step(self):
        idx_1, _ = plan_starts(_grid(200), max_segments=48, stride=1)
        idx_2, plan_2 = plan_starts(_grid(200), max_segments=48, stride=2)
        assert plan_2["budget"] == 24
        assert len(idx_2) == 24
        assert len(idx_2) < len(idx_1)

    @pytest.mark.parametrize("bad", [0, -1, -5])
    def test_non_positive_stride_is_clamped(self, bad):
        """A stride of 0 would mean range(0, n, 0) -> ValueError."""
        idx, plan = plan_starts(_grid(50), max_segments=48, stride=bad)
        assert plan["extra_stride"] == 1
        assert idx


class TestDegenerate:
    def test_no_eligible_downbeats(self):
        """A clip shorter than one window has no usable downbeat at all."""
        idx, plan = plan_starts([], max_segments=48)
        assert idx == []
        assert plan["strategy"] == "none"

    def test_single_downbeat(self):
        idx, plan = plan_starts([0.0], max_segments=48)
        assert idx == [0]
        assert plan["selected"] == 1

    def test_cap_of_one(self):
        """max_segments=1 must yield exactly one window, not zero."""
        idx, _ = plan_starts(_grid(100), max_segments=1)
        assert len(idx) == 1
