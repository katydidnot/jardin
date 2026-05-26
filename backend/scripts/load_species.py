"""
load_species — CLI tool for seeding the species catalogue
==========================================================
Fetches native terrestrial plant species from GBIF for one or more country
codes, generates semantic embeddings, stores them in the database, and
optionally runs AI scoring on the newly loaded rows.

Usage examples
--------------
Load 50 French species (embeddings only, placeholder scores)::

    python -m backend.scripts.load_species --country FR --limit 50

Load and immediately AI-score new species::

    python -m backend.scripts.load_species --country GB --limit 100 --score

Multiple countries in one run::

    python -m backend.scripts.load_species --country FR --country DE --limit 200 --score

Prerequisites
-------------
* DATABASE_URL must point to a running Postgres instance with the schema applied
  (``alembic upgrade head`` from ``backend/``).
* ANTHROPIC_API_KEY is required when ``--score`` is used.
* Semantic embeddings are generated locally via ``BAAI/bge-base-en-v1.5``
  (sentence-transformers). No external embedding API key is required.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Path bootstrap — allow running as `python -m backend.scripts.load_species`
# from the repo root, or as `python -m scripts.load_species` from backend/.
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_here)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# Late imports so path is set up first
from app.db import SessionLocal  # noqa: E402
from app.models.species import Species  # noqa: E402
from services.gbif_loader import load_species_for_country  # noqa: E402


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="load_species",
        description=(
            "Load native plant species from GBIF into the Jardin database, "
            "generate semantic embeddings, and optionally AI-score them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--country",
        metavar="CODE",
        type=str.upper,
        required=True,
        action="append",
        dest="countries",
        help=(
            "ISO 3166-1 alpha-2 country code (e.g. FR, GB, DE). "
            "Repeat to load multiple countries."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        metavar="N",
        help="Maximum species to load per country (default: 100).",
    )
    parser.add_argument(
        "--score",
        action="store_true",
        default=False,
        help=(
            "After loading, run AI scoring (via Claude) on all unscored "
            "species in the database. Requires ANTHROPIC_API_KEY."
        ),
    )
    parser.add_argument(
        "--score-limit",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Cap the number of species to AI-score in this run (0 = no cap). "
            "Useful for incremental scoring on large datasets."
        ),
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Seconds between Claude API calls when scoring (default: 1.0).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------

def _run_scoring(
    db,
    score_limit: int,
    rate_limit_delay: float,
) -> int:
    """
    Score unscored species (those whose scores are still at 0.5) using Claude.

    Returns the number of successfully scored species.
    """
    from services.species_scorer import apply_scores, batch_score_species

    # Identify species that still carry the placeholder score on all dimensions
    query = db.query(Species).filter(
        Species.pollinator_score == 0.5,
        Species.insect_host_score == 0.5,
        Species.soil_benefit_score == 0.5,
        Species.environmental_score == 0.5,
        Species.food_utility_score == 0.5,
    )
    if score_limit > 0:
        query = query.limit(score_limit)

    to_score = query.all()

    if not to_score:
        logger.info("No unscored species found — nothing to do.")
        return 0

    logger.info("AI-scoring %d species (delay=%.1fs)…", len(to_score), rate_limit_delay)
    results = batch_score_species(to_score, rate_limit_delay=rate_limit_delay)

    scored = 0
    for result in results:
        if result["scores"] is None:
            logger.warning(
                "Scoring failed for %s: %s",
                result["scientific_name"],
                result["error"],
            )
            continue

        # Find the ORM object and apply scores
        species = db.get(Species, result["species_id"])
        if species is None:
            logger.warning("Species %s not found in DB — skipping.", result["species_id"])
            continue

        apply_scores(species, result["scores"])
        scored += 1

    db.commit()
    logger.info("Committed scores for %d/%d species.", scored, len(to_score))
    return scored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:  # returns exit code
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)

    # Validate scoring prerequisites early
    if args.score and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error(
            "--score requires ANTHROPIC_API_KEY to be set in the environment."
        )

    overall_totals = {"fetched": 0, "created": 0, "skipped": 0, "errors": 0}
    db = SessionLocal()

    try:
        # ── Loading phase ──────────────────────────────────────────────────
        for country in args.countries:
            logger.info(
                "Loading species: country=%s, limit=%d", country, args.limit
            )
            try:
                summary = load_species_for_country(
                    country_code=country,
                    limit=args.limit,
                    db=db,
                )
                for key in overall_totals:
                    overall_totals[key] += summary.get(key, 0)

                print(
                    f"\n{'─'*50}\n"
                    f" Country    : {country}\n"
                    f" Fetched    : {summary['fetched']}\n"
                    f" Created    : {summary['created']}\n"
                    f" Skipped    : {summary['skipped']}\n"
                    f" Errors     : {summary['errors']}\n"
                )
            except Exception as exc:
                logger.error(
                    "Failed to load country %s: %s", country, exc, exc_info=True
                )
                overall_totals["errors"] += 1

        # ── Scoring phase (optional) ───────────────────────────────────────
        scored = 0
        if args.score:
            scored = _run_scoring(
                db=db,
                score_limit=args.score_limit,
                rate_limit_delay=args.rate_limit_delay,
            )

        # ── Final summary ──────────────────────────────────────────────────
        print(
            f"\n{'═'*50}\n"
            f" TOTALS across {len(args.countries)} country/countries\n"
            f"{'─'*50}\n"
            f" Fetched    : {overall_totals['fetched']}\n"
            f" Created    : {overall_totals['created']}\n"
            f" Skipped    : {overall_totals['skipped']}\n"
            f" Errors     : {overall_totals['errors']}\n"
        )
        if args.score:
            print(f" AI-scored  : {scored}\n{'═'*50}\n")

        return 0 if overall_totals["errors"] == 0 else 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
