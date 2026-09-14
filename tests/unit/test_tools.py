"""The tool registry and its caching contract."""
from __future__ import annotations

import pytest

from labs import tools as registry
from labs.services.tools import cache_key

EXPECTED_SLUGS = {
    "master-check", "tempo-lab", "key-lab", "reference-match",
    "vocal-lab", "beat-vocal-fit",
}


# --------------------------------------------------------------- registry ---
def test_every_expected_tool_is_registered():
    assert set(registry.slugs()) == EXPECTED_SLUGS


def test_unknown_slug_resolves_to_nothing():
    assert registry.get("no-such-tool") is None
    assert registry.spec("no-such-tool") is None


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_each_tool_exposes_a_runnable_interface(slug):
    module = registry.get(slug)
    assert module is not None
    assert callable(module.run)
    assert module.SPEC.slug == slug


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_each_spec_is_complete(slug):
    """The catalogue is the API's contract. A tool missing its accuracy or
    limitations text would ship a claim the product cannot back."""
    spec = registry.spec(slug)
    assert spec.name
    assert spec.summary
    assert spec.inputs
    assert spec.accuracy
    assert spec.basis
    assert spec.limitations
    assert len(spec.typical_seconds) == 2
    assert spec.typical_seconds[0] <= spec.typical_seconds[1]


def test_catalogue_matches_the_registry():
    catalogue = registry.catalogue()
    assert {t["slug"] for t in catalogue} == EXPECTED_SLUGS
    for entry in catalogue:
        assert entry["file_count"] == len(entry["inputs"])


def test_declared_input_count_matches_the_spec():
    assert registry.spec("beat-vocal-fit").inputs == ("beat", "vocal")
    assert registry.spec("reference-match").inputs == ("file", "reference")
    assert registry.spec("master-check").inputs == ("file",)


# ------------------------------------------------------------ cache keys ----
def test_same_inputs_produce_the_same_key():
    a = cache_key("master-check", ["d1"], {})
    b = cache_key("master-check", ["d1"], {})
    assert a == b


def test_different_audio_produces_a_different_key():
    assert cache_key("master-check", ["d1"], {}) != \
           cache_key("master-check", ["d2"], {})


def test_different_tool_produces_a_different_key():
    """The same audio measured by a different tool is a different question."""
    assert cache_key("master-check", ["d1"], {}) != \
           cache_key("tempo-lab", ["d1"], {})


def test_input_order_does_not_change_the_key():
    """A two-file tool receives its inputs in a fixed order, but the key is
    built from a set of digests and must not depend on iteration order."""
    assert cache_key("beat-vocal-fit", ["d1", "d2"], {}) == \
           cache_key("beat-vocal-fit", ["d2", "d1"], {})


def test_options_participate_in_the_key():
    """A cached result must never be returned for different options; that
    answers a question the caller did not ask."""
    plain = cache_key("hit-lab", ["d1"], {})
    with_genre = cache_key("hit-lab", ["d1"], {"genre": "techno"})
    assert plain != with_genre


def test_keys_are_hex_digests():
    key = cache_key("master-check", ["d1"], {})
    assert len(key) == 64
    int(key, 16)


# --------------------------------------------------------------- digests ---
def test_file_digest_is_stable_and_content_addressed(tmp_path):
    from labs.tools.base import file_digest

    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"identical bytes")
    b.write_bytes(b"identical bytes")

    assert file_digest(str(a)) == file_digest(str(b))

    b.write_bytes(b"different bytes")
    assert file_digest(str(a)) != file_digest(str(b))
