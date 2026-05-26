"""
Pytest configuration and shared fixtures.
"""

from __future__ import annotations

import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — make sure `backend/` is on sys.path so imports work
# without installing the package.
# ---------------------------------------------------------------------------
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


# ---------------------------------------------------------------------------
# Minimal environment variables needed by import-time code
# (avoids crashing on DB / Anthropic checks before tests even run)
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/jardin_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-000000000000000000000000000000000000000000000000")
os.environ.setdefault("GBIF_API_BASE", "https://api.gbif.org/v1")


# ---------------------------------------------------------------------------
# Sample GBIF records for use across tests
# ---------------------------------------------------------------------------

@pytest.fixture
def plant_record() -> dict:
    """A well-formed GBIF record for a plant species."""
    return {
        "scientificName": "Centaurea cyanus L.",
        "canonicalName": "Centaurea cyanus",
        "kingdom": "Plantae",
        "phylum": "Tracheophyta",
        "class": "Magnoliopsida",
        "family": "Asteraceae",
        "genus": "Centaurea",
        "species": "Centaurea cyanus",
        "vernacularName": "Cornflower, Bachelor's button, Bluebottle",
        "vernacularNames": [
            {"vernacularName": "Cornflower", "language": "eng"},
            {"vernacularName": "Bachelor's button", "language": "eng"},
        ],
    }


@pytest.fixture
def animal_record() -> dict:
    """A GBIF record for an animal — should be filtered out."""
    return {
        "scientificName": "Apis mellifera L.",
        "canonicalName": "Apis mellifera",
        "kingdom": "Animalia",
        "phylum": "Arthropoda",
        "family": "Apidae",
    }


@pytest.fixture
def fungi_record() -> dict:
    """A GBIF record for a fungus — should be filtered out."""
    return {
        "scientificName": "Amanita muscaria (L.) Lam.",
        "canonicalName": "Amanita muscaria",
        "kingdom": "Fungi",
        "phylum": "Basidiomycota",
    }


@pytest.fixture
def no_name_record() -> dict:
    """A GBIF record with no usable scientific name — should be filtered out."""
    return {
        "scientificName": "",
        "canonicalName": None,
        "kingdom": "Plantae",
    }
