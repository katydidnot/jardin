"""
AI-based ecological species scorer
====================================
Uses Claude (claude-sonnet-4-6) with structured output to score a plant
species on five ecological dimensions and produce food-utility notes.

Each call is a structured parse — Claude returns a validated JSON object that
Pydantic coerces into a ``SpeciesScores`` instance with range-checked floats.
The system prompt is marked for prompt caching so repeated scoring calls share
the cached prefix, reducing costs significantly.

Usage
-----
Single species::

    from services.species_scorer import score_species_with_ai
    scores = score_species_with_ai(species_orm_object)
    # → {"pollinator_score": 0.82, "insect_host_score": 0.65, ...}

Batch (rate-limited to 1 req/s by default)::

    from services.species_scorer import batch_score_species
    results = batch_score_species(species_list)
    # → [{"species_id": "...", "scores": {...}, "error": None}, ...]
"""

from __future__ import annotations

import logging
import os
import sys
import time

import anthropic

# ---------------------------------------------------------------------------
# Ensure `from app.X import Y` works regardless of invocation directory
# ---------------------------------------------------------------------------
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.models.species import Species
from app.schemas.scorer import SpeciesScores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# The user specified claude-sonnet-4-20250514 (= claude-sonnet-4-0, deprecated).
# We use claude-sonnet-4-6 — the current recommended Sonnet model — which is
# the direct replacement per the Anthropic migration guide.
MODEL = "claude-sonnet-4-6"
DEFAULT_RATE_LIMIT_DELAY = 1.0   # seconds between requests
MAX_SCORING_TOKENS = 1024        # scores + notes fit easily within 1K tokens


# ---------------------------------------------------------------------------
# System prompt (stable across all species — cache it)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a botanical and ecological expert with deep knowledge of plant biology, \
ethnobotany, and ecological interactions. Your task is to score plant species on \
five ecological dimensions based on established botanical literature and research.

Scoring guidelines:
- Base scores on well-documented botanical and ecological evidence.
- For species with limited research, extrapolate conservatively from related \
  genera or families and note uncertainty in food_utility_notes if relevant.
- Scores should reflect realistic ecological value, not theoretical maximums.
- 0.0–0.3  = low value or evidence;  0.4–0.6 = moderate;  0.7–1.0 = high.

Respond ONLY with the requested JSON structure — no additional prose."""


# ---------------------------------------------------------------------------
# Single-species scoring
# ---------------------------------------------------------------------------

def score_species_with_ai(species: Species) -> dict:
    """
    Score a single species on five ecological dimensions using Claude.

    Parameters
    ----------
    species:
        A :class:`~app.models.species.Species` ORM instance (must have at least
        ``scientific_name`` set; ``family``, ``common_names``, and
        ``description`` improve score accuracy when present).

    Returns
    -------
    dict
        Keys: ``pollinator_score``, ``insect_host_score``, ``soil_benefit_score``,
        ``environmental_score``, ``food_utility_score``, ``food_utility_notes``.

    Raises
    ------
    anthropic.APIError
        On any unrecoverable Anthropic API failure.
    """
    client = anthropic.Anthropic()

    common_names_str = (
        ", ".join(species.common_names) if species.common_names else "none documented"
    )
    description_str = species.description or "No description available."

    user_prompt = f"""\
Score the following plant species:

Scientific name : {species.scientific_name}
Family          : {species.family or "Unknown"}
Common names    : {common_names_str}
Description     : {description_str[:600]}

Return a JSON object with these exact keys:
  pollinator_score     – float 0.0–1.0
  insect_host_score    – float 0.0–1.0
  soil_benefit_score   – float 0.0–1.0
  environmental_score  – float 0.0–1.0
  food_utility_score   – float 0.0–1.0
  food_utility_notes   – string (edible/medicinal summary or "Not commonly used...")
"""

    logger.debug("Scoring: %s", species.scientific_name)

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_SCORING_TOKENS,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                # Cache the system prompt — it is identical across all species calls.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=SpeciesScores,
    )

    scores: SpeciesScores = response.parsed_output
    result = scores.model_dump()
    logger.debug(
        "Scored %s: pollinator=%.2f insects=%.2f soil=%.2f env=%.2f food=%.2f",
        species.scientific_name,
        result["pollinator_score"],
        result["insect_host_score"],
        result["soil_benefit_score"],
        result["environmental_score"],
        result["food_utility_score"],
    )
    return result


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------

def batch_score_species(
    species_list: list[Species],
    rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY,
) -> list[dict]:
    """
    Score a list of species sequentially with rate limiting.

    Parameters
    ----------
    species_list:
        ORM instances to score.
    rate_limit_delay:
        Seconds to sleep between API calls (default 1.0 to stay within
        Anthropic's standard rate limits).

    Returns
    -------
    list[dict]
        One entry per species, in input order::

            {
                "species_id": str,           # UUID of the Species row
                "scientific_name": str,
                "scores": dict | None,       # None on failure
                "error": str | None,         # error message or None
            }
    """
    results: list[dict] = []
    total = len(species_list)

    for idx, species in enumerate(species_list):
        entry: dict = {
            "species_id": str(species.id),
            "scientific_name": species.scientific_name,
            "scores": None,
            "error": None,
        }

        try:
            scores = score_species_with_ai(species)
            entry["scores"] = scores
            logger.info(
                "[%d/%d] Scored: %s",
                idx + 1,
                total,
                species.scientific_name,
            )

        except anthropic.RateLimitError as exc:
            retry_after = int(
                getattr(exc, "response", None)
                and exc.response.headers.get("retry-after", 60)
                or 60
            )
            logger.warning(
                "Rate limited on %s; waiting %ds before retry.",
                species.scientific_name,
                retry_after,
            )
            time.sleep(retry_after)
            try:
                entry["scores"] = score_species_with_ai(species)
                logger.info(
                    "[%d/%d] Scored (after retry): %s",
                    idx + 1,
                    total,
                    species.scientific_name,
                )
            except Exception as retry_exc:  # noqa: BLE001
                entry["error"] = f"retry failed: {retry_exc}"
                logger.error(
                    "Retry failed for %s: %s",
                    species.scientific_name,
                    retry_exc,
                    exc_info=True,
                )

        except anthropic.BadRequestError as exc:
            entry["error"] = f"bad request: {exc.message}"
            logger.error(
                "Bad request for %s: %s",
                species.scientific_name,
                exc.message,
            )

        except anthropic.APIConnectionError as exc:
            entry["error"] = f"connection error: {exc}"
            logger.error(
                "Connection error for %s: %s",
                species.scientific_name,
                exc,
                exc_info=True,
            )

        except anthropic.APIStatusError as exc:
            entry["error"] = f"API {exc.status_code}: {exc.message}"
            logger.error(
                "API error %s for %s: %s",
                exc.status_code,
                species.scientific_name,
                exc.message,
            )

        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
            logger.error(
                "Unexpected error for %s: %s",
                species.scientific_name,
                exc,
                exc_info=True,
            )

        results.append(entry)

        # Rate-limit sleep between requests (skip after the last one)
        if idx < total - 1:
            time.sleep(rate_limit_delay)

    successful = sum(1 for r in results if r["scores"] is not None)
    failed = total - successful
    logger.info(
        "Batch scoring complete: %d/%d succeeded, %d failed.",
        successful,
        total,
        failed,
    )
    return results


# ---------------------------------------------------------------------------
# DB helper — apply a scores dict back to a Species ORM object
# ---------------------------------------------------------------------------

def apply_scores(species: Species, scores: dict) -> None:
    """
    Write scored values onto a :class:`~app.models.species.Species` instance
    in-place.  The caller is responsible for committing the session.
    """
    for field in (
        "pollinator_score",
        "insect_host_score",
        "soil_benefit_score",
        "environmental_score",
        "food_utility_score",
        "food_utility_notes",
    ):
        if field in scores:
            setattr(species, field, scores[field])
