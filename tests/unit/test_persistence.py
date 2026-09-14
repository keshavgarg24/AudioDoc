"""Persistence and content deduplication.

The dedup path is what stops the same audio being analysed twice. It hinges on
one field: `audio.sha256` on the stored analysis. If that is ever written
conditionally, dedup silently stops working and every submission pays for a
full pipeline run - a regression with no error and no log line, visible only
as a bill.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from labs.services import persistence, storage
from labs.services.storage import summarize, trim_report

REPORT = {
    "mode": "ai",
    "prediction": "Fake",
    "confidence": 0.91,
    "fake_probability": 0.93,
    "source": {"duration_seconds": 184.2},
    "musical": {"rhythm": {"bpm": 128.0}, "harmony": {"key": "A min"}},
    "detection": {"consensus": {"verdict": "ai_generated", "agreement": "full"}},
}


@pytest.fixture()
def audio_file():
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"RIFF" + b"\x00" * 64 + b"WAVEfmt ")
    yield path
    if os.path.exists(path):
        os.unlink(path)


# --------------------------------------------------------------- the bug ---
def test_sha256_is_stored_without_object_storage(mongo, audio_file,
                                                 monkeypatch):
    """Dedup must not depend on the audio bucket being configured.

    The SHA used to arrive only as a by-product of the S3 upload, so a
    deployment with no bucket wrote `audio: null`, the dedup query on
    `audio.sha256` never matched, and byte-identical audio was re-analysed
    every single time.
    """
    class NoBucket:
        enabled = False

        def put(self, *a, **k):
            return None

    monkeypatch.setattr(storage, "get_audio_store", lambda *a, **k: NoBucket())

    analysis_id = persistence.record(
        analysis_id="a1", report=REPORT, path=audio_file,
        filename="track.wav", seconds=12.0, mode="ai")

    assert analysis_id == "a1"
    doc = mongo.db().analyses.find_one({"_id": "a1"})
    assert doc is not None
    assert doc.get("audio", {}).get("sha256"), \
        "sha256 missing; dedup cannot work on this deployment"
    assert len(doc["audio"]["sha256"]) == 64


def test_identical_audio_is_found_by_hash(mongo, audio_file, monkeypatch):
    class NoBucket:
        enabled = False

        def put(self, *a, **k):
            return None

    monkeypatch.setattr(storage, "get_audio_store", lambda *a, **k: NoBucket())
    persistence.record(analysis_id="a1", report=REPORT, path=audio_file,
                       filename="track.wav", seconds=12.0, mode="ai")

    from labs.tools.base import file_digest
    digest = file_digest(audio_file)

    hit = mongo.find_report_by_hash(digest, mode="ai")
    assert hit is not None
    assert hit["_id"] == "a1"
    assert hit["report"]["prediction"] == "Fake"


def test_dedup_is_scoped_to_the_mode(mongo, audio_file, monkeypatch):
    """An audio-only run carries no verdict. Returning it to a caller who
    asked for detection would answer a different question than the one
    asked."""
    class NoBucket:
        enabled = False

        def put(self, *a, **k):
            return None

    monkeypatch.setattr(storage, "get_audio_store", lambda *a, **k: NoBucket())
    persistence.record(analysis_id="a1", report={**REPORT, "mode": "audio"},
                       path=audio_file, filename="t.wav", seconds=3.0,
                       mode="audio")

    from labs.tools.base import file_digest
    digest = file_digest(audio_file)

    assert mongo.find_report_by_hash(digest, mode="audio") is not None
    assert mongo.find_report_by_hash(digest, mode="ai") is None


def test_persistence_is_a_noop_without_a_database(audio_file):
    storage.reset()
    result = persistence.record(
        analysis_id="x", report=REPORT, path=audio_file,
        filename="t.wav", seconds=1.0, mode="ai")
    assert result is None


def test_a_storage_failure_does_not_raise(mongo, audio_file, monkeypatch):
    """An analysis that succeeded must be returned even if recording it fails."""
    def boom(*a, **k):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(mongo, "save_analysis", boom)
    assert persistence.record(
        analysis_id="a1", report=REPORT, path=audio_file,
        filename="t.wav", seconds=1.0, mode="ai") is None


# ------------------------------------------------------------- audio_files --
def test_tool_runs_record_the_audio_they_saw(mongo):
    from labs.services import tools as tool_service

    digests = {"file": "a" * 64}
    decoded = {"file": type("D", (), {"duration": 12.5, "channels": 2,
                                      "sr": 44100})()}
    tool_service._record_audio_files(digests, decoded, {})

    doc = mongo.db().audio_files.find_one({"_id": "a" * 64})
    assert doc is not None
    assert doc["duration_seconds"] == 12.5
    assert doc["sample_rate"] == 44100
    assert doc["analysis_count"] == 1


def test_seeing_the_same_audio_again_increments_rather_than_duplicates(mongo):
    from labs.services import tools as tool_service

    digests = {"file": "b" * 64}
    for _ in range(3):
        tool_service._record_audio_files(digests, None, {})

    assert mongo.db().audio_files.count_documents({"_id": "b" * 64}) == 1
    doc = mongo.db().audio_files.find_one({"_id": "b" * 64})
    assert doc["analysis_count"] == 3


def test_tool_results_are_findable_by_any_input_digest(mongo):
    """Two-file tools name their inputs "beat"/"vocal", not "file". A reverse
    lookup keyed on one fixed name would miss every one of them."""
    mongo.save_tool_result(
        key="k1", tool="beat-vocal-fit",
        digests={"beat": "c" * 64, "vocal": "d" * 64},
        params={}, result={"ok": True})

    assert len(mongo.list_tool_results_for_audio("c" * 64)) == 1
    assert len(mongo.list_tool_results_for_audio("d" * 64)) == 1
    assert mongo.list_tool_results_for_audio("e" * 64) == []


# ----------------------------------------------------------- report tiering --
def test_summary_extracts_the_indexed_scalars():
    s = summarize(REPORT)
    assert s["verdict"]["is_ai"] is True
    assert s["verdict"]["prediction"] == "Fake"
    assert s["music"]["bpm"] == 128.0
    assert s["duration_s"] == 184.2


def test_plotting_series_are_dropped_before_storage():
    """Long numeric arrays exist to draw a chart once and are what turn a
    working database into an expensive one."""
    report = {**REPORT, "features": {"spectrum": list(range(4096))},
              "keep": [1, 2, 3]}
    trimmed, dropped = trim_report(report)

    assert "spectrum" not in trimmed.get("features", {})
    assert "features.spectrum" in dropped
    # Short arrays are real data, not a series.
    assert trimmed["keep"] == [1, 2, 3]


def test_round_trip_through_compression():
    from labs.services.storage import compress_report, decompress_report

    packed = compress_report(REPORT)
    assert packed["report_bytes_gz"] < packed["report_bytes_raw"]
    assert decompress_report(packed)["prediction"] == "Fake"
