"""
core/tracing.py — lightweight per-run node tracer
===================================================

Records timing, I/O sizes, and success/error for every LangGraph node in a
garden-planner run, then writes a single JSON file to ``backend/traces/``.

Design
------
A ``RunTracer`` accumulates ``NodeTrace`` records as the graph executes.
It is stored in an async ``ContextVar`` so the same tracer instance is
visible to all coroutines in a request (including parallel scoring nodes).

The tracer is **write-once per request** — callers append traces; the tracer
saves them once at the end of ``_stream_planner``.

In production you would stream these records to LangSmith, Honeycomb, or
Datadog instead of (or in addition to) writing local files.

Usage (in recommendations.py)::

    from core.tracing import RunTracer, set_current_tracer, reset_current_tracer

    tracer = RunTracer(request_id=request_id)
    token = set_current_tracer(tracer)
    try:
        async for chunk in graph.astream(initial_state):
            ...
    finally:
        tracer.save()
        reset_current_tracer(token)

Usage (in graph.py, inside _run_scoring_agent)::

    from core.tracing import record_node_trace

    record_node_trace(
        node_name="score_pollinators",
        start_time=start_iso,
        end_time=end_iso,
        duration_ms=duration_ms,
        input_size=len(candidate_species),
        output_size=len(validated),
        success=True,
    )
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Traces directory — backend/traces/ relative to this file's parent's parent
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).parent.parent
TRACES_DIR = _BACKEND_DIR / "traces"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NodeTrace:
    """One completed LangGraph node execution."""
    node_name: str
    start_time: str          # ISO-8601 UTC
    end_time: str            # ISO-8601 UTC
    duration_ms: float
    input_size: int          # number of candidate species passed in
    output_size: int         # number of scored species returned
    success: bool
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunTrace:
    """All node traces for a single graph run."""
    request_id: str
    run_start: str           # ISO-8601 UTC
    run_end: str | None = None
    nodes: list[NodeTrace] = field(default_factory=list)
    total_duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Context variable — one RunTracer per async request context
# ---------------------------------------------------------------------------

_current_tracer: ContextVar[RunTrace | None] = ContextVar(
    "current_tracer", default=None
)


def set_current_tracer(tracer: RunTrace) -> Token:
    """Bind *tracer* to the current async context.  Store the returned token."""
    return _current_tracer.set(tracer)


def reset_current_tracer(token: Token) -> None:
    """Restore the previous tracer (or None) using the token from ``set_current_tracer``."""
    _current_tracer.reset(token)


def get_current_tracer() -> RunTrace | None:
    """Return the tracer bound to the current async context, or ``None``."""
    return _current_tracer.get()


# ---------------------------------------------------------------------------
# Convenience helper called from graph.py
# ---------------------------------------------------------------------------

def record_node_trace(
    *,
    node_name: str,
    start_time: str,
    end_time: str,
    duration_ms: float,
    input_size: int,
    output_size: int,
    success: bool,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Append a ``NodeTrace`` to the current request's ``RunTrace``.

    If no tracer is active (e.g. when running evals without a request
    context), this is a no-op.
    """
    tracer = get_current_tracer()
    if tracer is None:
        return

    tracer.nodes.append(
        NodeTrace(
            node_name=node_name,
            start_time=start_time,
            end_time=end_time,
            duration_ms=round(duration_ms, 2),
            input_size=input_size,
            output_size=output_size,
            success=success,
            error=error,
            extra=extra or {},
        )
    )


# ---------------------------------------------------------------------------
# RunTrace factory and persistence
# ---------------------------------------------------------------------------

def new_run_trace(request_id: str) -> RunTrace:
    """Create a fresh ``RunTrace`` for a new request."""
    return RunTrace(
        request_id=request_id,
        run_start=datetime.now(timezone.utc).isoformat(),
    )


def finish_run_trace(tracer: RunTrace) -> None:
    """Mark the run as complete (sets run_end and total_duration_ms)."""
    now = datetime.now(timezone.utc)
    tracer.run_end = now.isoformat()
    try:
        start = datetime.fromisoformat(tracer.run_start)
        tracer.total_duration_ms = round(
            (now - start).total_seconds() * 1000, 2
        )
    except (ValueError, TypeError):
        pass


def save_run_trace(tracer: RunTrace, traces_dir: Path = TRACES_DIR) -> Path | None:
    """
    Serialise *tracer* to a JSON file under *traces_dir*.

    File path: ``traces/YYYYMMDD/{request_id_short}.json``

    Returns the path written, or ``None`` on failure (failures are logged but
    never propagated — tracing is best-effort).
    """
    try:
        # Date-bucketed subdirectory so the folder doesn't grow unbounded
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        day_dir = traces_dir / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        # Use first 8 chars of request_id for a human-scannable filename
        short_id = tracer.request_id[:8] if tracer.request_id else "unknown"
        out_path = day_dir / f"{short_id}.json"

        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(tracer.to_dict(), fh, indent=2, default=str)

        logger.debug(
            "Trace written: %s (%d nodes, %.0f ms total)",
            out_path,
            len(tracer.nodes),
            tracer.total_duration_ms or 0,
        )
        return out_path

    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write trace for %s: %s", tracer.request_id, exc)
        return None
