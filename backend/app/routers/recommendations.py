"""
Recommendations router
========================
POST /api/recommendations              — create a garden request, start the agent
GET  /api/recommendations/{id}/stream  — SSE stream of agent progress + results
GET  /api/species/search               — pgvector semantic search
GET  /api/species/{species_id}         — full species detail

SSE stream design
-----------------
POST returns 202 immediately and stores an ``asyncio.Queue`` keyed by request_id.
A ``BackgroundTask`` drives the LangGraph graph and writes events to the queue.
GET /stream reads from the queue and forwards events to the client.
The queue is cleaned up one hour after the task completes (or 5 min on cache hit).

Event shapes::

    {"event": "fetch_complete",        "candidate_count": 47}
    {"event": "agent_started",         "agent": "pollinators"}
    {"event": "agent_complete",        "agent": "pollinators", "count": 45}
    {"event": "recommendations_ready", "data": [{rank, final_score, species, …}]}
    {"event": "error",                 "message": "…"}
    {"event": "ping"}                  # keep-alive every 25 s of silence
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db import SessionLocal, get_db
from app.models.garden_request import GardenRequest
from app.models.recommendation import Recommendation
from app.models.species import Species
from app.schemas.recommendations import RecommendationCreated, RecommendationRequest, SpeciesOut
from app.services.recommendation_cache import (
    compute_content_hash,
    load_cached_recommendations,
)
from core.tracing import (
    finish_run_trace,
    new_run_trace,
    reset_current_tracer,
    save_run_trace,
    set_current_tracer,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["recommendations"])

# ---------------------------------------------------------------------------
# SSE queue registry — {request_id: asyncio.Queue}
# ---------------------------------------------------------------------------

_active_queues: dict[str, asyncio.Queue] = {}
_SENTINEL = object()  # signals end-of-stream

# ---------------------------------------------------------------------------
# Node → agent-name mappings
# ---------------------------------------------------------------------------

_NODE_AGENT_MAP: dict[str, str] = {
    "score_pollinators": "pollinators",
    "score_insects":     "insects",
    "score_soil":        "soil",
    "score_environment": "environment",
    "score_food_utility": "food_utility",
    "score_size":        "size",
}
_AGENT_RESULT_KEYS: dict[str, str] = {
    "pollinators":  "pollinator_results",
    "insects":      "insect_results",
    "soil":         "soil_results",
    "environment":  "environment_results",
    "food_utility": "food_utility_results",
    "size":         "size_results",
}
_AGENT_NAMES: list[str] = list(_NODE_AGENT_MAP.values())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _species_to_out(s: Species) -> dict:
    """Serialise a Species ORM row to the standard API dict shape."""
    return {
        "id": str(s.id),
        "scientific_name": s.scientific_name,
        "common_names": s.common_names or [],
        "family": s.family,
        "description": s.description,
        "food_utility_notes": s.food_utility_notes,
        "pollinator_score": s.pollinator_score,
        "insect_host_score": s.insect_host_score,
        "soil_benefit_score": s.soil_benefit_score,
        "environmental_score": s.environmental_score,
        "food_utility_score": s.food_utility_score,
        "bloom_months": s.bloom_months,
    }


def _save_and_enrich(
    garden_request_id: uuid.UUID,
    recommendations: list[dict],
) -> list[dict]:
    """
    Synchronous: persist Recommendation rows, then enrich each with its full
    Species data for the SSE payload.  Run via ``asyncio.to_thread``.
    """
    db = SessionLocal()
    try:
        names = [r["scientific_name"] for r in recommendations]
        species_map: dict[str, Species] = {
            s.scientific_name: s
            for s in db.query(Species).filter(Species.scientific_name.in_(names)).all()
        }

        enriched: list[dict] = []
        for item in recommendations:
            species = species_map.get(item["scientific_name"])
            if species is None:
                logger.warning(
                    "Recommended species '%s' not found in DB — skipping.",
                    item["scientific_name"],
                )
                continue

            db.add(
                Recommendation(
                    garden_request_id=garden_request_id,
                    species_id=species.id,
                    final_score=item["final_score"],
                    score_breakdown=item.get("score_breakdown"),
                    agent_reasoning=item.get("agent_reasoning"),
                    rank=item["rank"],
                )
            )
            enriched.append(
                {
                    "rank":            item["rank"],
                    "final_score":     item["final_score"],
                    "score_breakdown": item.get("score_breakdown"),
                    "agent_reasoning": item.get("agent_reasoning"),
                    "species":         _species_to_out(species),
                }
            )

        db.commit()
        return enriched

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Queue lifecycle helpers
# ---------------------------------------------------------------------------

async def _delayed_cleanup(request_id: str, delay: float = 3600.0) -> None:
    await asyncio.sleep(delay)
    _active_queues.pop(request_id, None)
    logger.debug("Cleaned up SSE queue for request %s.", request_id)


def _create_queue(request_id: str) -> asyncio.Queue:
    """Register a new empty queue for *request_id* and return it."""
    q: asyncio.Queue = asyncio.Queue()
    _active_queues[request_id] = q
    return q


# ---------------------------------------------------------------------------
# Background task — cached-result replay
# ---------------------------------------------------------------------------

async def _stream_cached_results(
    request_id: str,
    enriched: list[dict],
) -> None:
    """
    Immediately replay cached recommendations as SSE events so the UI
    receives the same event sequence as a live agent run.
    """
    queue = _active_queues.get(request_id)
    if queue is None:
        return

    try:
        await queue.put({"event": "fetch_complete", "candidate_count": len(enriched)})
        for agent in _AGENT_NAMES:
            await queue.put({"event": "agent_started", "agent": agent})
        for agent in _AGENT_NAMES:
            await queue.put({"event": "agent_complete", "agent": agent, "count": len(enriched)})
        await queue.put({"event": "recommendations_ready", "data": enriched})
    finally:
        await queue.put(_SENTINEL)
        asyncio.create_task(_delayed_cleanup(request_id, delay=300.0))


# ---------------------------------------------------------------------------
# Background task — live agent run
# ---------------------------------------------------------------------------

async def _handle_node_update(
    node_name: str,
    update: dict,
    garden_request_id: uuid.UUID,
    queue: asyncio.Queue,
) -> None:
    """Translate a LangGraph node-completion update to SSE events."""
    for err in update.get("errors", []):
        await queue.put({"event": "error", "message": err})

    if node_name == "fetch_candidates":
        count = len(update.get("candidate_species", []))
        await queue.put({"event": "fetch_complete", "candidate_count": count})
        for agent in _AGENT_NAMES:
            await queue.put({"event": "agent_started", "agent": agent})

    elif node_name in _NODE_AGENT_MAP:
        agent = _NODE_AGENT_MAP[node_name]
        count = len(update.get(_AGENT_RESULT_KEYS[agent], []))
        await queue.put({"event": "agent_complete", "agent": agent, "count": count})

    elif node_name == "merge_and_rank":
        recs = update.get("final_recommendations", [])
        if recs:
            enriched = await asyncio.to_thread(
                _save_and_enrich, garden_request_id, recs
            )
            await queue.put({"event": "recommendations_ready", "data": enriched})
        else:
            await queue.put(
                {"event": "error", "message": "Agent pipeline produced no recommendations."}
            )


async def _stream_planner(
    request_id: str,
    garden_request_id: uuid.UUID,
    garden_request_dict: dict,
) -> None:
    """
    BackgroundTask entry point.  Runs the LangGraph graph with ``astream()``,
    translating node-completion events into SSE payloads on the request queue.
    """
    from agents.graph import GardenPlannerState, get_compiled_graph  # noqa: PLC0415

    queue = _active_queues.get(request_id)
    if queue is None:
        logger.error("_stream_planner: no queue found for request %s", request_id)
        return

    tracer = new_run_trace(request_id=request_id)
    trace_token = set_current_tracer(tracer)

    try:
        graph = get_compiled_graph()
        initial_state: GardenPlannerState = {
            "garden_request":        garden_request_dict,
            "candidate_species":     [],
            "pollinator_results":    [],
            "insect_results":        [],
            "soil_results":          [],
            "environment_results":   [],
            "food_utility_results":  [],
            "size_results":          [],
            "final_recommendations": [],
            "errors":                [],
        }

        async for chunk in graph.astream(initial_state):
            for node_name, node_update in chunk.items():
                await _handle_node_update(
                    node_name, node_update, garden_request_id, queue
                )

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_stream_planner error for %s: %s", request_id, exc, exc_info=True
        )
        await queue.put({"event": "error", "message": str(exc)})

    finally:
        finish_run_trace(tracer)
        save_run_trace(tracer)
        reset_current_tracer(trace_token)
        await queue.put(_SENTINEL)
        # Keep queue alive for 1 h in case the client connects late / reconnects
        asyncio.create_task(_delayed_cleanup(request_id, delay=3600.0))


# ---------------------------------------------------------------------------
# POST /api/recommendations
# ---------------------------------------------------------------------------

@router.post(
    "/recommendations",
    status_code=202,
    response_model=RecommendationCreated,
    summary="Submit a garden planting request",
    description=(
        "Creates a GardenRequest record, starts the multi-agent scoring "
        "pipeline in the background, and returns a 202 with a ``request_id``. "
        "Connect to ``GET /recommendations/{request_id}/stream`` for live progress."
    ),
)
async def create_recommendation(
    body: RecommendationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RecommendationCreated:
    content_hash = compute_content_hash(body)

    # ── Cache check ────────────────────────────────────────────────────────
    cached = load_cached_recommendations(db, content_hash)
    if cached:
        request_id = str(uuid.uuid4())
        _create_queue(request_id)
        background_tasks.add_task(_stream_cached_results, request_id, cached)
        return RecommendationCreated(
            request_id=request_id,
            stream_url=f"/api/recommendations/{request_id}/stream",
        )

    # ── Fresh run ──────────────────────────────────────────────────────────
    garden_req = GardenRequest(
        id=uuid.uuid4(),
        latitude=body.latitude,
        longitude=body.longitude,
        country_code=body.country_code,
        region=body.region,
        priority_pollinators=body.priority_pollinators,
        priority_insects=body.priority_insects,
        priority_soil=body.priority_soil,
        priority_environment=body.priority_environment,
        priority_food_utility=body.priority_food_utility,
        priority_size=body.priority_size,
        content_hash=content_hash,
        free_text_description=body.description,
    )
    db.add(garden_req)
    db.commit()
    db.refresh(garden_req)

    request_id = str(garden_req.id)
    _create_queue(request_id)

    # Serialise to dict — the ORM session will be closed before the task runs
    garden_request_dict = {
        "id":                    request_id,
        "latitude":              garden_req.latitude,
        "longitude":             garden_req.longitude,
        "country_code":          garden_req.country_code,
        "region":                garden_req.region,
        "priority_pollinators":  garden_req.priority_pollinators,
        "priority_insects":      garden_req.priority_insects,
        "priority_soil":         garden_req.priority_soil,
        "priority_environment":  garden_req.priority_environment,
        "priority_food_utility": garden_req.priority_food_utility,
        "priority_size":         garden_req.priority_size,
        "free_text_description": garden_req.free_text_description,
    }

    background_tasks.add_task(
        _stream_planner, request_id, garden_req.id, garden_request_dict
    )

    return RecommendationCreated(
        request_id=request_id,
        stream_url=f"/api/recommendations/{request_id}/stream",
    )


# ---------------------------------------------------------------------------
# GET /api/recommendations/{request_id}/stream
# ---------------------------------------------------------------------------

@router.get(
    "/recommendations/{request_id}/stream",
    summary="Stream agent progress via Server-Sent Events",
    description=(
        "Opens an SSE connection. Events are emitted as each scoring agent "
        "completes, and the stream closes after ``recommendations_ready`` or "
        "``error``. A ``ping`` event is sent every 25 s to keep the connection "
        "alive through proxies."
    ),
)
async def stream_recommendations(
    request: Request,
    request_id: str,
) -> EventSourceResponse:
    queue = _active_queues.get(request_id)
    if queue is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Request not found. Either the request_id is wrong or "
                "the result stream has expired (TTL: 1 hour)."
            ),
        )

    async def event_generator():
        terminal_events = {"recommendations_ready", "error"}
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("SSE client disconnected for request %s", request_id)
                    return

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"event": "ping"})}
                    continue

                if item is _SENTINEL:
                    return

                yield {"data": json.dumps(item)}

                event_name = item.get("event") if isinstance(item, dict) else None
                if event_name in terminal_events:
                    return

        except asyncio.CancelledError:
            logger.info("SSE generator cancelled for request %s", request_id)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# GET /api/species/search
# (registered before /{species_id} to avoid the path parameter shadowing it)
# ---------------------------------------------------------------------------

@router.get(
    "/species/search",
    response_model=list[SpeciesOut],
    summary="Semantic species search via pgvector",
    description=(
        "Embeds the query with BAAI/bge-base-en-v1.5 and returns species "
        "ordered by cosine similarity. Falls back to an empty list if no "
        "embeddings are present in the database."
    ),
)
async def search_species(
    q: Annotated[str, Query(min_length=1, max_length=500, description="Free-text search query")],
    country: Annotated[str | None, Query(max_length=3, description="ISO country code filter")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict]:
    from services.gbif_loader import generate_embedding  # noqa: PLC0415

    query_embedding: list[float] | None = await asyncio.to_thread(generate_embedding, q)

    def _db_search() -> list[dict]:
        db = SessionLocal()
        try:
            if query_embedding is None:
                rows = (
                    db.query(Species)
                    .filter(
                        Species.scientific_name.ilike(f"%{q}%")
                        if q else Species.scientific_name.isnot(None)
                    )
                    .limit(limit)
                    .all()
                )
                return [_species_to_out(s) for s in rows]

            vec_str = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"
            stmt = sql_text(
                "SELECT id FROM species "
                "WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:vec AS vector) "
                "LIMIT :lim"
            )
            id_rows = db.execute(stmt, {"vec": vec_str, "lim": limit}).fetchall()
            if not id_rows:
                return []

            ids = [row.id for row in id_rows]
            id_order = {id_: i for i, id_ in enumerate(ids)}
            rows = db.query(Species).filter(Species.id.in_(ids)).all()
            rows.sort(key=lambda s: id_order.get(s.id, len(ids)))
            return [_species_to_out(s) for s in rows]

        finally:
            db.close()

    return await asyncio.to_thread(_db_search)


# ---------------------------------------------------------------------------
# GET /api/species/{species_id}
# ---------------------------------------------------------------------------

@router.get(
    "/species/{species_id}",
    response_model=SpeciesOut,
    summary="Full species detail",
)
def get_species(
    species_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    species = db.query(Species).filter(Species.id == species_id).first()
    if species is None:
        raise HTTPException(status_code=404, detail="Species not found")
    return _species_to_out(species)
