"""Structured logging with request correlation.

Every log line carries the id of the request that produced it. Without that,
a report of "my upload failed at about 3pm" cannot be tied to anything: the
service handles requests concurrently on a worker pool, so lines from
different requests interleave and the stack trace that matters is
indistinguishable from four others beside it.

The id travels in a ContextVar, which follows async tasks automatically.
Worker threads do not inherit it, so `bind_request_id` is exported for the
job runner to re-attach it on the thread that actually runs the analysis.

Set LABS_LOG_FORMAT=json for one JSON object per line, which is what a log
shipper wants. The default stays human-readable for local work.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from typing import Optional

# The id of the request being served on this task/thread, if any.
_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "labs_request_id", default=None)

# Log record attributes present on every record. Anything outside this set was
# attached by the caller via `extra=` and belongs in the structured output.
_STANDARD = frozenset((
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "request_id", "message", "asctime",
))


def set_request_id(request_id: Optional[str]) -> contextvars.Token:
    return _request_id.set(request_id)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


class bind_request_id:
    """Re-attach a request id inside a worker thread.

    ContextVars are per-thread. The job runner hands work to a thread pool, so
    without this every line an analysis logs - including the traceback when it
    fails - is orphaned from the request that submitted it.

        with bind_request_id(job.meta.get("request_id")):
            ...
    """

    def __init__(self, request_id: Optional[str]):
        self._id = request_id
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> "bind_request_id":
        self._token = _request_id.set(self._id)
        return self

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            _request_id.reset(self._token)


class RequestIdFilter(logging.Filter):
    """Put the current request id on every record, so formatters can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        # default=str so an unserialisable value in `extra=` degrades to its
        # repr rather than taking down the log call that was reporting a
        # problem in the first place.
        return json.dumps(payload, default=str)


def configure(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Install the handler, filter and formatter on the root logger.

    Additive and idempotent. Called at import time by the application module,
    and safe to call again: it replaces its own handler rather than stacking a
    second one, which is what produces duplicated lines.
    """
    level = (level or os.environ.get("LABS_LOG_LEVEL", "INFO")).upper()
    fmt = (fmt or os.environ.get("LABS_LOG_FORMAT", "text")).lower()

    root = logging.getLogger()
    root.setLevel(level)

    handler = next((h for h in root.handlers
                    if getattr(h, "_labs_handler", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._labs_handler = True                       # type: ignore[attr-defined]
        root.addHandler(handler)

    handler.setLevel(level)
    handler.setFormatter(
        JsonFormatter() if fmt == "json"
        else logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"))

    # On the handler, not the logger: a filter on a logger does not run for
    # records that propagate up from its children, which is most of them.
    if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
        handler.addFilter(RequestIdFilter())

    # Chatty at INFO and operationally uninteresting.
    for noisy in ("botocore", "urllib3", "s3transfer", "httpx", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
