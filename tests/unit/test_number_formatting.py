"""Tempo is reported as a whole number; measured quantities keep decimals.

A BPM is read by a musician who will type it into a DAW. "128" answers that
question; "128.04" is the estimator's internal state leaking into the response,
and the extra digits are not precision - beat-tracking resolution over a
three-minute track is nowhere near a hundredth of a BPM.

The distinction being pinned here is between quantities that are COUNTED and
quantities that are MEASURED. LUFS, true peak and timing deviation are read
against real tolerances where a fraction changes the decision, so they keep
their decimals. Getting this backwards in either direction is the bug.
"""
from __future__ import annotations

from labs.analysis.musical import _i as musical_i
from labs.artist.insights import _i as insights_i
from labs.tools.base import _f, _i


class TestWholeNumbers:
    def test_rounds_to_the_nearest_integer(self):
        assert _i(128.04) == 128
        assert _i(127.6) == 128

    def test_returns_a_real_int_not_a_float(self):
        """128.0 still serialises as "128.0" in JSON, so the type matters and
        not just the value."""
        assert isinstance(_i(128.04), int)
        assert repr(_i(128.0)) == "128"

    def test_half_rounds_consistently(self):
        """Whatever the rule, it must not vary between calls - a tempo that
        renders differently on two requests for the same file reads as
        instability in the measurement."""
        assert _i(127.5) == _i(127.5)

    def test_non_finite_is_none_not_zero(self):
        """A missing tempo and a tempo of zero are different facts, and 0 BPM
        would be rendered as a real reading by anything downstream that only
        checks for None."""
        assert _i(float("nan")) is None
        assert _i(float("inf")) is None
        assert _i(None) is None
        assert _i("not a number") is None

    def test_every_implementation_agrees(self):
        """Three modules define this helper locally, following the existing
        per-module `_f` convention. They must not drift apart."""
        for value in (128.04, 127.6, 90.5, 174.49):
            assert _i(value) == musical_i(value) == insights_i(value)

    def test_all_implementations_reject_non_finite(self):
        for impl in (_i, musical_i, insights_i):
            assert impl(float("nan")) is None


class TestMeasuredQuantitiesKeepDecimals:
    def test_f_still_rounds_to_places(self):
        """LUFS, true peak and swing are read against real tolerances. A
        mastering engineer works to a tenth of a dB."""
        assert _f(-14.237, 2) == -14.24
        assert _f(0.5123, 3) == 0.512

    def test_f_is_unchanged_for_non_finite(self):
        assert _f(float("nan")) is None
        assert _f(float("inf")) is None


class TestTempoLabOutput:
    """The tool's own shaping, without decoding audio."""

    def test_alternatives_are_whole_numbers(self):
        from labs.tools.tempo_lab import _alternatives

        for alt in _alternatives(128.04):
            assert isinstance(alt["bpm"], int), alt

    def test_alternatives_still_suppress_implausible_readings(self):
        """Whole numbers must not change which readings are offered: a 170 BPM
        track still does not suggest 340."""
        from labs.tools.tempo_lab import _alternatives

        assert [a["bpm"] for a in _alternatives(170.0)] == [85, 170]

    def test_half_and_double_are_exact_for_an_even_tempo(self):
        """256 is dropped for being outside the plausible range, so this also
        pins that the range check still runs against the unrounded value."""
        from labs.tools.tempo_lab import _alternatives

        assert [a["bpm"] for a in _alternatives(128.0)] == [64, 128]

    def test_an_odd_half_time_reading_is_still_whole(self):
        """127 / 2 is 63.5, which is exactly the case that would otherwise
        reintroduce a decimal after the primary reading was rounded."""
        from labs.tools.tempo_lab import _alternatives

        for alt in _alternatives(127.0):
            assert isinstance(alt["bpm"], int), alt

    def test_no_alternatives_without_a_tempo(self):
        from labs.tools.tempo_lab import _alternatives

        assert _alternatives(None) == []
        assert _alternatives(0) == []

    def test_a_tempo_switch_reports_whole_numbers(self):
        from labs.tools.tempo_lab import _tempo_changes

        # A clean half-time switch partway through.
        curve = ([{"time": float(i), "bpm": 140.4} for i in range(12)]
                 + [{"time": float(i), "bpm": 70.2} for i in range(12, 24)])
        out = _tempo_changes(curve)

        assert out["available"] is True
        for switch in out.get("switches", []):
            assert isinstance(switch["from_bpm"], int), switch
            assert isinstance(switch["to_bpm"], int), switch
