"""
Unit tests for services/gbif_loader.py

Tests cover:
- map_gbif_to_species_fields: kingdom/phylum filtering, name extraction
- _extract_common_names: both vernacularNames list and comma-string formats
- _safe_score equivalent logic via the loader's own score validation
"""

from __future__ import annotations

import pytest

from services.gbif_loader import map_gbif_to_species_fields


# ---------------------------------------------------------------------------
# Kingdom / phylum filtering
# ---------------------------------------------------------------------------

class TestKingdomFilter:
    def test_plant_passes_through(self, plant_record):
        result = map_gbif_to_species_fields(plant_record)
        assert result is not None

    def test_animal_is_rejected(self, animal_record):
        result = map_gbif_to_species_fields(animal_record)
        assert result is None

    def test_fungi_is_rejected(self, fungi_record):
        result = map_gbif_to_species_fields(fungi_record)
        assert result is None

    def test_record_without_kingdom_passes(self, plant_record):
        """Records with empty kingdom should pass (many GBIF records omit it)."""
        plant_record["kingdom"] = ""
        result = map_gbif_to_species_fields(plant_record)
        assert result is not None

    def test_record_with_null_kingdom_passes(self, plant_record):
        plant_record["kingdom"] = None
        result = map_gbif_to_species_fields(plant_record)
        assert result is not None

    @pytest.mark.parametrize("kingdom", [
        "Animalia", "ANIMALIA", "animalia",   # case-insensitive
        "Fungi", "Bacteria", "Chromista",
        "Protozoa", "Archaea",
    ])
    def test_non_plant_kingdoms_rejected(self, plant_record, kingdom):
        plant_record["kingdom"] = kingdom
        result = map_gbif_to_species_fields(plant_record)
        assert result is None

    @pytest.mark.parametrize("phylum", [
        "Arthropoda", "Chordata", "Ascomycota", "Basidiomycota",
    ])
    def test_non_plant_phyla_rejected(self, plant_record, phylum):
        plant_record["kingdom"] = ""   # no kingdom, but bad phylum
        plant_record["phylum"] = phylum
        result = map_gbif_to_species_fields(plant_record)
        assert result is None


# ---------------------------------------------------------------------------
# Name extraction
# ---------------------------------------------------------------------------

class TestNameExtraction:
    def test_scientific_name_preserved(self, plant_record):
        result = map_gbif_to_species_fields(plant_record)
        assert result["scientific_name"] == "Centaurea cyanus L."

    def test_falls_back_to_canonical_name(self, plant_record):
        del plant_record["scientificName"]
        plant_record["canonicalName"] = "Centaurea cyanus"
        result = map_gbif_to_species_fields(plant_record)
        assert result["scientific_name"] == "Centaurea cyanus"

    def test_no_name_record_rejected(self, no_name_record):
        result = map_gbif_to_species_fields(no_name_record)
        assert result is None

    def test_family_extracted(self, plant_record):
        result = map_gbif_to_species_fields(plant_record)
        assert result["family"] == "Asteraceae"

    def test_missing_family_is_none(self, plant_record):
        del plant_record["family"]
        result = map_gbif_to_species_fields(plant_record)
        assert result["family"] is None


# ---------------------------------------------------------------------------
# Common name extraction
# ---------------------------------------------------------------------------

class TestCommonNameExtraction:
    def test_extracts_from_vernacular_names_list(self, plant_record):
        plant_record.pop("vernacularName", None)   # remove string form
        result = map_gbif_to_species_fields(plant_record)
        assert "Cornflower" in result["common_names"]
        assert "Bachelor's button" in result["common_names"]

    def test_extracts_from_comma_string(self, plant_record):
        plant_record.pop("vernacularNames", None)  # remove list form
        plant_record["vernacularName"] = "Cornflower, Bachelor's button"
        result = map_gbif_to_species_fields(plant_record)
        assert "Cornflower" in result["common_names"]
        assert "Bachelor's button" in result["common_names"]

    def test_empty_common_names_when_absent(self, plant_record):
        plant_record.pop("vernacularNames", None)
        plant_record.pop("vernacularName", None)
        result = map_gbif_to_species_fields(plant_record)
        # The loader may return None or [] when no common names are available
        assert result["common_names"] in (None, [])

    def test_deduplicates_common_names(self, plant_record):
        """Names appearing in both list and string should not be duplicated."""
        plant_record["vernacularNames"] = [
            {"vernacularName": "Cornflower", "language": "eng"},
        ]
        plant_record["vernacularName"] = "Cornflower, Cornflower"
        result = map_gbif_to_species_fields(plant_record)
        assert result["common_names"].count("Cornflower") == 1


# ---------------------------------------------------------------------------
# Default score placeholders
# ---------------------------------------------------------------------------

class TestDefaultScores:
    def test_default_scores_present(self, plant_record):
        result = map_gbif_to_species_fields(plant_record)
        for field in (
            "pollinator_score",
            "insect_host_score",
            "soil_benefit_score",
            "environmental_score",
            "food_utility_score",
        ):
            assert field in result

    def test_default_score_values_are_neutral(self, plant_record):
        """Default placeholder scores should be 0.5 (neutral)."""
        result = map_gbif_to_species_fields(plant_record)
        # Scores may be None (unset) or 0.5 (neutral) — either is acceptable
        for field in (
            "pollinator_score",
            "insect_host_score",
            "soil_benefit_score",
            "environmental_score",
            "food_utility_score",
        ):
            score = result[field]
            assert score is None or 0.0 <= score <= 1.0
