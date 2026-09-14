"""Field selection / response filtering."""
from __future__ import annotations

from labs.api.fields import ALWAYS_FIELDS, FIELD_GROUPS, filter_fields, group_names


# ------------------------------------------------------------------ groups ---
def test_all_always_fields_are_never_stripped():
    report = {"mode": "ai", "source": {}, "runtime": 1.0,
               "model": "v1", "secret": "hidden"}
    out = filter_fields(report, "verdict")
    for f in ALWAYS_FIELDS:
        if f in report:
            assert f in out, f"always-field '{f}' was stripped"


def test_empty_csv_returns_everything():
    report = {"mode": "ai", "prediction": "Real", "musical": {}}
    out = filter_fields(report, "")
    assert out == report


def test_selecting_verdict_group_includes_its_keys():
    report = {"mode": "ai", "prediction": "Real", "confidence": 0.9,
               "fake_probability": 0.1, "model": "v1"}
    out = filter_fields(report, "verdict")
    assert "prediction" in out
    assert "confidence" in out
    assert "fake_probability" in out


def test_selecting_multiple_groups_via_csv():
    report = {"mode": "full", "prediction": "Real",
              "musical": {"key": "C"}, "production": {"lufs": -8.0},
              "model": "v1"}
    out = filter_fields(report, "verdict,musical")
    assert "prediction" in out
    assert "musical" in out
    assert "production" not in out


def test_unknown_group_name_is_treated_as_literal_key():
    report = {"mode": "ai", "prediction": "Real", "model": "v1",
               "custom_field": "present"}
    out = filter_fields(report, "verdict,custom_field")
    assert "custom_field" in out


def test_always_fields_survive_any_selection():
    report = {"mode": "ai", "source": {}, "runtime": 1.5,
               "model": "v1", "prediction": "Real"}
    out = filter_fields(report, "verdict")
    assert "mode" in out
    assert "source" in out


def test_result_is_a_different_dict_not_the_original():
    report = {"mode": "ai", "prediction": "Real", "model": "v1"}
    out = filter_fields(report, "verdict")
    out["injected"] = True
    assert "injected" not in report


def test_all_defined_groups_contain_at_least_one_key():
    for group, keys in FIELD_GROUPS.items():
        assert len(keys) >= 1, f"FIELD_GROUPS['{group}'] is empty"


def test_group_names_are_sorted():
    names = group_names()
    assert names == sorted(names)


def test_group_names_match_field_groups_keys():
    assert set(group_names()) == set(FIELD_GROUPS.keys())


def test_csv_whitespace_is_stripped():
    report = {"mode": "ai", "musical": {}, "model": "v1"}
    out = filter_fields(report, "  musical  ,  verdict  ")
    assert "musical" in out
