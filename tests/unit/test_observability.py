"""Structured logging, request correlation and metrics."""
from __future__ import annotations

import json
import logging
import threading

import pytest

from labs.core import metrics as metrics_mod
from labs.core.logging import (
    JsonFormatter,
    RequestIdFilter,
    bind_request_id,
    get_request_id,
    reset_request_id,
    set_request_id,
)


@pytest.fixture(autouse=True)
def _fresh_metrics():
    metrics_mod.reset()
    yield
    metrics_mod.reset()


# ------------------------------------------------------------ correlation ---
def test_request_id_defaults_to_none():
    assert get_request_id() is None


def test_set_and_reset_request_id():
    token = set_request_id("abc123")
    assert get_request_id() == "abc123"
    reset_request_id(token)
    assert get_request_id() is None


def test_bind_request_id_restores_the_previous_value():
    token = set_request_id("outer")
    with bind_request_id("inner"):
        assert get_request_id() == "inner"
    assert get_request_id() == "outer"
    reset_request_id(token)


def test_bind_request_id_works_inside_a_worker_thread():
    """The case that matters: ContextVars do not cross into a pool thread."""
    seen = {}

    def worker():
        # Nothing inherited: a fresh thread starts with no context.
        seen["before"] = get_request_id()
        with bind_request_id("job-request-id"):
            seen["during"] = get_request_id()
        seen["after"] = get_request_id()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert seen["before"] is None
    assert seen["during"] == "job-request-id"
    assert seen["after"] is None


def test_filter_puts_request_id_on_the_record():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello",
                               None, None)
    token = set_request_id("req-9")
    RequestIdFilter().filter(record)
    assert record.request_id == "req-9"
    reset_request_id(token)


def test_filter_uses_a_placeholder_outside_a_request():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x", None, None)
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


# ------------------------------------------------------------------ json ----
def _format(msg="hello", request_id="req-1", **extra):
    record = logging.LogRecord("labs.test", logging.INFO, __file__, 7, msg,
                               None, None)
    record.request_id = request_id
    for k, v in extra.items():
        setattr(record, k, v)
    return json.loads(JsonFormatter().format(record))


def test_json_formatter_emits_one_object_per_line():
    out = _format()
    assert out["level"] == "INFO"
    assert out["logger"] == "labs.test"
    assert out["message"] == "hello"
    assert out["request_id"] == "req-1"
    assert out["ts"].endswith("Z")


def test_json_formatter_includes_extra_fields():
    out = _format(analysis_id="abc", duration=1.5)
    assert out["analysis_id"] == "abc"
    assert out["duration"] == 1.5


def test_json_formatter_survives_an_unserialisable_extra():
    out = _format(obj=object())
    assert "obj" in out          # degraded to a repr rather than raising


def test_json_formatter_includes_the_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord("labs.test", logging.ERROR, __file__, 1,
                                   "failed", None, sys.exc_info())
        record.request_id = "r"
        out = json.loads(JsonFormatter().format(record))
    assert "boom" in out["exception"]


# --------------------------------------------------------------- metrics ---
@pytest.mark.parametrize("path,expected", [
    ("/health", "/health"),
    ("/v1/tools", "/v1/tools"),
    ("/v1/analyses", "/v1/analyses"),
    ("/v1/analyses/9f2c1a4e", "/v1/analyses/{id}"),
    ("/v1/tools/results/abc123", "/v1/tools/results/{id}"),
    ("/totally/unknown", "other"),
])
def test_route_labels_are_bounded(path, expected):
    """A label carrying a job id gives one time series per request."""
    assert metrics_mod.route_of(path) == expected


def test_request_counter_increments():
    m = metrics_mod.get_metrics()
    m.request("/v1/tools", 200, 5.0)
    m.request("/v1/tools", 200, 6.0)
    m.request("/v1/tools", 500, 7.0)
    out = m.render({})
    assert 'labs_requests_total{route="/v1/tools",status="200"} 2' in out
    assert 'labs_requests_total{route="/v1/tools",status="500"} 1' in out


def test_latency_histogram_is_cumulative():
    m = metrics_mod.get_metrics()
    for ms in (1, 1, 1, 400):
        m.request("/health", 200, ms)
    out = m.render({})
    # Three sub-10ms requests land in every bucket at or above 0.01s.
    assert 'labs_request_duration_seconds_bucket{route="/health",le="0.01"} 3' in out
    assert 'labs_request_duration_seconds_bucket{route="/health",le="+Inf"} 4' in out
    assert 'labs_request_duration_seconds_count{route="/health"} 4' in out


def test_analysis_outcomes_are_counted():
    m = metrics_mod.get_metrics()
    m.analysis("succeeded", 12.0)
    m.analysis("failed", 3.0)
    m.analysis("succeeded", 8.0)
    out = m.render({})
    assert 'labs_analyses_total{outcome="succeeded"} 2' in out
    assert 'labs_analyses_total{outcome="failed"} 1' in out
    assert "labs_analysis_duration_seconds_count 3" in out


def test_queue_gauges_are_rendered():
    out = metrics_mod.get_metrics().render(
        {"queued": 2, "running": 1, "pending": 3, "total": 40})
    assert 'labs_queue_jobs{state="queued"} 2' in out
    assert 'labs_queue_jobs{state="pending"} 3' in out
    assert 'labs_queue_jobs{state="total"} 40' in out


def test_render_is_valid_prometheus_exposition():
    m = metrics_mod.get_metrics()
    m.request("/health", 200, 3.0)
    m.analysis("succeeded", 1.0)
    for line in m.render({"pending": 0}).splitlines():
        if line.startswith("#"):
            assert line.startswith(("# HELP ", "# TYPE "))
        elif line:
            name, _, value = line.rpartition(" ")
            assert name, line
            float(value)          # every sample must end in a number


def test_metrics_are_threadsafe():
    m = metrics_mod.get_metrics()

    def hammer():
        for _ in range(200):
            m.request("/health", 200, 1.0)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 'labs_requests_total{route="/health",status="200"} 1600' in m.render({})
