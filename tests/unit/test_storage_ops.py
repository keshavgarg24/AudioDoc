"""MongoStore operations: dedup, audio_files, tool_results, idempotency."""
from __future__ import annotations

import hashlib


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_REPORT = {
    "mode": "ai",
    "prediction": "Real",
    "confidence": 0.9,
    "fake_probability": 0.1,
    "source": {"duration_seconds": 184.2},
}


def _save(mongo, analysis_id: str, sha: str, mode: str = "ai",
          report=None):
    """Helper: save an analysis with the correct signature."""
    mongo.save_analysis(
        analysis_id,
        {**(report or _REPORT), "mode": mode},
        {"filename": "t.mp3"},
        {"sha256": sha},
    )


# -------------------------------------------------- analysis dedup ------
def test_find_by_hash_returns_none_when_no_match(mongo):
    assert mongo.find_by_hash("a" * 64, "ai") is None


def test_find_by_hash_hits_after_save(mongo):
    sha = _sha(b"audio-payload")
    _save(mongo, "job-1", sha, "ai")
    hit = mongo.find_by_hash(sha, "ai")
    assert hit is not None


def test_find_by_hash_is_mode_scoped(mongo):
    sha = _sha(b"same-audio-dedup")
    _save(mongo, "job-mode", sha, "audio")
    assert mongo.find_by_hash(sha, "ai") is None
    assert mongo.find_by_hash(sha, "audio") is not None


def test_find_report_by_hash_returns_report_content(mongo):
    sha = _sha(b"report-hash-test")
    _save(mongo, "job-report", sha, "full")
    doc = mongo.find_report_by_hash(sha, "full")
    assert doc is not None


# --------------------------------------------------- audio file records --
def test_audio_file_not_found_returns_none(mongo):
    assert mongo.get_audio_file("not-a-real-sha") is None


def test_list_tool_results_for_unknown_sha_is_empty(mongo):
    assert mongo.list_tool_results_for_audio("not-a-real-sha") == []


# --------------------------------------------------- tool results cache ---
def test_tool_result_miss_returns_none(mongo):
    assert mongo.get_tool_result("not-a-real-cache-key") is None


def test_tool_result_stored_and_retrieved(mongo):
    key = "cache-key-abc123"
    result = {"tool": "master-check", "score": 0.88}
    mongo.save_tool_result(key, "master-check", {"file": "sha1"}, {}, result)
    cached = mongo.get_tool_result(key)
    assert cached is not None
    assert cached.get("score") == 0.88


def test_tool_result_lookup_by_input_digest(mongo):
    sha = _sha(b"audio-for-tool")
    key = "tool-result-key-lookup"
    mongo.save_tool_result(key, "tempo-lab", {"file": sha}, {}, {"bpm": 128})
    results = mongo.list_tool_results_for_audio(sha)
    assert isinstance(results, list)
    assert len(results) >= 1


def test_two_file_tool_result_found_by_either_sha(mongo):
    sha_beat = _sha(b"beat-audio")
    sha_vocal = _sha(b"vocal-audio")
    key = "two-file-key-xyz"
    mongo.save_tool_result(
        key, "beat-vocal-fit",
        {"beat": sha_beat, "vocal": sha_vocal},
        {}, {"compatible": True},
    )
    assert len(mongo.list_tool_results_for_audio(sha_beat)) >= 1
    assert len(mongo.list_tool_results_for_audio(sha_vocal)) >= 1


# --------------------------------------------------- idempotency claims ---
def test_idempotency_first_call_returns_none(mongo):
    assert mongo.claim_idempotency("idem-unique-1", "job-abc") is None


def test_idempotency_repeat_returns_original_job(mongo):
    mongo.claim_idempotency("idem-unique-2", "job-xyz")
    existing = mongo.claim_idempotency("idem-unique-2", "job-different")
    assert existing == "job-xyz"


def test_different_idempotency_keys_are_independent(mongo):
    mongo.claim_idempotency("idem-a", "job-a")
    mongo.claim_idempotency("idem-b", "job-b")
    assert mongo.claim_idempotency("idem-a", "job-x") == "job-a"
    assert mongo.claim_idempotency("idem-b", "job-x") == "job-b"


# ------------------------------------------------------- usage tracking ---
def test_usage_today_starts_at_zero(mongo):
    assert mongo.usage_today("new-principal") == 0


def test_enabled_flag_is_true(mongo):
    assert mongo.enabled is True


def test_health_returns_ok_when_enabled(mongo):
    h = mongo.health()
    assert h.get("enabled") is True
    assert h.get("ok") is True


# ------------------------------------------------------- list analyses ---
def test_list_analyses_with_no_data_returns_empty(mongo):
    results = mongo.list_analyses(limit=10)
    assert isinstance(results, list)


def test_list_analyses_filters_by_api_key(mongo):
    sha = _sha(b"keyed-analysis")
    mongo.save_analysis(
        "job-keyed",
        {**_REPORT, "mode": "ai"},
        {"filename": "t.mp3", "api_key_id": "key-abc"},
        {"sha256": sha},
    )
    by_key = mongo.list_analyses(api_key_id="key-abc", limit=10)
    other_key = mongo.list_analyses(api_key_id="key-xyz", limit=10)
    assert len(by_key) >= 1
    assert len(other_key) == 0
