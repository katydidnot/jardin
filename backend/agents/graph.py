"""
LangGraph garden planner — multi-agent scoring graph
=====================================================

Architecture
------------
Each of the 5 scoring nodes calls Claude (claude-sonnet-4-6) via
ChatAnthropic and returns a [{scientific_name, score, reasoning}] list.
merge_and_rank applies the user's priority weights and emits the top-20
species with their final scores and per-dimension breakdowns.

Fan-out / fan-in is implemented with direct parallel edges
(equivalent to Send() dispatch from fetch_candidates):

    builder.add_edge("fetch_candidates", "score_pollinators")
    builder.add_edge("fetch_candidates", "score_insects")
    ...
    builder.add_edge(SCORER_NODES, "merge_and_rank")   # list → barrier
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from operator import add
from typing import Annotated, Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Path bootstrap — allow import from repo root or backend/
# ---------------------------------------------------------------------------
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.db import SessionLocal  # noqa: E402
from app.models.species import Species  # noqa: E402
from agents.prompts import NODE_PROMPTS, VERSION as PROMPTS_VERSION  # noqa: E402
from core.logging import get_logger, log_agent_call  # noqa: E402
from core.resilience import with_retry  # noqa: E402
from core.tracing import record_node_trace  # noqa: E402

logger = logging.getLogger(__name__)
_slog = get_logger(__name__)  # structlog logger for rich structured events

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# claude-sonnet-4-20250514 (= claude-sonnet-4-0) is deprecated.
# claude-sonnet-4-6 is the current recommended Sonnet model.
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192   # scoring 50 species comfortably fits within 8K tokens
TEMPERATURE = 0     # deterministic JSON

SCORER_NODES = [
    "score_pollinators",
    "score_insects",
    "score_soil",
    "score_environment",
    "score_food_utility",
    "score_size",
]

# How many candidate species to pass to the scoring agents
CANDIDATE_LIMIT = 50


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class GardenPlannerState(TypedDict):
    garden_request: dict            # location + priority weights
    candidate_species: list[dict]   # top-N species from DB
    pollinator_results: list[dict]  # [{scientific_name, score, reasoning}]
    insect_results: list[dict]
    soil_results: list[dict]
    environment_results: list[dict]
    food_utility_results: list[dict]
    size_results: list[dict]        # space / container suitability
    final_recommendations: list[dict]
    # Annotated with `add` so parallel nodes accumulate errors rather than
    # overwriting each other's entries.
    errors: Annotated[list[str], add]


# ---------------------------------------------------------------------------
# Helpers — JSON parsing
# ---------------------------------------------------------------------------
def _parse_json_response(text: str, context: str = "") -> list[dict]:
    """
    Robustly extract a JSON array from a Claude response.

    Handles:
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - Leading/trailing prose around the array
    - Objects wrapped in a dict key (e.g. {"results": [...]})
    - Partial arrays — individually parses complete ``{...}`` objects as
      a last resort (logs a warning when triggered).

    Parameters
    ----------
    text:
        Raw string from Claude.
    context:
        Short label used in log messages to identify the calling agent.

    Raises
    ------
    ValueError
        If no JSON array can be recovered.
    """
    original = text

    # 1. Strip markdown code fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    # 2. Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass

    # 3. Find the first JSON array anywhere in the text
    array_match = re.search(r"\[[\s\S]*\]", text)
    if array_match:
        try:
            result = json.loads(array_match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 4. Last resort — extract individual {...} objects
    objects: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start : i + 1])
                    if isinstance(obj, dict):
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1

    if objects:
        logger.warning(
            "[%s] Partial JSON parse: recovered %d objects from incomplete response.",
            context,
            len(objects),
        )
        return objects

    raise ValueError(
        f"[{context}] Could not parse JSON array from response: "
        f"{original[:300]!r}"
    )


def _safe_score(value: Any, fallback: float = 0.5) -> float:
    """Clamp a score value to [0.0, 1.0], using *fallback* on failure."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Helpers — LLM factory
# ---------------------------------------------------------------------------
def _llm() -> ChatAnthropic:
    """Return a ChatAnthropic instance configured for structured scoring."""
    return ChatAnthropic(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )


# ---------------------------------------------------------------------------
# Prompt version logging — emitted once at graph-compile time
# ---------------------------------------------------------------------------
logger.info("agents/graph.py loaded — prompts version: %s", PROMPTS_VERSION)


# ---------------------------------------------------------------------------
# Helpers — species formatting for prompts
# ---------------------------------------------------------------------------
def _format_species_list(species: list[dict]) -> str:
    """
    Produce a compact JSON list of species for inclusion in a scoring prompt.

    Keeps only the fields relevant to ecological scoring (name, family,
    common names, description) and truncates description to 200 chars to
    stay within sensible prompt budgets.
    """
    compact = [
        {
            "scientific_name": s["scientific_name"],
            "family": s.get("family") or "Unknown",
            "common_names": (s.get("common_names") or [])[:4],
            "description": (s.get("description") or "")[:200],
        }
        for s in species
    ]
    return json.dumps(compact, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Helpers — generic scoring agent call
# ---------------------------------------------------------------------------
@with_retry(max_attempts=3, backoff=2.0)
async def _run_scoring_agent(
    node_name: str,
    system_prompt: str,
    candidate_species: list[dict],
    garden_request: dict,
) -> list[dict]:
    """
    Call Claude with a domain-specific system prompt and the candidate species
    list.  Returns a validated list of ``{scientific_name, score, reasoning}``
    dicts, one per species.

    Decorated with ``@with_retry`` so transient 429 / connection errors are
    retried up to 3 times with exponential backoff before propagating.

    Also emits:
    - A structured ``agent_call`` log event (via structlog)
    - A ``NodeTrace`` appended to the current request's ``RunTrace``
    """
    if not candidate_species:
        logger.warning("[%s] No candidates to score — returning empty list.", node_name)
        return []

    request_id: str | None = garden_request.get("id")

    location_ctx = (
        f"Country: {garden_request.get('country_code') or 'unspecified'}. "
        f"Coordinates: ({garden_request.get('latitude', 0):.4f}, "
        f"{garden_request.get('longitude', 0):.4f}). "
        f"Extra context: {garden_request.get('free_text_description') or 'none'}."
    )

    user_content = (
        f"Location context: {location_ctx}\n\n"
        f"Score the following {len(candidate_species)} plant species:\n\n"
        f"{_format_species_list(candidate_species)}\n\n"
        "Return a JSON array — one object per species — with exactly these keys:\n"
        '  "scientific_name": string (copy exactly from the input)\n'
        '  "score": float 0.0–1.0\n'
        '  "reasoning": string (1–2 sentences)\n\n'
        "Include every species from the input list, even if you must estimate."
    )

    t0 = time.perf_counter()
    start_time = datetime.now(timezone.utc).isoformat()
    error_str: str | None = None
    validated: list[dict] = []

    try:
        llm = _llm()
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_content),
            ]
        )
        elapsed = time.perf_counter() - t0

        # Extract token usage from langchain_anthropic response metadata
        usage_meta = getattr(response, "response_metadata", {}).get("usage", {})
        token_usage: dict[str, int] | None = (
            {
                "input":  usage_meta.get("input_tokens", 0),
                "output": usage_meta.get("output_tokens", 0),
            }
            if usage_meta
            else None
        )

        raw_text: str = response.content  # type: ignore[assignment]
        scored = _parse_json_response(raw_text, context=node_name)

        # Normalise and validate each entry
        for item in scored:
            if not isinstance(item, dict) or "scientific_name" not in item:
                continue
            validated.append(
                {
                    "scientific_name": str(item["scientific_name"]),
                    "score": _safe_score(item.get("score", 0.5)),
                    "reasoning": str(item.get("reasoning", "")),
                }
            )

        # ── Structured log event ───────────────────────────────────────────
        log_agent_call(
            _slog,
            agent_name=node_name,
            request_id=request_id,
            duration_ms=elapsed * 1000,
            species_count=len(validated),
            token_usage=token_usage,
        )
        logger.info(
            "[%s] Scored %d/%d species in %.2fs.",
            node_name,
            len(validated),
            len(candidate_species),
            elapsed,
        )

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        error_str = str(exc)
        log_agent_call(
            _slog,
            agent_name=node_name,
            request_id=request_id,
            duration_ms=elapsed * 1000,
            species_count=0,
            error=error_str,
        )
        raise

    finally:
        # ── Node trace (best-effort — never raises) ────────────────────────
        end_time = datetime.now(timezone.utc).isoformat()
        record_node_trace(
            node_name=node_name,
            start_time=start_time,
            end_time=end_time,
            duration_ms=(time.perf_counter() - t0) * 1000,
            input_size=len(candidate_species),
            output_size=len(validated),
            success=error_str is None,
            error=error_str,
            extra={"prompts_version": PROMPTS_VERSION},
        )

    return validated


# ---------------------------------------------------------------------------
# Node 1 — fetch_candidates
# ---------------------------------------------------------------------------
async def fetch_candidates(state: GardenPlannerState) -> dict:
    """
    Query the DB for up to CANDIDATE_LIMIT species.

    Ranking strategy:
      - Average of the five ecological scores (rough quality signal).
      - If a country_code is present in the garden_request, prefer species
        whose native_regions JSONB contains that code (not yet populated by
        the GBIF loader — included for forward-compatibility).
    """
    req = state["garden_request"]
    country_code: str | None = req.get("country_code")
    t0 = time.perf_counter()

    def _query_db() -> list[dict]:
        db = SessionLocal()
        try:
            from sqlalchemy import Float, cast, func, text  # noqa: PLC0415

            # Rough average score across all five dimensions
            avg_score = (
                func.coalesce(cast(Species.pollinator_score, Float), 0.5)
                + func.coalesce(cast(Species.insect_host_score, Float), 0.5)
                + func.coalesce(cast(Species.soil_benefit_score, Float), 0.5)
                + func.coalesce(cast(Species.environmental_score, Float), 0.5)
                + func.coalesce(cast(Species.food_utility_score, Float), 0.5)
            ) / 5.0

            query = db.query(Species).order_by(avg_score.desc()).limit(CANDIDATE_LIMIT)
            rows = query.all()

            return [
                {
                    "id": str(row.id),
                    "scientific_name": row.scientific_name,
                    "common_names": row.common_names or [],
                    "family": row.family,
                    "description": row.description or "",
                    "pollinator_score": row.pollinator_score or 0.5,
                    "insect_host_score": row.insect_host_score or 0.5,
                    "soil_benefit_score": row.soil_benefit_score or 0.5,
                    "environmental_score": row.environmental_score or 0.5,
                    "food_utility_score": row.food_utility_score or 0.5,
                    "food_utility_notes": row.food_utility_notes or "",
                }
                for row in rows
            ]
        finally:
            db.close()

    try:
        candidates = await asyncio.to_thread(_query_db)
        logger.info(
            "fetch_candidates: retrieved %d species in %.2fs (country=%s).",
            len(candidates),
            time.perf_counter() - t0,
            country_code or "any",
        )
        return {"candidate_species": candidates}
    except Exception as exc:  # noqa: BLE001
        msg = f"fetch_candidates DB error: {exc}"
        logger.error(msg, exc_info=True)
        return {"candidate_species": [], "errors": [msg]}


# ---------------------------------------------------------------------------
# Nodes 2–6 — scoring agents
#
# Each node delegates to _run_scoring_agent, passing the correct system
# prompt from agents/prompts.py (imported as NODE_PROMPTS).
# ---------------------------------------------------------------------------
async def score_pollinators(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_pollinators",
            system_prompt=NODE_PROMPTS["score_pollinators"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"pollinator_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_pollinators error: {exc}"
        logger.error(msg, exc_info=True)
        return {"pollinator_results": [], "errors": [msg]}


async def score_insects(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_insects",
            system_prompt=NODE_PROMPTS["score_insects"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"insect_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_insects error: {exc}"
        logger.error(msg, exc_info=True)
        return {"insect_results": [], "errors": [msg]}


async def score_soil(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_soil",
            system_prompt=NODE_PROMPTS["score_soil"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"soil_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_soil error: {exc}"
        logger.error(msg, exc_info=True)
        return {"soil_results": [], "errors": [msg]}


async def score_environment(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_environment",
            system_prompt=NODE_PROMPTS["score_environment"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"environment_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_environment error: {exc}"
        logger.error(msg, exc_info=True)
        return {"environment_results": [], "errors": [msg]}


async def score_food_utility(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_food_utility",
            system_prompt=NODE_PROMPTS["score_food_utility"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"food_utility_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_food_utility error: {exc}"
        logger.error(msg, exc_info=True)
        return {"food_utility_results": [], "errors": [msg]}


async def score_size(state: GardenPlannerState) -> dict:
    try:
        results = await _run_scoring_agent(
            node_name="score_size",
            system_prompt=NODE_PROMPTS["score_size"],
            candidate_species=state["candidate_species"],
            garden_request=state["garden_request"],
        )
        return {"size_results": results}
    except Exception as exc:  # noqa: BLE001
        msg = f"score_size error: {exc}"
        logger.error(msg, exc_info=True)
        return {"size_results": [], "errors": [msg]}


# ---------------------------------------------------------------------------
# Node 7 — merge_and_rank  (pure Python, no LLM call)
# ---------------------------------------------------------------------------
async def merge_and_rank(state: GardenPlannerState) -> dict:
    """
    Apply priority weights from the garden_request to all 5 scored lists,
    compute a weighted final score for each species, and return the top 20
    sorted by descending final_score.

    Weight normalisation: raw weights are divided by their sum so varying
    priority scales (e.g. all 1.0 vs. all 0.2) produce the same ranking.
    A species absent from a dimension's result list receives a neutral 0.5.
    """
    req = state["garden_request"]

    raw_weights = {
        "pollinators":   float(req.get("priority_pollinators", 0.5)),
        "insects":       float(req.get("priority_insects", 0.5)),
        "soil":          float(req.get("priority_soil", 0.5)),
        "environment":   float(req.get("priority_environment", 0.5)),
        "food_utility":  float(req.get("priority_food_utility", 0.5)),
        "size":          float(req.get("priority_size", 0.5)),
    }
    total = sum(raw_weights.values()) or 1.0
    weights = {k: v / total for k, v in raw_weights.items()}

    # Build per-dimension lookup: {scientific_name: {score, reasoning}}
    dimension_maps: dict[str, dict[str, dict]] = {
        "pollinators":  {r["scientific_name"]: r for r in state.get("pollinator_results", [])},
        "insects":      {r["scientific_name"]: r for r in state.get("insect_results", [])},
        "soil":         {r["scientific_name"]: r for r in state.get("soil_results", [])},
        "environment":  {r["scientific_name"]: r for r in state.get("environment_results", [])},
        "food_utility": {r["scientific_name"]: r for r in state.get("food_utility_results", [])},
        "size":         {r["scientific_name"]: r for r in state.get("size_results", [])},
    }

    # Union of all scored species names
    all_names: set[str] = set()
    for dim_map in dimension_maps.values():
        all_names.update(dim_map.keys())

    if not all_names:
        logger.warning("merge_and_rank: no scored species — returning empty recommendations.")
        return {"final_recommendations": []}

    ranked: list[dict] = []
    for name in all_names:
        final_score = 0.0
        score_breakdown: dict[str, float] = {}
        agent_reasoning: dict[str, str] = {}

        for dim, w in weights.items():
            entry = dimension_maps[dim].get(name)
            score = _safe_score(entry["score"]) if entry else 0.5
            reasoning = entry.get("reasoning", "") if entry else "Not scored"
            score_breakdown[dim] = round(score, 4)
            agent_reasoning[dim] = reasoning
            final_score += score * w

        ranked.append(
            {
                "scientific_name": name,
                "final_score": round(final_score, 4),
                "score_breakdown": score_breakdown,
                "agent_reasoning": agent_reasoning,
            }
        )

    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    top_20 = ranked[:20]
    for i, item in enumerate(top_20, start=1):
        item["rank"] = i

    logger.info(
        "merge_and_rank: ranked %d species, returning top %d. "
        "Best: %s (%.4f), worst in top-20: %s (%.4f).",
        len(ranked),
        len(top_20),
        top_20[0]["scientific_name"] if top_20 else "–",
        top_20[0]["final_score"] if top_20 else 0.0,
        top_20[-1]["scientific_name"] if top_20 else "–",
        top_20[-1]["final_score"] if top_20 else 0.0,
    )
    return {"final_recommendations": top_20}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def _build_graph() -> StateGraph:
    """
    Construct the GardenPlannerState graph.

    Fan-out from fetch_candidates → 5 scoring nodes (run in parallel).
    Fan-in:  all 5 scoring nodes → merge_and_rank (barrier).
    """
    builder = StateGraph(GardenPlannerState)

    # Register nodes
    builder.add_node("fetch_candidates", fetch_candidates)
    builder.add_node("score_pollinators", score_pollinators)
    builder.add_node("score_insects", score_insects)
    builder.add_node("score_soil", score_soil)
    builder.add_node("score_environment", score_environment)
    builder.add_node("score_food_utility", score_food_utility)
    builder.add_node("score_size", score_size)
    builder.add_node("merge_and_rank", merge_and_rank)

    # Entry point
    builder.set_entry_point("fetch_candidates")

    # Fan-out: fetch_candidates triggers all 5 scoring nodes simultaneously.
    # (Equivalent to returning [Send(node, state) for node in SCORER_NODES]
    # from a conditional_edge on fetch_candidates.)
    for scorer in SCORER_NODES:
        builder.add_edge("fetch_candidates", scorer)

    # Fan-in: merge_and_rank only runs after ALL 5 scoring nodes complete.
    builder.add_edge(SCORER_NODES, "merge_and_rank")
    builder.add_edge("merge_and_rank", END)

    return builder


# ---------------------------------------------------------------------------
# Compiled graph singleton
# ---------------------------------------------------------------------------
_compiled_graph = None
_graph_lock = asyncio.Lock() if False else None  # replaced below at module load


def get_compiled_graph():
    """
    Return the compiled LangGraph application, building it on first call.

    The compiled graph is module-level cached — compilation is lightweight
    but there is no benefit to repeating it.
    """
    global _compiled_graph
    if _compiled_graph is None:
        logger.info("Compiling GardenPlanner LangGraph…")
        _compiled_graph = _build_graph().compile()
        logger.info("Graph compiled.")
    return _compiled_graph
