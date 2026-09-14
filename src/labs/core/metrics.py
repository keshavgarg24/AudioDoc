"""In-process metrics, exposed in Prometheus text format.

Deliberately dependency-free. `prometheus_client` would bring a registry, a
multiprocess mode and a WSGI app, none of which this needs: one process, one
registry, one endpoint. Adding it would be a dependency to patch for the
lifetime of the service in exchange for code that fits on a screen.

What is tracked is what an alert is written against:

    request rate, error rate, latency   are callers being served
    analysis outcomes and duration      is the pipeline healthy
    queue depth                         is work backing up

Latency is bucketed rather than sampled. Percentiles computed from a reservoir
lie under exactly the conditions you want them to be honest - a slow tail
during an incident - and Prometheus already knows how to read a histogram.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

# Upper bounds in seconds. Chosen around the shape of this service: reads are
# single-digit milliseconds, a submission is tens, and anything past a second
# on the HTTP path means something is wrong.
_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

# Paths collapse to a route template before they become a label. A label whose
# value is a job id gives Prometheus one time series per request, which is the
# standard way to melt a monitoring system.
_KNOWN_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("/v1/analyses", "/v1/analyses"),
    ("/v1/tools/results", "/v1/tools/results/{id}"),
    ("/v1/tools", "/v1/tools"),
    ("/v1/genres", "/v1/genres"),
    ("/v1/health", "/v1/health"),
    ("/v1/ready", "/v1/ready"),
    ("/v1/diagnostics", "/v1/diagnostics"),
    ("/v1/usage", "/v1/usage"),
    ("/v1/metrics", "/v1/metrics"),
    ("/health", "/health"),
    ("/docs", "/docs"),
    ("/openapi.json", "/openapi.json"),
)


def route_of(path: str) -> str:
    """Collapse a concrete path to a bounded label value."""
    if path == "/v1/analyses" or path.startswith("/v1/analyses/"):
        return "/v1/analyses/{id}" if path != "/v1/analyses" else "/v1/analyses"
    for prefix, label in _KNOWN_PREFIXES:
        if path == prefix:
            return label
        if path.startswith(prefix + "/"):
            return f"{prefix}/{{id}}" if "results" not in prefix else label
    return "other"


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.time()
        self._requests: Dict[Tuple[str, int], int] = defaultdict(int)
        self._latency: Dict[str, List[int]] = defaultdict(
            lambda: [0] * (len(_BUCKETS) + 1))
        self._latency_sum: Dict[str, float] = defaultdict(float)
        self._analyses: Dict[str, int] = defaultdict(int)
        self._analysis_seconds: float = 0.0
        self._analysis_count: int = 0

    # ---------------------------------------------------------- recording --
    def request(self, path: str, status: int, elapsed_ms: float) -> None:
        route = route_of(path)
        seconds = elapsed_ms / 1000.0
        with self._lock:
            self._requests[(route, status)] += 1
            buckets = self._latency[route]
            self._latency_sum[route] += seconds
            for i, bound in enumerate(_BUCKETS):
                if seconds <= bound:
                    buckets[i] += 1
                    break
            else:
                buckets[-1] += 1

    def analysis(self, outcome: str, seconds: float = 0.0) -> None:
        """`outcome` is succeeded | failed | cached."""
        with self._lock:
            self._analyses[outcome] += 1
            if seconds > 0:
                self._analysis_seconds += seconds
                self._analysis_count += 1

    # ----------------------------------------------------------- exposure --
    def render(self, queue: Dict) -> str:
        """Prometheus text exposition format."""
        with self._lock:
            requests = dict(self._requests)
            latency = {k: list(v) for k, v in self._latency.items()}
            latency_sum = dict(self._latency_sum)
            analyses = dict(self._analyses)
            analysis_seconds = self._analysis_seconds
            analysis_count = self._analysis_count
            uptime = time.time() - self._started

        out: List[str] = []

        out.append("# HELP labs_uptime_seconds Seconds since process start.")
        out.append("# TYPE labs_uptime_seconds gauge")
        out.append(f"labs_uptime_seconds {uptime:.1f}")

        out.append("# HELP labs_requests_total HTTP requests by route and status.")
        out.append("# TYPE labs_requests_total counter")
        for (route, status), count in sorted(requests.items()):
            out.append(f'labs_requests_total{{route="{route}",'
                       f'status="{status}"}} {count}')

        out.append("# HELP labs_request_duration_seconds HTTP handling time.")
        out.append("# TYPE labs_request_duration_seconds histogram")
        for route, buckets in sorted(latency.items()):
            cumulative = 0
            for i, bound in enumerate(_BUCKETS):
                cumulative += buckets[i]
                out.append(f'labs_request_duration_seconds_bucket'
                           f'{{route="{route}",le="{bound}"}} {cumulative}')
            cumulative += buckets[-1]
            out.append(f'labs_request_duration_seconds_bucket'
                       f'{{route="{route}",le="+Inf"}} {cumulative}')
            out.append(f'labs_request_duration_seconds_sum'
                       f'{{route="{route}"}} {latency_sum.get(route, 0.0):.4f}')
            out.append(f'labs_request_duration_seconds_count'
                       f'{{route="{route}"}} {cumulative}')

        out.append("# HELP labs_analyses_total Analyses by outcome.")
        out.append("# TYPE labs_analyses_total counter")
        for outcome, count in sorted(analyses.items()):
            out.append(f'labs_analyses_total{{outcome="{outcome}"}} {count}')

        out.append("# HELP labs_analysis_duration_seconds_sum Pipeline seconds.")
        out.append("# TYPE labs_analysis_duration_seconds_sum counter")
        out.append(f"labs_analysis_duration_seconds_sum {analysis_seconds:.2f}")
        out.append("# HELP labs_analysis_duration_seconds_count Analyses timed.")
        out.append("# TYPE labs_analysis_duration_seconds_count counter")
        out.append(f"labs_analysis_duration_seconds_count {analysis_count}")

        out.append("# HELP labs_queue_jobs Jobs held in memory by state.")
        out.append("# TYPE labs_queue_jobs gauge")
        for state in ("queued", "running", "pending", "total"):
            out.append(f'labs_queue_jobs{{state="{state}"}} '
                       f'{queue.get(state, 0)}')

        return "\n".join(out) + "\n"


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics


def record_request(path: str, status: int, elapsed_ms: float) -> None:
    _metrics.request(path, status, elapsed_ms)


def record_analysis(outcome: str, seconds: float = 0.0) -> None:
    _metrics.analysis(outcome, seconds)


def reset() -> None:
    """Test hook."""
    global _metrics
    _metrics = Metrics()
