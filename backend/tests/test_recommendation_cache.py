"""
Unit tests for app/services/recommendation_cache.py

Tests cover:
- compute_content_hash: determinism, sensitivity, normalisation
"""

from __future__ import annotations

import pytest

from app.schemas.recommendations import RecommendationRequest
from app.services.recommendation_cache import compute_content_hash


def _make_request(**overrides) -> RecommendationRequest:
    defaults = {
        "latitude":              51.5,
        "longitude":             -0.1,
        "country_code":          "GB",
        "priority_pollinators":  0.6,
        "priority_insects":      0.5,
        "priority_soil":         0.5,
        "priority_environment":  0.5,
        "priority_food_utility": 0.4,
        "priority_size":         0.5,
        "description":           None,
    }
    defaults.update(overrides)
    return RecommendationRequest(**defaults)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestComputeContentHash:
    def test_is_deterministic(self):
        r = _make_request()
        assert compute_content_hash(r) == compute_content_hash(r)

    def test_same_params_same_hash(self):
        r1 = _make_request()
        r2 = _make_request()
        assert compute_content_hash(r1) == compute_content_hash(r2)

    def test_16_chars_long(self):
        r = _make_request()
        assert len(compute_content_hash(r)) == 16

    def test_lowercase_hex(self):
        r = _make_request()
        h = compute_content_hash(r)
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Sensitivity — different inputs → different hashes
# ---------------------------------------------------------------------------

class TestHashSensitivity:
    def test_different_location_different_hash(self):
        h1 = compute_content_hash(_make_request(latitude=51.5, longitude=-0.1))
        h2 = compute_content_hash(_make_request(latitude=48.8, longitude=2.35))
        assert h1 != h2

    def test_different_priority_different_hash(self):
        h1 = compute_content_hash(_make_request(priority_pollinators=0.9))
        h2 = compute_content_hash(_make_request(priority_pollinators=0.1))
        assert h1 != h2

    def test_different_country_different_hash(self):
        h1 = compute_content_hash(_make_request(country_code="GB"))
        h2 = compute_content_hash(_make_request(country_code="FR"))
        assert h1 != h2

    def test_different_description_different_hash(self):
        h1 = compute_content_hash(_make_request(description="sunny border"))
        h2 = compute_content_hash(_make_request(description="shady corner"))
        assert h1 != h2

    def test_different_size_priority_different_hash(self):
        h1 = compute_content_hash(_make_request(priority_size=0.2))
        h2 = compute_content_hash(_make_request(priority_size=0.8))
        assert h1 != h2


# ---------------------------------------------------------------------------
# Normalisation — small variations that should NOT change the hash
# ---------------------------------------------------------------------------

class TestHashNormalisation:
    def test_country_code_case_insensitive(self):
        h1 = compute_content_hash(_make_request(country_code="gb"))
        h2 = compute_content_hash(_make_request(country_code="GB"))
        assert h1 == h2

    def test_description_whitespace_normalised(self):
        h1 = compute_content_hash(_make_request(description="  sunny border  "))
        h2 = compute_content_hash(_make_request(description="sunny border"))
        assert h1 == h2

    def test_description_case_normalised(self):
        h1 = compute_content_hash(_make_request(description="Sunny Border"))
        h2 = compute_content_hash(_make_request(description="sunny border"))
        assert h1 == h2

    def test_none_description_equals_empty_string(self):
        h1 = compute_content_hash(_make_request(description=None))
        h2 = compute_content_hash(_make_request(description=""))
        assert h1 == h2

    def test_lat_lon_precision_100m(self):
        """Locations within ~100 m should produce the same hash."""
        # 0.001° ≈ 111 m; we round to 3 d.p. so these are equal
        h1 = compute_content_hash(_make_request(latitude=51.5001, longitude=-0.1001))
        h2 = compute_content_hash(_make_request(latitude=51.5002, longitude=-0.1002))
        # Both round to 51.500 / -0.100
        assert h1 == h2

    def test_lat_lon_different_beyond_100m(self):
        """Locations ~1 km apart should produce different hashes."""
        h1 = compute_content_hash(_make_request(latitude=51.500, longitude=-0.100))
        h2 = compute_content_hash(_make_request(latitude=51.510, longitude=-0.110))
        assert h1 != h2
