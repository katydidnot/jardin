"""
Chat router
============
POST /api/chat

A RAG-style streaming endpoint: loads a species record from the DB, injects
its full ecological profile into Claude's context, then streams Claude's
response back as Server-Sent Events.

The client sends an optional conversation `history` (list of prior turns)
so multi-turn Q&A works without the client managing the full prompt.

SSE event shapes::

    {"text": "...token..."}   # streamed text delta
    {"event": "done"}         # stream complete
    {"event": "error",  "message": "..."}
"""

from __future__ import annotations

import json
import logging

import anthropic
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.db import SessionLocal
from app.models.species import Species
from app.schemas.chat import ChatMessage, ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

# claude-sonnet-4-6 is used here for quality; swap to claude-haiku-4-5 for
# lower-latency / lower-cost if chat responsiveness matters more than depth.
_CHAT_MODEL = "claude-sonnet-4-6"
_CHAT_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------
def _build_system_prompt(species: Species) -> str:
    common_names = (
        ", ".join(species.common_names[:6])
        if species.common_names
        else "none documented"
    )

    def _fmt_score(v: float | None) -> str:
        if v is None:
            return "not scored"
        label = (
            "low" if v < 0.35
            else "moderate" if v < 0.65
            else "high"
        )
        return f"{v:.2f} ({label})"

    return f"""\
You are a knowledgeable and approachable garden-planning assistant with deep \
expertise in botany, ecology, and sustainable gardening.

You are currently answering questions about the following plant species:

────────────────────────────────────────────────────
Scientific name : {species.scientific_name}
Family          : {species.family or "Unknown"}
Common names    : {common_names}
────────────────────────────────────────────────────
Description
{species.description or "No description on file."}

────────────────────────────────────────────────────
Ecological scores  (0 = low value · 1 = high value)
  Pollinator value   : {_fmt_score(species.pollinator_score)}
  Insect host plant  : {_fmt_score(species.insect_host_score)}
  Soil health benefit: {_fmt_score(species.soil_benefit_score)}
  Environmental svc  : {_fmt_score(species.environmental_score)}
  Food / medicinal   : {_fmt_score(species.food_utility_score)}

Food & medicinal notes
{species.food_utility_notes or "None on file."}
────────────────────────────────────────────────────

Guidelines:
- Ground your answers in the species data above when relevant.
- For questions outside the provided data, draw on your botanical knowledge \
and note when you are supplementing beyond the record.
- Keep answers concise but complete; use markdown lists or headers when \
they aid clarity.
- If asked about growing conditions, pests, companions, or care, give \
practical advice suited to a home garden context.
- Never fabricate scores or citations."""


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------
@router.post(
    "/chat",
    summary="Stream a conversational answer about a species",
    description=(
        "Loads the species record, builds a RAG system prompt, then streams "
        "Claude's response back as SSE text deltas. Pass prior turns in "
        "`history` for multi-turn conversations."
    ),
)
async def chat(body: ChatRequest) -> EventSourceResponse:
    # ── 1. Load species synchronously (fast, single-row lookup) ────────────
    db = SessionLocal()
    try:
        species = db.query(Species).filter(Species.id == body.species_id).first()
        if species is None:
            raise HTTPException(status_code=404, detail="Species not found")
        system_prompt = _build_system_prompt(species)
    finally:
        db.close()

    # ── 2. Build messages list: history + current user message ─────────────
    messages: list[dict] = [
        {"role": msg.role, "content": msg.content}
        for msg in body.history
    ]
    messages.append({"role": "user", "content": body.message})

    # ── 3. Stream Claude's response as SSE ─────────────────────────────────
    async def event_generator():
        client = anthropic.AsyncAnthropic()
        try:
            async with client.messages.stream(
                model=_CHAT_MODEL,
                max_tokens=_CHAT_MAX_TOKENS,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text_delta in stream.text_stream:
                    yield {"data": json.dumps({"text": text_delta})}

            yield {"data": json.dumps({"event": "done"})}

        except anthropic.RateLimitError as exc:
            logger.warning("Chat rate-limited: %s", exc)
            yield {
                "data": json.dumps({
                    "event": "error",
                    "message": "Rate limit reached — please try again in a moment.",
                })
            }
        except anthropic.APIStatusError as exc:
            logger.error("Chat API error %s: %s", exc.status_code, exc.message)
            yield {
                "data": json.dumps({
                    "event": "error",
                    "message": f"API error {exc.status_code}: {exc.message}",
                })
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Chat unexpected error: %s", exc, exc_info=True)
            yield {
                "data": json.dumps({
                    "event": "error",
                    "message": "An unexpected error occurred.",
                })
            }

    return EventSourceResponse(event_generator())
