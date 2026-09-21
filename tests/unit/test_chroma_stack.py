"""The shared constant-Q transform must not change what Key Lab reports.

`analysis.musical.chroma_stack` replaced two `librosa.feature.chroma_cqt`
calls with one transform folded twice. That is only a safe trade if the folded
result is the same evidence the two calls produced - otherwise it is a silent
change to key detection dressed up as an optimisation.

These tests pin the two claims the docstring makes, so a librosa upgrade that
changes `chroma_cqt` internals fails here rather than quietly shifting every
key result in production.
"""
from __future__ import annotations

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")

from labs.analysis.musical import (
    _CHORD_BANK, _CHORD_NAMES, _CHORD_SHAPES, PITCHES, chroma_stack)

SR = 22050


def _tone_bed(seconds: float = 12.0) -> np.ndarray:
    """A low C root under an upper triad that shares none of its pitch classes.

    The registers are deliberately given disjoint pitch classes. The bass fold
    covers three octaves from C1, so it reaches C4 and no further: a C2 root
    lands inside it and an F#4 triad lands outside. That separation is what
    lets the third test below tell a genuine low-register fold apart from one
    that folded the same bins twice - which a signal with the same notes in
    both registers could not do.
    """
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    y = np.zeros_like(t)
    for hz, amp in ((65.41, 0.6),      # C2  - bass register only
                    (369.99, 0.3),     # F#4 - above the bass fold's ceiling
                    (440.00, 0.25),    # A4
                    (554.37, 0.25)):   # C#5
        y += amp * np.sin(2 * np.pi * hz * t)
    # A little noise so the tuning estimator has something to work with.
    y += 0.01 * np.random.default_rng(0).standard_normal(t.size)
    return y.astype(np.float32)


@pytest.fixture(scope="module")
def signal() -> np.ndarray:
    return _tone_bed()


def test_full_fold_is_identical_to_chroma_cqt(signal):
    """The seven-octave fold must reproduce librosa's own call exactly."""
    expected = librosa.feature.chroma_cqt(y=signal, sr=SR, n_octaves=7)
    got = chroma_stack(signal, SR)["full"]

    assert got.shape == expected.shape
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)


def test_bass_fold_matches_a_standalone_three_octave_call(signal):
    """The bass fold may differ per frame, but not in the profile that is read.

    `_rank` only ever consumes the time-averaged 12-bin profile, so that is
    where the tolerance has to be tight. Individual frames are allowed a
    little slack because a three-octave filter bank rounds its frame count
    differently from a seven-octave one.
    """
    expected = librosa.feature.chroma_cqt(
        y=signal, sr=SR, fmin=librosa.note_to_hz("C1"), n_octaves=3)
    got = chroma_stack(signal, SR)["bass"]

    assert got.shape == expected.shape

    def profile(c: np.ndarray) -> np.ndarray:
        p = c.mean(axis=1)
        return p / (p.sum() or 1.0)

    l1 = float(np.abs(profile(got) - profile(expected)).sum())
    assert l1 < 1e-4, f"bass profile drifted by {l1}"


def test_bass_fold_is_weighted_to_the_low_register(signal):
    """Sanity: the bass fold must actually favour the bass root.

    Without this the two tests above would still pass if `chroma_stack` folded
    the same bins twice, which would silently remove the tonic evidence Key
    Lab uses to separate relative major from relative minor.

    Compared as shares rather than raw weights: every chroma column is
    infinity-normalised, so the loudest pitch class reads 1.0 in both folds
    and an absolute comparison would be vacuous.
    """
    stack = chroma_stack(signal, SR)
    c_index, fsharp_index = 0, 6  # C2 root; F#4 sits above the bass ceiling

    def share(chroma: np.ndarray, index: int) -> float:
        profile = chroma.mean(axis=1)
        return float(profile[index] / (profile.sum() or 1.0))

    assert share(stack["bass"], c_index) > share(stack["full"], c_index)
    assert share(stack["full"], fsharp_index) > share(stack["bass"], fsharp_index)


def test_chord_bank_matches_the_templates_it_replaced():
    """The prebuilt bank must equal the templates the per-beat loop built."""
    assert _CHORD_BANK.shape == (12, 12 * len(_CHORD_SHAPES))
    assert len(_CHORD_NAMES) == 12 * len(_CHORD_SHAPES)

    col = 0
    for shift in range(12):
        for suffix, tones in _CHORD_SHAPES.items():
            expected = np.zeros(12)
            for tone in tones:
                expected[(tone + shift) % 12] = 1.0
            expected /= expected.sum()
            np.testing.assert_allclose(_CHORD_BANK[:, col], expected)
            col += 1


def test_chord_scoring_picks_the_same_winner_as_the_nested_loop():
    """Vectorised scoring must agree with the loop on every beat, ties included."""
    rng = np.random.default_rng(7)
    sync = rng.random((12, 64))

    totals = sync.sum(axis=0)
    normalised = sync / totals
    vectorised = [_CHORD_NAMES[i]
                  for i in (normalised.T @ _CHORD_BANK).argmax(axis=1)]

    looped = []
    for i in range(sync.shape[1]):
        v = normalised[:, i]
        top_name, top_score = None, -1.0
        for shift in range(12):
            for suffix, tones in _CHORD_SHAPES.items():
                tpl = np.zeros(12)
                for tone in tones:
                    tpl[(tone + shift) % 12] = 1.0
                tpl /= tpl.sum()
                score = float(np.dot(v, tpl))
                if score > top_score:
                    top_score, top_name = score, f"{PITCHES[shift]}{suffix}"
        looped.append(top_name)

    assert vectorised == looped


