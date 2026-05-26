"""
GBIF species loader
===================
Fetches plant species for a country from the GBIF API, maps them to our
Species model, generates semantic embeddings, and persists them to the
database — skipping any scientific name that already exists.

Filtering strategy
------------------
``kingdomKey=6`` (Plantae) is silently **ignored** by GBIF's ``/species/search``
endpoint — it returns animals regardless.  The occurrence endpoint
(``/occurrence/search``) correctly honours ``kingdomKey=6``.

We therefore use a two-phase fetch:
1. ``/occurrence/search`` with ``kingdomKey=6`` + ``country=XX`` + facet on
   ``speciesKey`` — returns the most-observed plant species in the country.
2. ``/species/{key}`` for each species key — returns the full taxonomic record
   including ``scientificName``, ``family``, ``vernacularName``, etc.

As a belt-and-suspenders safeguard, ``map_gbif_to_species_fields`` also checks
the ``kingdom`` and ``phylum`` fields and silently drops any record that is
explicitly labelled as a non-plant kingdom (Animalia, Fungi, Bacteria, …).

Embedding notes
---------------
Embeddings are generated locally using the ``sentence-transformers`` library
with the ``BAAI/bge-base-en-v1.5`` model (768-dim, Apache 2.0 licence).
The model is downloaded from Hugging Face on first use and cached locally by
the sentence-transformers library (``~/.cache/torch/sentence_transformers``).
No API key or network access is required after the initial download.

A deterministic numpy placeholder is used as a fallback if the model fails
to load (development / CI use only — not semantically meaningful).

Environment variables
---------------------
GBIF_API_BASE   – GBIF base URL  (default: https://api.gbif.org/v1)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import sys
import threading

import httpx

# ---------------------------------------------------------------------------
# Make `from app.X import Y` work whether we are invoked from backend/ or
# from the repo root as `python -m backend.scripts.load_species`.
# ---------------------------------------------------------------------------
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.species import Species

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GBIF_API_BASE: str = os.environ.get("GBIF_API_BASE", "https://api.gbif.org/v1")
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"  # 768-dim, Apache 2.0
EMBEDDING_DIM = 768

_GBIF_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ---------------------------------------------------------------------------
# Sentence-transformers singleton (lazy, thread-safe)
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_st_model = None  # type: ignore[assignment]


def _get_st_model():
    """
    Return the SentenceTransformer model instance, loading it on first call.

    Thread-safe via a module-level lock.  The model is cached in
    ``~/.cache/torch/sentence_transformers`` by the sentence-transformers
    library after the initial download.
    """
    global _st_model
    if _st_model is not None:
        return _st_model

    with _model_lock:
        if _st_model is None:  # double-checked locking
            try:
                from sentence_transformers import SentenceTransformer  # noqa: PLC0415

                logger.info(
                    "Loading sentence-transformers model '%s' (first call)…",
                    EMBEDDING_MODEL,
                )
                _st_model = SentenceTransformer(EMBEDDING_MODEL)
                logger.info("Embedding model loaded successfully.")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to load sentence-transformers model '%s': %s. "
                    "Placeholder embeddings will be used.",
                    EMBEDDING_MODEL,
                    exc,
                )
                _st_model = None

    return _st_model


# ---------------------------------------------------------------------------
# GBIF fetching
# ---------------------------------------------------------------------------

def fetch_gbif_species(
    country_code: str,
    limit: int = 200,
) -> list[dict]:
    """
    Fetch vascular plant species for *country_code* from GBIF.

    Two-phase strategy
    ------------------
    **Phase 1 — occurrence facets** (``/occurrence/search``):
        ``kingdomKey=6`` *works* on the occurrence endpoint and reliably filters
        to Plantae.  We use ``facet=speciesKey`` to get up to ``limit × 3``
        species keys that have confirmed occurrences in the country.

    **Phase 2 — species detail** (``/species/{key}``):
        Fetch the taxonomic record for each key concurrently (10 workers).
        The detail endpoint returns ``scientificName``, ``family``,
        ``vernacularName`` (comma-separated list), ``kingdom``, and ``phylum``.
        Records that fail the Python-side plant guard are dropped later in
        ``map_gbif_to_species_fields``.

    Note: ``kingdomKey`` is silently ignored by ``/species/search`` (it returns
    animals despite the filter), so that endpoint is no longer used here.

    Parameters
    ----------
    country_code:
        ISO 3166-1 alpha-2 code, e.g. ``"FR"``.
    limit:
        Target number of species records to return.  We fetch up to
        ``limit × 3`` candidate keys to allow for mapper-side filtering.

    Returns
    -------
    list[dict]
        GBIF ``/species/{key}`` records, up to *limit* items.
    """
    over_fetch = min(limit * 3, 1200)   # headroom for Python-side filter losses

    # ── Phase 1: occurrence facets → species keys ────────────────────────────
    logger.info(
        "GBIF Phase 1: occurrence facets for %s (requesting %d candidate keys).",
        country_code,
        over_fetch,
    )
    try:
        with httpx.Client(timeout=_GBIF_TIMEOUT) as client:
            resp = client.get(
                f"{GBIF_API_BASE}/occurrence/search",
                params={
                    "country":          country_code.upper(),
                    "kingdomKey":       6,        # Plantae — works on occurrence endpoint
                    "occurrenceStatus": "PRESENT",
                    "hasCoordinate":    "true",
                    "facet":            "speciesKey",
                    "facetMincount":    3,         # at least 3 observations = established
                    "facetLimit":       over_fetch,
                    "limit":            0,         # we only want the facet, not occurrences
                },
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "GBIF occurrence search HTTP %s: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        raise
    except httpx.RequestError as exc:
        logger.error("GBIF network error (occurrence search): %s", exc)
        raise

    facets = resp.json().get("facets", [])
    if not facets or not facets[0].get("counts"):
        logger.warning("GBIF occurrence facet returned no results for %s.", country_code)
        return []

    species_keys: list[str] = [c["name"] for c in facets[0]["counts"]]
    logger.info(
        "GBIF Phase 1 complete: %d candidate species keys for %s.",
        len(species_keys),
        country_code,
    )

    # ── Phase 2: species detail lookups (10 concurrent workers) ─────────────
    logger.info("GBIF Phase 2: fetching species details (%d keys).", len(species_keys))

    def _fetch_detail(key: str) -> dict | None:
        """Return the /species/{key} JSON or None on any error."""
        try:
            with httpx.Client(timeout=_GBIF_TIMEOUT) as c:
                r = c.get(f"{GBIF_API_BASE}/species/{key}")
                return r.json() if r.status_code == 200 else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Species detail fetch failed for key=%s: %s", key, exc)
            return None

    records: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(_fetch_detail, species_keys):
            if result is not None:
                records.append(result)

    logger.info(
        "GBIF Phase 2 complete: %d species details fetched for %s.",
        len(records),
        country_code,
    )
    return records[:limit]


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------

def _extract_common_names(gbif_record: dict) -> list[str] | None:
    """
    Pull up to 10 distinct vernacular names from a GBIF record.

    Handles two source formats:
    - ``vernacularNames`` array of ``{vernacularName: str}`` dicts
      (from ``/species/search`` results)
    - ``vernacularName`` single comma-separated string
      (from ``/species/{key}`` detail endpoint)
    """
    names: list[str] = []
    seen: set[str] = set()

    # Format A: embedded array from /species/search
    for entry in gbif_record.get("vernacularNames", []):
        name = (entry.get("vernacularName") or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
        if len(names) >= 10:
            break

    # Format B: comma-separated string from /species/{key}
    if not names:
        raw = (gbif_record.get("vernacularName") or "").strip()
        for part in raw.split(","):
            name = part.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
            if len(names) >= 10:
                break

    return names if names else None


def _first_description(gbif_record: dict) -> str | None:
    """Return the first available description text, if any."""
    for desc in gbif_record.get("descriptions", []):
        text = (desc.get("description") or "").strip()
        if text:
            return text[:2000]
    return None


def map_gbif_to_species_fields(gbif_record: dict) -> dict | None:
    """
    Map a single GBIF species record to the fields accepted by :class:`Species`.

    Returns ``None`` if the record has no usable scientific name or if the
    record is not a plant (kingdom != Plantae).
    """
    # ── Kingdom guard (blacklist) ────────────────────────────────────────────
    # We fetched via the occurrence endpoint with kingdomKey=6 (Plantae), so
    # most records will have kingdom="Plantae" or kingdom="" (not populated).
    # Reject only records that are *explicitly* labelled as a non-plant kingdom.
    # A whitelist ("must be Plantae") would incorrectly drop valid plant records
    # whose kingdom field is absent.
    _NON_PLANT_KINGDOMS = {
        "animalia", "fungi", "bacteria", "chromista",
        "protozoa", "archaea", "viruses",
    }
    kingdom = (gbif_record.get("kingdom") or "").strip().lower()
    if kingdom in _NON_PLANT_KINGDOMS:
        logger.debug(
            "Skipping non-plant record '%s' (kingdom=%s)",
            gbif_record.get("scientificName") or gbif_record.get("canonicalName"),
            kingdom,
        )
        return None

    # ── Phylum guard — belt-and-suspenders for clearly non-plant phyla ───────
    _NON_PLANT_PHYLA = {
        "arthropoda", "chordata", "mollusca", "annelida", "nematoda",
        "platyhelminthes", "echinodermata", "porifera", "cnidaria",
        "bacteriota", "proteobacteria", "firmicutes", "actinobacteria",
        "ascomycota", "basidiomycota", "mucoromycota",
    }
    phylum = (gbif_record.get("phylum") or "").strip().lower()
    if phylum in _NON_PLANT_PHYLA:
        logger.debug(
            "Skipping non-plant record '%s' (phylum=%s)",
            gbif_record.get("scientificName") or gbif_record.get("canonicalName"),
            phylum,
        )
        return None

    scientific_name = (
        gbif_record.get("scientificName")
        or gbif_record.get("canonicalName")
        or ""
    ).strip()
    if not scientific_name:
        return None

    return {
        "scientific_name": scientific_name,
        "common_names": _extract_common_names(gbif_record),
        "family": gbif_record.get("family"),
        # Geographic / growth fields — not available from the search endpoint
        "native_regions": None,
        "bloom_months": None,
        "height_cm_min": None,
        "height_cm_max": None,
        "soil_preferences": None,
        # Placeholder ecological scores — refined later by species_scorer.py
        "pollinator_score": 0.5,
        "insect_host_score": 0.5,
        "soil_benefit_score": 0.5,
        "environmental_score": 0.5,
        "food_utility_score": 0.5,
        "food_utility_notes": None,
        "description": _first_description(gbif_record),
    }


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

def build_embedding_text(fields: dict) -> str:
    """
    Construct the text to embed from species fields.
    Combines scientific name, common names, family, and description.
    """
    parts: list[str] = []

    if fields.get("scientific_name"):
        parts.append(f"Plant species: {fields['scientific_name']}")

    if fields.get("family"):
        parts.append(f"Family: {fields['family']}")

    if fields.get("common_names"):
        parts.append(f"Also known as: {', '.join(fields['common_names'])}")

    if fields.get("description"):
        # Truncate long descriptions for the embedding context
        parts.append(f"Description: {fields['description'][:500]}")

    return ". ".join(parts) if parts else fields.get("scientific_name", "unknown plant species")


def _placeholder_embedding(text: str) -> list[float]:
    """
    Generate a deterministic, unit-normalised 768-dim vector from text via
    SHA-256 seeding.  NOT semantically meaningful — used only when the
    sentence-transformers model fails to load (e.g. in CI environments without
    the model cache).
    """
    import numpy as np  # numpy is already in requirements.txt

    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    vec /= float(np.linalg.norm(vec))  # unit norm
    return vec.tolist()


def generate_embedding(text: str) -> list[float] | None:
    """
    Return a 768-dim embedding for *text* using ``BAAI/bge-base-en-v1.5``.

    The model runs locally via ``sentence-transformers``; no API key is needed.
    Falls back to a deterministic numpy placeholder if the model is unavailable.

    BGE models are trained with a query-prefix convention for retrieval tasks.
    For document-side encoding (species descriptions stored in the catalogue),
    no prefix is needed — the raw text is passed directly.
    """
    model = _get_st_model()
    if model is not None:
        try:
            vector = model.encode(text, normalize_embeddings=True)
            return vector.tolist()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "sentence-transformers encode failed for '%s…': %s — using placeholder.",
                text[:60],
                exc,
            )

    logger.warning(
        "Falling back to placeholder embedding for '%s…'. "
        "Ensure sentence-transformers is installed and the model cache is populated.",
        text[:60],
    )
    return _placeholder_embedding(text)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _species_exists(db: Session, scientific_name: str) -> bool:
    return (
        db.query(Species.id)
        .filter(Species.scientific_name == scientific_name)
        .first()
        is not None
    )


def _create_species(db: Session, fields: dict, embedding: list[float] | None) -> Species:
    species = Species(**fields, embedding=embedding)
    db.add(species)
    return species


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def load_species_for_country(
    country_code: str,
    limit: int = 100,
    db: Session | None = None,
) -> dict:
    """
    Fetch, embed, and persist native plant species for *country_code*.

    Parameters
    ----------
    country_code:
        ISO 3166-1 alpha-2 code, e.g. ``"FR"``.
    limit:
        Maximum number of species to load.
    db:
        Optional SQLAlchemy session; a new one is created (and closed) if not
        provided.

    Returns
    -------
    dict
        ``{"fetched": int, "created": int, "skipped": int, "errors": int}``
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    summary = {"fetched": 0, "created": 0, "skipped": 0, "errors": 0}

    try:
        gbif_records = fetch_gbif_species(country_code, limit)
        summary["fetched"] = len(gbif_records)
        logger.info("Fetched %d GBIF records for %s", len(gbif_records), country_code)

        for idx, record in enumerate(gbif_records):
            sci_name = (
                record.get("scientificName")
                or record.get("canonicalName")
                or ""
            ).strip()

            try:
                fields = map_gbif_to_species_fields(record)
                if fields is None:
                    logger.debug(
                        "Skipping GBIF key=%s — no scientific name.", record.get("key")
                    )
                    summary["skipped"] += 1
                    continue

                # Duplicate check before paying for an embedding
                if _species_exists(db, fields["scientific_name"]):
                    logger.debug("Duplicate skipped: %s", fields["scientific_name"])
                    summary["skipped"] += 1
                    continue

                embed_text = build_embedding_text(fields)
                embedding = generate_embedding(embed_text)

                species = _create_species(db, fields, embedding)
                summary["created"] += 1
                logger.info(
                    "[%d/%d] Created: %s",
                    idx + 1,
                    len(gbif_records),
                    species.scientific_name,
                )

                # Commit every 20 records to keep transactions small
                if summary["created"] % 20 == 0:
                    db.commit()
                    logger.debug("Committed batch at creation #%d", summary["created"])

            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Error processing '%s' (GBIF key=%s): %s",
                    sci_name,
                    record.get("key"),
                    exc,
                    exc_info=True,
                )
                summary["errors"] += 1
                try:
                    db.rollback()
                except Exception:
                    pass

        db.commit()
        logger.info("Load complete for %s: %s", country_code, summary)
        return summary

    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
