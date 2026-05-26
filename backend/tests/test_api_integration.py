"""
Integration tests for the FastAPI application.

These tests use FastAPI's TestClient and mock out:
- Database calls (via monkeypatching Session / SessionLocal)
- LangGraph pipeline (never actually called)
- Anthropic API (never actually called in these flows)

They validate:
- POST /api/recommendations returns 202 with the expected shape
- GET /api/species/{id} returns 404 for unknown IDs
- GET /health returns a valid status payload
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — fake ORM row
# ---------------------------------------------------------------------------

def _mock_species(
    *,
    id_: str | None = None,
    scientific_name: str = "Rosa canina L.",
    family: str = "Rosaceae",
    common_names: list[str] | None = None,
) -> MagicMock:
    s = MagicMock()
    s.id = uuid.UUID(id_) if id_ else uuid.uuid4()
    s.scientific_name = scientific_name
    s.family = family
    s.common_names = common_names or ["Dog rose"]
    s.description = "A thorny shrub."
    s.food_utility_notes = None
    s.pollinator_score = 0.8
    s.insect_host_score = 0.7
    s.soil_benefit_score = 0.5
    s.environmental_score = 0.6
    s.food_utility_score = 0.4
    s.bloom_months = [5, 6, 7]
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """
    Return a TestClient with the DB session dependency overridden to return
    a mock session that never touches Postgres.
    """
    # We need to import app after setting env vars (done in conftest.py)
    from app.main import app
    from app.db import get_db

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/recommendations
# ---------------------------------------------------------------------------

class TestCreateRecommendation:
    _VALID_BODY = {
        "latitude": 51.5,
        "longitude": -0.1,
        "country_code": "GB",
        "priority_pollinators": 0.6,
        "priority_insects": 0.5,
        "priority_soil": 0.5,
        "priority_environment": 0.5,
        "priority_food_utility": 0.4,
        "priority_size": 0.5,
    }

    def test_returns_202(self, client):
        with patch("app.routers.recommendations.load_cached_recommendations", return_value=None), \
             patch("app.routers.recommendations._stream_planner"):
            resp = client.post("/api/recommendations", json=self._VALID_BODY)
        assert resp.status_code == 202

    def test_response_has_request_id(self, client):
        with patch("app.routers.recommendations.load_cached_recommendations", return_value=None), \
             patch("app.routers.recommendations._stream_planner"):
            resp = client.post("/api/recommendations", json=self._VALID_BODY)
        data = resp.json()
        assert "request_id" in data
        assert len(data["request_id"]) > 0

    def test_response_has_stream_url(self, client):
        with patch("app.routers.recommendations.load_cached_recommendations", return_value=None), \
             patch("app.routers.recommendations._stream_planner"):
            resp = client.post("/api/recommendations", json=self._VALID_BODY)
        data = resp.json()
        assert "stream_url" in data
        assert "/stream" in data["stream_url"]

    def test_missing_required_field_returns_422(self, client):
        body = {k: v for k, v in self._VALID_BODY.items() if k != "latitude"}
        resp = client.post("/api/recommendations", json=body)
        assert resp.status_code == 422

    def test_invalid_latitude_returns_422(self, client):
        body = {**self._VALID_BODY, "latitude": 999.0}
        resp = client.post("/api/recommendations", json=body)
        assert resp.status_code == 422

    def test_country_code_uppercased(self, client):
        body = {**self._VALID_BODY, "country_code": "gb"}
        with patch("app.routers.recommendations.load_cached_recommendations", return_value=None), \
             patch("app.routers.recommendations._stream_planner"):
            resp = client.post("/api/recommendations", json=body)
        # Request should succeed (validator uppercases the code)
        assert resp.status_code == 202

    def test_cache_hit_returns_202(self, client):
        fake_cached = [{"rank": 1, "final_score": 0.8, "score_breakdown": {}, "agent_reasoning": {}, "species": {}}]
        with patch("app.routers.recommendations.load_cached_recommendations", return_value=fake_cached), \
             patch("app.routers.recommendations._stream_cached_results"):
            resp = client.post("/api/recommendations", json=self._VALID_BODY)
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# GET /api/species/{species_id}
# ---------------------------------------------------------------------------

class TestGetSpecies:
    def test_unknown_uuid_returns_404(self, client):
        unknown_id = str(uuid.uuid4())
        resp = client.get(f"/api/species/{unknown_id}")
        assert resp.status_code == 404

    def test_invalid_uuid_returns_422(self, client):
        resp = client.get("/api/species/not-a-uuid")
        assert resp.status_code == 422

    def test_known_species_returns_200(self, client):
        from app.db import get_db
        from app.main import app

        species = _mock_species()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = species

        def override():
            yield mock_db

        app.dependency_overrides[get_db] = override
        resp = client.get(f"/api/species/{species.id}")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["scientific_name"] == "Rosa canina L."
        assert data["family"] == "Rosaceae"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        with patch("app.main._check_db", return_value="connected"), \
             patch("app.main._check_anthropic", return_value="reachable"):
            resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_payload_shape(self, client):
        with patch("app.main._check_db", return_value="connected"), \
             patch("app.main._check_anthropic", return_value="reachable"):
            data = client.get("/health").json()
        assert {"status", "db", "anthropic", "version"} <= set(data.keys())

    def test_health_ok_when_all_pass(self, client):
        with patch("app.main._check_db", return_value="connected"), \
             patch("app.main._check_anthropic", return_value="reachable"):
            data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_degraded_when_db_fails(self, client):
        with patch("app.main._check_db", return_value="error: connection refused"), \
             patch("app.main._check_anthropic", return_value="reachable"):
            data = client.get("/health").json()
        assert data["status"] == "degraded"


# ---------------------------------------------------------------------------
# GET /api/recommendations/{id}/stream — basic queue check
# ---------------------------------------------------------------------------

class TestStreamEndpoint:
    def test_unknown_request_id_returns_404(self, client):
        resp = client.get(f"/api/recommendations/{uuid.uuid4()}/stream")
        assert resp.status_code == 404
