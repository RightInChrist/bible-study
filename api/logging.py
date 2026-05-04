"""Structured JSON logging shared across the importer and the FastAPI app.

PLAN §Observability pins the format: one JSON object per line on stdout,
mandatory keys ``ts`` (ISO-8601 UTC), ``level``, ``event``, plus optional
domain keys (``run_id``, ``sentence_id``, ``error_code``, ``duration_ms`` …).
Adding a new event identifier is a deliberate code change; renaming an
existing one is a breaking log-grep change.

Callers emit events via ``logger.info("import.started", extra={...})``.
The formatter merges ``extra`` into the JSON object alongside the
mandatory keys.

# TODO(slice-2): contextvar + middleware for request_id. PLAN §Observability
# also names ``request_id`` as a per-line correlation key. Plumbing the
# contextvar now would only be useful once a runner emits ``run_id``
# correlated with the originating HTTP request — neither exists in this
# slice. When the generation runner ships, add an ASGI middleware that
# generates a UUID4, stores it in a contextvar read by the formatter, and
# propagates it across the runner's awaited tasks via ``contextvars.copy_context``.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


_RESERVED_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class _JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as one JSON object on a single line.

    The ``msg`` field of the record is the **event identifier** (e.g.
    ``import.started``). Any keyword passed via ``extra={...}`` becomes a
    top-level key on the emitted object — that's how the importer's
    ``manifest_hash``, the eventual ``run_id``, etc. travel. (See module
    docstring TODO: ``request_id`` is not yet propagated.)
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = False


def configure_logging() -> None:
    """Attach the JSON formatter to the root logger exactly once.

    Idempotent: subsequent calls are no-ops. Tests that capture log output
    (caplog / capsys) get the JSON-formatted lines verbatim.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    # Replace any pre-existing handlers so duplicate lines aren't emitted
    # (uvicorn / pytest may have already attached a default StreamHandler).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger; ensures the JSON formatter is configured.

    If a previous ``logging.config.fileConfig`` call (e.g. Alembic's
    ``alembic.ini``) disabled the named logger by virtue of its
    ``disable_existing_loggers=True`` default, re-enable it here. The
    importer's structured event log is part of the data contract; a
    transient migration step should not silence it.
    """
    configure_logging()
    logger = logging.getLogger(name)
    logger.disabled = False
    return logger
