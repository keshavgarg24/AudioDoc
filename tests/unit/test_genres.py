"""Genre resolution and profile shape."""
from __future__ import annotations

import pytest

from labs.artist.genres import GENRES, resolve


# ----------------------------------------------------------------- resolve ---
def test_exact_slug_resolves_to_itself():
    assert resolve("trap") == "trap"
    assert resolve("pop") == "pop"
    assert resolve("house") == "house"


def test_lookup_is_case_insensitive():
    assert resolve("Trap") == "trap"
    assert resolve("POP") == "pop"
    assert resolve("HOUSE") == "house"


def test_hyphen_is_treated_as_underscore():
    # genre slugs use underscores; hyphens in the input are normalised
    assert resolve("boom-bap") == "boom_bap"
    assert resolve("lofi-hiphop") == "lofi_hiphop"


def test_alias_resolves_to_canonical():
    assert resolve("rap") == "trap"
    assert resolve("edm") == "edm_festival"
    assert resolve("r&b") == "rnb"


def test_unknown_genre_returns_none():
    assert resolve("klingon-opera") is None


def test_empty_string_returns_none():
    assert resolve("") is None


def test_none_returns_none():
    assert resolve(None) is None


def test_all_defined_genre_slugs_resolve_to_themselves():
    for slug in GENRES:
        result = resolve(slug)
        assert result == slug, f"genre '{slug}' did not resolve to itself: got {result!r}"


# -------------------------------------------------------------- profiles ---
@pytest.mark.parametrize("slug", list(GENRES.keys()))
def test_genre_profile_has_required_keys(slug):
    profile = GENRES[slug]
    for key in ("bpm", "lufs", "signature", "build"):
        assert key in profile, f"genre '{slug}' missing key '{key}'"


@pytest.mark.parametrize("slug", list(GENRES.keys()))
def test_bpm_range_is_ordered(slug):
    lo, hi = GENRES[slug]["bpm"]
    assert lo < hi, f"genre '{slug}' bpm lo >= hi"


@pytest.mark.parametrize("slug", list(GENRES.keys()))
def test_lufs_target_is_plausible(slug):
    lufs = GENRES[slug]["lufs"]
    assert -30 <= lufs <= -4, f"genre '{slug}' LUFS {lufs} out of range"


@pytest.mark.parametrize("slug", list(GENRES.keys()))
def test_signature_is_non_empty_list(slug):
    sig = GENRES[slug]["signature"]
    assert isinstance(sig, (list, tuple)) and len(sig) > 0, \
        f"genre '{slug}' has no signature items"


@pytest.mark.parametrize("slug", list(GENRES.keys()))
def test_build_steps_are_non_empty(slug):
    build = GENRES[slug]["build"]
    assert isinstance(build, (list, tuple)) and len(build) > 0, \
        f"genre '{slug}' has no build steps"
