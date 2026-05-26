"""
Unit tests for agents/graph.py

Tests cover:
- _safe_score: clamping, type coercion, fallback on invalid input
- _parse_json_response: various Claude response formats
- merge_and_rank: weight normalisation, neutral fallbacks, ranking, top-20 cap
"""

from __future__ import annotations

import pytest

from agents.graph import _parse_json_response, _safe_score, merge_and_rank


# ---------------------------------------------------------------------------
# _safe_score
# ---------------------------------------------------------------------------

class TestSafeScore:
    def test_valid_float_in_range(self):
        assert _safe_score(0.7) == pytest.approx(0.7)

    def test_clamps_above_one(self):
        assert _safe_score(1.5) == 1.0

    def test_clamps_below_zero(self):
        assert _safe_score(-0.3) == 0.0

    def test_coerces_string_float(self):
        assert _safe_score("0.8") == pytest.approx(0.8)

    def test_fallback_on_none(self):
        assert _safe_score(None) == 0.5

    def test_fallback_on_non_numeric_string(self):
        assert _safe_score("not-a-number") == 0.5

    def test_custom_fallback(self):
        assert _safe_score(None, fallback=0.3) == pytest.approx(0.3)

    def test_integer_input(self):
        assert _safe_score(1) == 1.0
        assert _safe_score(0) == 0.0


# ---------------------------------------------------------------------------
# _parse_json_response
# ---------------------------------------------------------------------------

class TestParseJsonResponse:
    def test_bare_json_array(self):
        text = '[{"scientific_name": "Rosa canina", "score": 0.8, "reasoning": "good"}]'
        result = _parse_json_response(text)
        assert len(result) == 1
        assert result[0]["scientific_name"] == "Rosa canina"

    def test_json_in_markdown_fence(self):
        text = '```json\n[{"scientific_name": "Rosa canina", "score": 0.7, "reasoning": "ok"}]\n```'
        result = _parse_json_response(text)
        assert result[0]["score"] == pytest.approx(0.7)

    def test_json_in_unlabelled_fence(self):
        text = '```\n[{"scientific_name": "Rosa canina", "score": 0.6, "reasoning": "ok"}]\n```'
        result = _parse_json_response(text)
        assert len(result) == 1

    def test_array_wrapped_in_dict(self):
        text = '{"results": [{"scientific_name": "Rosa canina", "score": 0.5, "reasoning": "mid"}]}'
        result = _parse_json_response(text)
        assert result[0]["scientific_name"] == "Rosa canina"

    def test_array_embedded_in_prose(self):
        text = 'Here are my scores:\n[{"scientific_name": "Rosa canina", "score": 0.9, "reasoning": "great"}]\nThat is all.'
        result = _parse_json_response(text)
        assert result[0]["score"] == pytest.approx(0.9)

    def test_raises_on_unparseable_input(self):
        with pytest.raises(ValueError, match="Could not parse"):
            _parse_json_response("This is just plain text with no JSON.")

    def test_multiple_species(self):
        text = '[{"scientific_name": "A", "score": 0.8, "reasoning": "r"}, {"scientific_name": "B", "score": 0.6, "reasoning": "r"}]'
        result = _parse_json_response(text)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# merge_and_rank  (tested as a coroutine via pytest-asyncio)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMergeAndRank:
    _SENTINEL = object()  # distinguishes "not provided" from "explicitly empty list"

    def _state(
        self,
        pollinator_results=_SENTINEL,
        insect_results=_SENTINEL,
        soil_results=_SENTINEL,
        environment_results=_SENTINEL,
        food_utility_results=_SENTINEL,
        size_results=_SENTINEL,
        req_overrides=None,
    ) -> dict:
        garden_request = {
            "priority_pollinators":  0.5,
            "priority_insects":      0.5,
            "priority_soil":         0.5,
            "priority_environment":  0.5,
            "priority_food_utility": 0.5,
            "priority_size":         0.5,
        }
        if req_overrides:
            garden_request.update(req_overrides)

        def _make_results(names, score=0.7):
            return [{"scientific_name": n, "score": score, "reasoning": "test"} for n in names]

        names = ["Rosa canina", "Centaurea cyanus", "Plantago lanceolata"]
        _s = self._SENTINEL
        return {
            "garden_request":        garden_request,
            "candidate_species":     [],
            "pollinator_results":    _make_results(names) if pollinator_results is _s else pollinator_results,
            "insect_results":        _make_results(names) if insect_results is _s else insect_results,
            "soil_results":          _make_results(names) if soil_results is _s else soil_results,
            "environment_results":   _make_results(names) if environment_results is _s else environment_results,
            "food_utility_results":  _make_results(names) if food_utility_results is _s else food_utility_results,
            "size_results":          _make_results(names) if size_results is _s else size_results,
            "final_recommendations": [],
            "errors":                [],
        }

    async def test_returns_ranked_recommendations(self):
        state = self._state()
        result = await merge_and_rank(state)
        recs = result["final_recommendations"]
        assert len(recs) == 3
        assert all("rank" in r for r in recs)
        assert recs[0]["rank"] == 1

    async def test_rank_is_1_indexed(self):
        state = self._state()
        result = await merge_and_rank(state)
        ranks = [r["rank"] for r in result["final_recommendations"]]
        assert ranks == sorted(ranks)
        assert ranks[0] == 1

    async def test_score_breakdown_keys(self):
        state = self._state()
        result = await merge_and_rank(state)
        breakdown = result["final_recommendations"][0]["score_breakdown"]
        expected_keys = {"pollinators", "insects", "soil", "environment", "food_utility", "size"}
        assert expected_keys == set(breakdown.keys())

    async def test_agent_reasoning_present(self):
        state = self._state()
        result = await merge_and_rank(state)
        reasoning = result["final_recommendations"][0]["agent_reasoning"]
        assert len(reasoning) > 0

    async def test_neutral_fallback_for_missing_dimension(self):
        """A species missing from one dimension's results gets score 0.5."""
        names = ["Rosa canina", "Centaurea cyanus"]
        pollinators = [{"scientific_name": "Rosa canina", "score": 0.9, "reasoning": "r"}]
        # Centaurea cyanus is only in insects, not pollinators
        insects = [{"scientific_name": "Centaurea cyanus", "score": 0.8, "reasoning": "r"}]
        state = self._state(
            pollinator_results=pollinators,
            insect_results=insects,
            soil_results=[],
            environment_results=[],
            food_utility_results=[],
            size_results=[],
        )
        result = await merge_and_rank(state)
        # Both species should appear in the output
        names_out = {r["scientific_name"] for r in result["final_recommendations"]}
        assert "Rosa canina" in names_out
        assert "Centaurea cyanus" in names_out

    async def test_weight_normalisation(self):
        """Equal weights should give equal contribution per dimension."""
        state = self._state(
            req_overrides={
                "priority_pollinators":  1.0,
                "priority_insects":      1.0,
                "priority_soil":         1.0,
                "priority_environment":  1.0,
                "priority_food_utility": 1.0,
                "priority_size":         1.0,
            }
        )
        result = await merge_and_rank(state)
        # With uniform scores and equal weights, final_score ≈ 0.7
        for rec in result["final_recommendations"]:
            assert rec["final_score"] == pytest.approx(0.7, abs=0.01)

    async def test_caps_at_top_20(self):
        """merge_and_rank should return at most 20 recommendations."""
        names = [f"Species{i}" for i in range(30)]
        make = lambda names, s=0.5: [{"scientific_name": n, "score": s, "reasoning": "r"} for n in names]  # noqa: E731
        state = self._state(
            pollinator_results=make(names),
            insect_results=make(names),
            soil_results=make(names),
            environment_results=make(names),
            food_utility_results=make(names),
            size_results=make(names),
        )
        result = await merge_and_rank(state)
        assert len(result["final_recommendations"]) <= 20

    async def test_empty_results_returns_empty(self):
        state = self._state(
            pollinator_results=[],
            insect_results=[],
            soil_results=[],
            environment_results=[],
            food_utility_results=[],
            size_results=[],
        )
        result = await merge_and_rank(state)
        assert result["final_recommendations"] == []

    async def test_priority_weighting_affects_ranking(self):
        """Higher weight on a dimension should favour species that score well there."""
        high_pollinator = {"scientific_name": "BeeFlower", "score": 0.95, "reasoning": "r"}
        low_pollinator  = {"scientific_name": "NoNectar",  "score": 0.1,  "reasoning": "r"}

        state = self._state(
            pollinator_results=[high_pollinator, low_pollinator],
            insect_results=[
                {"scientific_name": "BeeFlower", "score": 0.5, "reasoning": "r"},
                {"scientific_name": "NoNectar",  "score": 0.5, "reasoning": "r"},
            ],
            soil_results=[],
            environment_results=[],
            food_utility_results=[],
            size_results=[],
            req_overrides={
                "priority_pollinators":  1.0,
                "priority_insects":      0.1,
                "priority_soil":         0.0,
                "priority_environment":  0.0,
                "priority_food_utility": 0.0,
                "priority_size":         0.0,
            },
        )
        result = await merge_and_rank(state)
        recs = result["final_recommendations"]
        top_name = recs[0]["scientific_name"]
        assert top_name == "BeeFlower"
