"""
Garden planner graph runner
============================
High-level entry point: accepts a :class:`~app.models.garden_request.GardenRequest`
ORM object, runs the LangGraph scoring pipeline, persists the results to the
database, and returns the saved :class:`~app.models.recommendation.Recommendation`
rows.

Usage
-----
::

    from app.models.garden_request import GardenRequest
    from agents.runner import run_garden_planner

    # garden_req is a fully committed GardenRequest ORM instance
    recommendations = await run_garden_planner(garden_req)
    # → list[Recommendation], ordered by rank (1-based)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.db import SessionLocal  # noqa: E402
from app.models.garden_request import GardenRequest  # noqa: E402
from app.models.recommendation import Recommendation  # noqa: E402
from app.models.species import Species  # noqa: E402
from agents.graph import GardenPlannerState, get_compiled_graph  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def run_garden_planner(garden_request: GardenRequest) -> list[Recommendation]:
    """
    Run the full multi-agent garden planner pipeline for *garden_request*.

    Steps
    -----
    1. Serialize the ORM object to a plain dict for the LangGraph state.
    2. Invoke the compiled graph (async).  The graph:
       a. Fetches candidate species from the DB.
       b. Runs 5 Claude scoring agents in parallel.
       c. Merges and ranks results, returning the top 20.
    3. Resolve each recommended scientific name to its Species DB row.
    4. Persist Recommendation rows and return them, ordered by rank.

    Parameters
    ----------
    garden_request:
        A fully committed (refreshed) GardenRequest ORM instance.

    Returns
    -------
    list[Recommendation]
        Persisted recommendations ordered by ``rank`` (1 = best match).
        May be shorter than 20 if some recommended species names cannot be
        resolved to DB rows (e.g. Claude hallucinated a name — logged as a
        warning).

    Raises
    ------
    Exception
        Any unhandled graph or DB error propagates to the caller.
    """
    request_id = str(garden_request.id)
    logger.info("run_garden_planner: starting for garden_request=%s", request_id)

    # ── 1. Build initial state ────────────────────────────────────────────
    initial_state: GardenPlannerState = {
        "garden_request": _request_to_dict(garden_request),
        "candidate_species": [],
        "pollinator_results": [],
        "insect_results": [],
        "soil_results": [],
        "environment_results": [],
        "food_utility_results": [],
        "final_recommendations": [],
        "errors": [],
    }

    # ── 2. Run the graph ──────────────────────────────────────────────────
    graph = get_compiled_graph()
    final_state: GardenPlannerState = await graph.ainvoke(initial_state)

    if final_state.get("errors"):
        logger.warning(
            "run_garden_planner: graph finished with %d error(s) for request=%s: %s",
            len(final_state["errors"]),
            request_id,
            "; ".join(final_state["errors"]),
        )

    recommendations_data: list[dict] = final_state.get("final_recommendations", [])
    if not recommendations_data:
        logger.warning(
            "run_garden_planner: no recommendations produced for request=%s.",
            request_id,
        )
        return []

    # ── 3 & 4. Resolve species → persist Recommendation rows ─────────────
    saved = await asyncio.to_thread(
        _persist_recommendations,
        garden_request.id,
        recommendations_data,
    )

    logger.info(
        "run_garden_planner: saved %d recommendation(s) for request=%s.",
        len(saved),
        request_id,
    )
    return saved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _request_to_dict(req: GardenRequest) -> dict:
    """Serialize a GardenRequest ORM object to a plain dict for the graph state."""
    return {
        "id": str(req.id),
        "latitude": req.latitude,
        "longitude": req.longitude,
        "country_code": req.country_code,
        "region": req.region,
        "priority_pollinators": req.priority_pollinators,
        "priority_insects": req.priority_insects,
        "priority_soil": req.priority_soil,
        "priority_environment": req.priority_environment,
        "priority_food_utility": req.priority_food_utility,
        "free_text_description": req.free_text_description,
    }


def _persist_recommendations(
    garden_request_id: Any,
    recommendations_data: list[dict],
) -> list[Recommendation]:
    """
    Synchronous helper (run via ``asyncio.to_thread``):
    look up each recommended species by scientific name, create
    Recommendation rows, commit, and return them.
    """
    db = SessionLocal()
    try:
        # Build a name → Species.id lookup for all recommended names in one
        # query rather than N individual queries.
        names = [item["scientific_name"] for item in recommendations_data]
        species_rows = (
            db.query(Species.id, Species.scientific_name)
            .filter(Species.scientific_name.in_(names))
            .all()
        )
        species_map: dict[str, Any] = {row.scientific_name: row.id for row in species_rows}

        saved: list[Recommendation] = []
        for item in recommendations_data:
            sci_name = item["scientific_name"]
            species_id = species_map.get(sci_name)

            if species_id is None:
                logger.warning(
                    "_persist_recommendations: species '%s' not found in DB — "
                    "skipping (Claude may have altered the name).",
                    sci_name,
                )
                continue

            rec = Recommendation(
                garden_request_id=garden_request_id,
                species_id=species_id,
                final_score=item["final_score"],
                score_breakdown=item.get("score_breakdown"),
                agent_reasoning=item.get("agent_reasoning"),
                rank=item["rank"],
            )
            db.add(rec)
            saved.append(rec)

        db.commit()

        # Refresh so callers get fully populated ORM objects (with .id, .created_at, etc.)
        for rec in saved:
            db.refresh(rec)

        return saved

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
