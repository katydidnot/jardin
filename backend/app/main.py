"""
Jardin API — application entry point
======================================
Run from the ``backend/`` directory:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sql_text

from app.db import SessionLocal
from app.routers import chat, recommendations
from core.logging import configure_logging

# Initialise logging before anything else so all subsequent imports log JSON
configure_logging()

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Lifespan — warm up the embedding model before serving requests
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """
    Pre-load the sentence-transformers model at startup so the first
    /api/species/search request isn't slow from a cold model download.
    """
    logger.info("Startup: warming up sentence-transformers model…")
    try:
        from services.gbif_loader import _get_st_model  # noqa: PLC0415

        model = await asyncio.to_thread(_get_st_model)
        if model is not None:
            logger.info("Embedding model ready.")
        else:
            logger.warning(
                "Embedding model failed to load — semantic search will use "
                "placeholder embeddings."
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Embedding warm-up error: %s", exc)

    yield
    # Nothing to tear down — model is held in memory by gbif_loader singleton.


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Jardin Garden Planner API",
    description=(
        "AI-powered native plant recommendation system. "
        "Combines GBIF species data, semantic embeddings, and a LangGraph "
        "multi-agent scoring pipeline to suggest ecologically valuable plants."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

# Allow the Next.js dev server and any Docker-mapped host
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(recommendations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


# ---------------------------------------------------------------------------
# Health check  GET /health
# ---------------------------------------------------------------------------

async def _check_db() -> str:
    """Run a trivial SELECT against Postgres.  Returns 'connected' or an error."""
    def _probe() -> None:
        db = SessionLocal()
        try:
            db.execute(sql_text("SELECT 1"))
        finally:
            db.close()

    try:
        await asyncio.to_thread(_probe)
        return "connected"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check — DB probe failed: %s", exc)
        return f"error: {exc}"


async def _check_anthropic() -> str:
    """
    Hit the Anthropic models list endpoint (GET /v1/models?limit=1).
    This is a cheap read-only call that confirms the API key is valid and
    the Anthropic API is reachable.
    Returns 'reachable' or an error string.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "error: ANTHROPIC_API_KEY not set"

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        # list() with a small limit is the cheapest authenticated call
        await client.models.list()
        return "reachable"
    except anthropic.AuthenticationError:
        return "error: invalid API key"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check — Anthropic probe failed: %s", exc)
        return f"error: {exc}"


@app.get(
    "/health",
    tags=["system"],
    summary="Readiness probe",
    description=(
        "Checks database connectivity and Anthropic API reachability. "
        "Returns HTTP 200 with a status payload even when sub-checks fail, "
        "so orchestrators can distinguish 'app is up but degraded' from "
        "'app is down'.  Returns HTTP 503 only if the app itself is crashing."
    ),
)
async def health() -> dict:
    """
    Returns::

        {
          "status":    "ok" | "degraded",
          "db":        "connected" | "error: ...",
          "anthropic": "reachable" | "error: ...",
          "version":   "0.1.0"
        }
    """
    db_status, anthropic_status = await asyncio.gather(
        _check_db(),
        _check_anthropic(),
    )

    overall = (
        "ok"
        if db_status == "connected" and anthropic_status == "reachable"
        else "degraded"
    )

    return {
        "status":    overall,
        "db":        db_status,
        "anthropic": anthropic_status,
        "version":   APP_VERSION,
    }
