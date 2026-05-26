"""
core/logging.py — structured logging for the Jardin backend
=============================================================

Sets up structlog so that **every** log call — whether from code that uses
``structlog.get_logger()`` or from code that uses the standard
``logging.getLogger()`` — is rendered as newline-delimited JSON on stdout.

Usage
-----
Call ``configure_logging()`` once at application startup (``lifespan`` in
``app/main.py``).  Everywhere else, just use whichever logger style you
prefer::

    # New-style structured logger (preferred for rich key-value events):
    from core.logging import get_logger
    log = get_logger(__name__)
    log.info("agent_call", agent_name="score_pollinators", duration_ms=1420.3)

    # Existing stdlib-style (works unchanged, output is JSON):
    import logging
    logger = logging.getLogger(__name__)
    logger.info("fetch_candidates: retrieved %d species", n)

JSON log line shape (one object per line)::

    {
      "timestamp": "2025-05-26T12:34:56.789Z",
      "level": "info",
      "logger": "agents.graph",
      "event": "agent_call",
      "agent_name": "score_pollinators",
      "duration_ms": 1420.3,
      "species_count": 48,
      "token_usage": {"input": 6231, "output": 1842},
      "request_id": "b3f9a12c"
    }
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_logging(
    log_level: str | None = None,
    json_logs: bool | None = None,
) -> None:
    """
    Configure structlog + stdlib logging for the Jardin backend.

    Parameters
    ----------
    log_level:
        One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``.  Defaults to the
        ``LOG_LEVEL`` environment variable, or ``INFO`` if unset.
    json_logs:
        ``True`` → JSON output (default in production / Docker).
        ``False`` → colourised human-readable output (nice for local dev).
        Defaults to ``True`` when ``LOG_FORMAT=json`` or in non-TTY
        environments.
    """
    level_name = (
        log_level
        or os.environ.get("LOG_LEVEL", "INFO")
    ).upper()
    level = getattr(logging, level_name, logging.INFO)

    if json_logs is None:
        json_logs = (
            os.environ.get("LOG_FORMAT", "").lower() == "json"
            or not sys.stdout.isatty()
        )

    # ── Shared processors (applied to both structlog and stdlib-via-structlog)
    shared_processors: list[Any] = [
        # Merge any bound context-var values (e.g. request_id bound at request
        # start via structlog.contextvars.bind_contextvars(request_id=...))
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Render exception info as a structured dict rather than a string
        structlog.processors.ExceptionRenderer(),
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # ── Configure structlog itself
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Configure stdlib so existing logging.getLogger() calls go through
    #    structlog's ProcessorFormatter and come out as the same JSON stream.
    formatter = structlog.stdlib.ProcessorFormatter(
        # Final renderer for structlog-native log records
        processor=renderer,
        # Pre-chain for foreign (stdlib) log records before they hit the
        # final renderer
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Silence noisy third-party libraries
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog bound-logger for ``name``.

    Prefer this over ``logging.getLogger`` for new code that needs rich
    key-value context; both feed the same JSON output stream.
    """
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Structured event helpers
# ---------------------------------------------------------------------------

def log_agent_call(
    logger: structlog.stdlib.BoundLogger,
    *,
    agent_name: str,
    request_id: str | None,
    duration_ms: float,
    species_count: int,
    token_usage: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """
    Emit a single structured log event for one scoring agent invocation.

    All fields are included even when ``None`` so downstream log aggregators
    can index the schema without encountering missing keys.

    Example output (JSON)::

        {
          "event": "agent_call",
          "agent_name": "score_pollinators",
          "request_id": "b3f9a12c",
          "duration_ms": 1420.3,
          "species_count": 48,
          "token_usage": {"input": 6231, "output": 1842},
          "error": null
        }
    """
    fields: dict[str, Any] = {
        "agent_name": agent_name,
        "request_id": request_id,
        "duration_ms": round(duration_ms, 1),
        "species_count": species_count,
        "token_usage": token_usage,
        "error": error,
    }
    if error:
        logger.error("agent_call", **fields)
    else:
        logger.info("agent_call", **fields)
