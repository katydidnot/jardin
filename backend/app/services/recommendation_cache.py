"""
app/services/recommendation_cache.py
======================================
Content-based caching for recommendation requests.

When a user submits a garden planning request with identical parameters
(same location, same sliders), this service detects the match and replays
the previous results instantly — no agent pipeline needed.

Hash strategy
-------------
A 16-char SHA-256 prefix of a normalised parameter string:
- lat/lon rounded to 3 d.p. (~100 m precision)
- priority weights rounded to 2 d.p.
- country code uppercased
- description stripped, lowercased, and trimmed

A cache hit is only valid when the prior run produced ≥ 10 recommendations
(incomplete/errored runs are not served).
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy.orm import Session

from app.models.garden_request import GardenRequest
from app.models.recommendation import Recommendation
from app.models.species import Species
from app.schemas.recommendations import RecommendationRequest

logger = logging.getLogger(__name__)


def compute_content_hash(body: RecommendationRequest) -> str:
    """
    Return a 16-char hex digest that uniquely identifies a set of request
    parameters.

    Parameters
    ----------
    body:
        Validated recommendation request.

    Returns
    -------
    str
        16-character lowercase hexadecimal string.
    """
    key = (
        f"{body.latitude:.3f},{body.longitude:.3f},"
        f"{body.country_code.upper()},"
        f"{body.priority_pollinators:.2f},"
        f"{body.priority_insects:.2f},"
        f"{body.priority_soil:.2f},"
        f"{body.priority_environment:.2f},"
        f"{body.priority_food_utility:.2f},"
        f"{body.priority_size:.2f},"
        f"{(body.description or '').strip().lower()}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _species_row_to_dict(s: Species) -> dict:
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


def load_cached_recommendations(
    db: Session,
    content_hash: str,
    min_recs: int = 10,
) -> list[dict] | None:
    """
    Look up a prior completed request with the same content hash and return
    its enriched recommendations, or ``None`` if no valid cache entry exists.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    content_hash:
        16-char hex hash from ``compute_content_hash``.
    min_recs:
        Minimum number of recommendations a prior run must have produced
        to be considered a valid cache hit (filters out incomplete runs).

    Returns
    -------
    list[dict] | None
        Enriched recommendation dicts ready for SSE replay, or ``None``.
    """
    existing = (
        db.query(GardenRequest)
        .filter(GardenRequest.content_hash == content_hash)
        .order_by(GardenRequest.created_at.desc())
        .first()
    )
    if existing is None:
        return None

    recs = (
        db.query(Recommendation)
        .filter(Recommendation.garden_request_id == existing.id)
        .order_by(Recommendation.rank)
        .all()
    )
    if len(recs) < min_recs:
        logger.debug(
            "Cache miss: found request %s but only %d recommendations (need %d).",
            existing.id,
            len(recs),
            min_recs,
        )
        return None

    # Batch-load all species in a single query (avoid N+1)
    names = [r.species.scientific_name for r in recs]
    species_map: dict[str, Species] = {
        s.scientific_name: s
        for s in db.query(Species).filter(Species.scientific_name.in_(names)).all()
    }

    enriched: list[dict] = []
    for rec in recs:
        sci_name = rec.species.scientific_name
        species = species_map.get(sci_name)
        if species is None:
            logger.warning(
                "Cached recommendation references missing species '%s' — skipping.",
                sci_name,
            )
            continue
        enriched.append(
            {
                "rank":            rec.rank,
                "final_score":     rec.final_score,
                "score_breakdown": rec.score_breakdown,
                "agent_reasoning": rec.agent_reasoning,
                "species":         _species_row_to_dict(species),
            }
        )

    if len(enriched) < min_recs:
        return None

    logger.info(
        "Cache hit: hash=%s → %d recommendations from request %s.",
        content_hash,
        len(enriched),
        existing.id,
    )
    return enriched
