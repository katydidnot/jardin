#!/usr/bin/env python3
"""
Jardin garden planner — evaluation harness
==========================================

Runs each test case in evals/test_cases.json through the LangGraph agent graph
directly (no HTTP, no DB writes), then calls Claude as an evaluator to assess
quality. Outputs:

  - evals/reports/eval_{timestamp}.json   (machine-readable full report)
  - Summary table on stdout
  - Exit code 1 if overall pass rate < 70%

Usage
-----
    cd /path/to/jardin
    source backend/.venv/bin/activate
    python evals/run_evals.py [--test-id tc_001] [--verbose]

Environment
-----------
  ANTHROPIC_API_KEY   required
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — make backend/ importable
# ---------------------------------------------------------------------------
_repo_root = Path(__file__).parent.parent.resolve()
_backend_dir = _repo_root / "backend"
for _p in [str(_repo_root), str(_backend_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import anthropic
from pydantic import BaseModel

# Import the compiled graph (no DB writes, pure agent logic)
from agents.graph import GardenPlannerState, get_compiled_graph

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
EVALS_DIR = Path(__file__).parent
TEST_CASES_PATH = EVALS_DIR / "test_cases.json"
REPORTS_DIR = EVALS_DIR / "reports"

# ---------------------------------------------------------------------------
# Anthropic client + evaluator model
# ---------------------------------------------------------------------------
EVAL_MODEL = "claude-opus-4-7"

_anthropic_client: anthropic.Anthropic | None = None


def get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


# ---------------------------------------------------------------------------
# Pydantic models for Claude evaluator
# ---------------------------------------------------------------------------

class CriterionResult(BaseModel):
    criterion: str
    passed: bool
    reasoning: str


class ClaudeEvalResult(BaseModel):
    criteria_results: list[CriterionResult]
    overall_assessment: str
    strengths: list[str]
    weaknesses: list[str]
    pass_count: int
    total_count: int


# ---------------------------------------------------------------------------
# Graph runner — avoids DB writes by calling graph.ainvoke directly
# ---------------------------------------------------------------------------

def _build_initial_state(tc_input: dict) -> dict:
    """
    Construct a GardenPlannerState-compatible dict from a test case input.

    The graph reads flat keys from ``garden_request`` — e.g. ``country_code``,
    ``latitude``, ``priority_pollinators`` — not nested sub-dicts.  We flatten
    the test-case ``location`` and ``priorities`` objects accordingly.
    """
    loc = tc_input["location"]
    prios = tc_input["priorities"]

    return {
        "garden_request": {
            "id":                    "eval-synthetic",
            "latitude":              loc["latitude"],
            "longitude":             loc["longitude"],
            "country_code":          loc["country_code"],
            "region":                loc.get("place_name"),
            "priority_pollinators":  prios.get("pollinators",  0.5),
            "priority_insects":      prios.get("insects",      0.5),
            "priority_soil":         prios.get("soil",         0.5),
            "priority_environment":  prios.get("environment",  0.5),
            "priority_food_utility": prios.get("food_utility", 0.5),
            "priority_size":         prios.get("size",         0.5),
            "free_text_description": tc_input.get("notes"),
        },
        "candidate_species":     [],
        "pollinator_results":    [],
        "insect_results":        [],
        "soil_results":          [],
        "environment_results":   [],
        "food_utility_results":  [],
        "size_results":          [],   # was missing — size agent results were dropped
        "final_recommendations": [],
        "errors":                [],
    }


async def _run_graph_for_test_case(
    tc_input: dict,
    candidate_limit: int | None = None,
    stagger_s: float = 0.0,
) -> dict:
    """
    Invoke the compiled LangGraph agent graph directly and return the final
    state dict.  This bypasses HTTP and database entirely.

    Parameters
    ----------
    tc_input:
        Test-case ``input`` dict.
    candidate_limit:
        Override ``agents.graph.CANDIDATE_LIMIT`` for this run.
    stagger_s:
        If > 0, monkey-patch each scoring node to add a staggered delay
        before it fires, spreading the burst of parallel API calls over time.
        This prevents hitting the output-tokens-per-minute rate limit when
        all 6 agents fire simultaneously.
        Recommended value: 10 seconds per agent stagger → 6 agents spread
        over ~50 s → ~1,200 tokens/10 s = well under 8,000 tokens/min.
    """
    import agents.graph as _graph_module  # noqa: PLC0415

    original_limit: int | None = None
    if candidate_limit is not None:
        original_limit = _graph_module.CANDIDATE_LIMIT
        _graph_module.CANDIDATE_LIMIT = candidate_limit
        logger.debug("CANDIDATE_LIMIT overridden to %d for this eval run.", candidate_limit)

    # ── Stagger patch: wrap each scoring node with a pre-call sleep ──────────
    _originals: dict = {}
    if stagger_s > 0:
        scorer_names = [
            "score_pollinators",
            "score_insects",
            "score_soil",
            "score_environment",
            "score_food_utility",
            "score_size",
        ]
        for i, name in enumerate(scorer_names):
            original_fn = getattr(_graph_module, name)
            _originals[name] = original_fn
            delay = i * stagger_s  # 0, 10, 20, 30, 40, 50 s

            def _make_staggered(fn, d: float):
                async def _staggered(state):
                    if d > 0:
                        logger.debug("Staggering %s by %.0fs…", fn.__name__, d)
                        await asyncio.sleep(d)
                    return await fn(state)
                _staggered.__name__ = fn.__name__
                return _staggered

            setattr(_graph_module, name, _make_staggered(original_fn, delay))

        # The graph must be recompiled so the patched functions are used.
        # Clear the cached compiled graph so get_compiled_graph() rebuilds it.
        _graph_module._compiled_graph = None  # noqa: SLF001
        logger.debug(
            "Stagger patches applied: 6 agents spread %.0f s apart.", stagger_s
        )

    try:
        graph = get_compiled_graph()
        initial_state = _build_initial_state(tc_input)
        final_state: dict = await graph.ainvoke(initial_state)
        return final_state
    finally:
        # Restore originals and invalidate graph cache
        if _originals:
            for name, fn in _originals.items():
                setattr(_graph_module, name, fn)
            _graph_module._compiled_graph = None  # noqa: SLF001
        if original_limit is not None:
            _graph_module.CANDIDATE_LIMIT = original_limit


# ---------------------------------------------------------------------------
# Automated structural checks (no LLM required)
# ---------------------------------------------------------------------------

def _get_top_genera(recommendations: list[dict], n: int = 5) -> set[str]:
    """Extract the top-N species genera from recommendations."""
    genera: set[str] = set()
    for rec in sorted(recommendations, key=lambda r: r.get("rank", 999))[:n]:
        name = (
            rec.get("species", {}).get("scientific_name", "")
            or rec.get("scientific_name", "")
        )
        if name:
            genus = name.split()[0]
            genera.add(genus)
    return genera


def run_automated_checks(tc: dict, final_state: dict) -> dict:
    """
    Run deterministic checks that don't require Claude:
      - expected genera presence (advisory)
      - must_not_include genera (hard failure, checked against top-10 only)

    The forbidden-genera check applies only to the top-10 ranked recommendations.
    Lower-ranked species may appear in the full list as completeness candidates, but
    what matters is whether egregiously wrong species are *actively* recommended.

    Returns a dict with keys: passed (bool), details (list[str]).
    """
    recs = final_state.get("final_recommendations", [])
    errors = final_state.get("errors", [])

    # Top-10 genera (the species being actively recommended)
    top_10_genera: set[str] = set()
    for rec in sorted(recs, key=lambda r: r.get("rank", 999))[:10]:
        name = (
            rec.get("species", {}).get("scientific_name", "")
            or rec.get("scientific_name", "")
        )
        if name:
            top_10_genera.add(name.split()[0])

    details: list[str] = []
    hard_failed = False

    # Hard check: forbidden genera must not appear in the top-10 recommendations
    forbidden = set(tc.get("must_not_include_genera", []))
    violations = forbidden & top_10_genera
    if violations:
        details.append(f"FAIL must_not_include: found {sorted(violations)} in top-10 recommendations")
        hard_failed = True
    else:
        details.append("PASS must_not_include: no forbidden genera in top-10")

    # Advisory check: expected genera
    expected = set(tc.get("expected_top_genera", []))
    top_genera = _get_top_genera(recs, n=5)
    overlap = expected & top_genera
    if len(overlap) >= 2:
        details.append(f"PASS expected genera: {len(overlap)}/{len(expected)} expected genera in top-5 ({sorted(overlap)})")
    else:
        details.append(f"ADVISORY expected genera: only {len(overlap)}/{len(expected)} expected genera in top-5 ({sorted(overlap)})")

    # Check we got any recommendations at all
    if not recs:
        details.append("FAIL no recommendations returned")
        hard_failed = True
    else:
        details.append(f"PASS {len(recs)} recommendations returned")

    # Log graph errors
    if errors:
        details.append(f"ADVISORY graph errors during run: {errors}")

    return {
        "passed": not hard_failed,
        "details": details,
        "recommendation_count": len(recs),
        "graph_errors": errors,
    }


# ---------------------------------------------------------------------------
# Claude evaluator
# ---------------------------------------------------------------------------

_EVALUATOR_SYSTEM = """\
You are an expert evaluator for a native garden planner AI system.

You will be given:
1. A test case description with location, priorities, and quality criteria
2. The top recommendations produced by the AI garden planner
3. Agent reasoning and score breakdowns for each recommendation

Your task is to evaluate whether the recommendations meet the quality criteria.
Be rigorous but fair. Consider the ecological context, the priority weights given,
and the plausibility of the recommendations for the stated location.

The system scores species across SIX ecological dimensions:
  • pollinators    — value to bees, butterflies, moths, hoverflies
  • insects        — host plant for caterpillars, beetles, other invertebrates
  • soil           — nitrogen fixation, mycorrhizal networks, organic matter
  • environment    — carbon sequestration, erosion control, microclimate
  • food_utility   — edible parts, medicinal uses, practical harvests
  • size           — suitability for compact spaces / containers (HIGH = compact,
                     LOW = large tree / vigorous spreader)

These appear in ``score_breakdown`` on each recommendation.
When evaluating a test case with a high ``size`` priority, check that the
``size`` component of ``score_breakdown`` is elevated for top-ranked species.

Respond with a structured JSON evaluation matching the provided schema exactly.
"""


def _format_recommendations_for_eval(recommendations: list[dict], limit: int = 10) -> str:
    """Format the top recommendations as a compact JSON string for the evaluator."""
    top = sorted(recommendations, key=lambda r: r.get("rank", 999))[:limit]
    # Trim verbose fields to keep the prompt manageable
    trimmed = []
    for rec in top:
        sp = rec.get("species", rec)  # support both nested and flat formats
        sb = rec.get("score_breakdown") or {}
        trimmed.append({
            "rank": rec.get("rank"),
            "final_score": round(rec.get("final_score", 0), 3),
            "scientific_name": sp.get("scientific_name", rec.get("scientific_name", "")),
            "common_names": sp.get("common_names", [])[:2],
            # DB ecological scores
            "pollinator_score":    sp.get("pollinator_score"),
            "insect_host_score":   sp.get("insect_host_score"),
            "soil_benefit_score":  sp.get("soil_benefit_score"),
            "environmental_score": sp.get("environmental_score"),
            "food_utility_score":  sp.get("food_utility_score"),
            # Per-recommendation scores from agent (more contextual)
            "score_breakdown": {
                k: round(v, 3) for k, v in sb.items()
            } if sb else None,
            "food_utility_notes": (sp.get("food_utility_notes") or "")[:200] or None,
            "description": (sp.get("description") or "")[:150] or None,
            "agent_reasoning": {
                k: v[:200] for k, v in rec.get("agent_reasoning", {}).items()
            },
        })
    return json.dumps(trimmed, indent=2, default=str)


def evaluate_with_claude(
    tc: dict,
    final_state: dict,
    verbose: bool = False,
) -> ClaudeEvalResult:
    """
    Call Claude to evaluate the recommendation quality against the test case
    quality criteria.
    """
    client = get_anthropic_client()
    recs = final_state.get("final_recommendations", [])
    rec_text = _format_recommendations_for_eval(recs)

    user_content = f"""## Test Case: {tc['id']} — {tc['description']}

### Location
{json.dumps(tc['input']['location'], indent=2)}

### Priority Weights
{json.dumps(tc['input']['priorities'], indent=2)}

### Notes
{tc['input'].get('notes', 'None')}

### Quality Criteria to Evaluate
{json.dumps(tc['quality_criteria'], indent=2)}

### Top Recommendations from the AI
{rec_text}

---

Please evaluate each quality criterion strictly, then provide an overall assessment.
Return your evaluation as a JSON object matching the ClaudeEvalResult schema.
"""

    if verbose:
        logger.debug("Evaluator prompt length: %d chars", len(user_content))

    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=_EVALUATOR_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
        tools=[
            {
                "name": "submit_evaluation",
                "description": "Submit the structured evaluation result",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "criteria_results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "criterion": {"type": "string"},
                                    "passed": {"type": "boolean"},
                                    "reasoning": {"type": "string"},
                                },
                                "required": ["criterion", "passed", "reasoning"],
                            },
                        },
                        "overall_assessment": {"type": "string"},
                        "strengths": {"type": "array", "items": {"type": "string"}},
                        "weaknesses": {"type": "array", "items": {"type": "string"}},
                        "pass_count": {"type": "integer"},
                        "total_count": {"type": "integer"},
                    },
                    "required": [
                        "criteria_results",
                        "overall_assessment",
                        "strengths",
                        "weaknesses",
                        "pass_count",
                        "total_count",
                    ],
                },
            }
        ],
        tool_choice={"type": "auto"},
    )

    # Extract tool use result
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_evaluation":
            data = block.input
            return ClaudeEvalResult(**data)

    # Fallback: try to parse text content
    for block in response.content:
        if hasattr(block, "text"):
            text = block.text.strip()
            # Try to find JSON in the response
            import re
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    return ClaudeEvalResult(**data)
                except Exception:
                    pass

    # Last resort: return a failure result
    criteria_count = len(tc.get("quality_criteria", []))
    return ClaudeEvalResult(
        criteria_results=[
            CriterionResult(
                criterion=c,
                passed=False,
                reasoning="Evaluator failed to produce structured output",
            )
            for c in tc.get("quality_criteria", ["(unknown)"])
        ],
        overall_assessment="Evaluation failed — could not parse Claude response",
        strengths=[],
        weaknesses=["Evaluator produced unstructured output"],
        pass_count=0,
        total_count=criteria_count,
    )


# ---------------------------------------------------------------------------
# Single test case runner
# ---------------------------------------------------------------------------

async def run_test_case(
    tc: dict,
    verbose: bool = False,
    candidate_limit: int | None = None,
    stagger_s: float = 0.0,
) -> dict:
    """
    Run a single test case end-to-end:
      1. Invoke the agent graph
      2. Run automated checks
      3. Call Claude evaluator
      4. Return a structured result dict
    """
    tc_id = tc["id"]
    logger.info("▶ %s  %s", tc_id, tc["description"])
    t0 = time.perf_counter()

    result: dict[str, Any] = {
        "id": tc_id,
        "description": tc["description"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "graph_duration_s": None,
        "eval_duration_s": None,
        "automated_checks": None,
        "claude_eval": None,
        "passed": False,
        "error": None,
    }

    try:
        # ── Step 1: run the graph ──────────────────────────────────────────
        t_graph = time.perf_counter()
        final_state = await _run_graph_for_test_case(
            tc["input"], candidate_limit=candidate_limit, stagger_s=stagger_s
        )
        result["graph_duration_s"] = round(time.perf_counter() - t_graph, 2)
        recs = final_state.get("final_recommendations", [])
        logger.info(
            "  ✓ graph done in %.1fs — %d recommendations, %d errors",
            result["graph_duration_s"],
            len(recs),
            len(final_state.get("errors", [])),
        )

        # ── Step 2: automated checks ───────────────────────────────────────
        auto = run_automated_checks(tc, final_state)
        result["automated_checks"] = auto
        if verbose:
            for detail in auto["details"]:
                logger.debug("    %s", detail)

        # Hard fail on automated checks prevents LLM evaluation
        if not auto["passed"]:
            logger.warning("  ✗ automated checks failed for %s", tc_id)
            result["passed"] = False
            result["error"] = "Automated checks failed: " + "; ".join(
                d for d in auto["details"] if d.startswith("FAIL")
            )
            return result

        # ── Step 3: Claude evaluator ───────────────────────────────────────
        t_eval = time.perf_counter()
        eval_result = evaluate_with_claude(tc, final_state, verbose=verbose)
        result["eval_duration_s"] = round(time.perf_counter() - t_eval, 2)
        result["claude_eval"] = eval_result.model_dump()

        criteria_pass_rate = (
            eval_result.pass_count / eval_result.total_count
            if eval_result.total_count > 0
            else 0.0
        )
        result["passed"] = criteria_pass_rate >= 0.67  # at least 2/3 criteria pass

        logger.info(
            "  %s Claude eval: %d/%d criteria passed (%.0f%%) in %.1fs",
            "✓" if result["passed"] else "✗",
            eval_result.pass_count,
            eval_result.total_count,
            criteria_pass_rate * 100,
            result["eval_duration_s"],
        )

    except Exception as exc:
        logger.exception("  ✗ exception running %s: %s", tc_id, exc)
        result["error"] = str(exc)
        result["passed"] = False

    result["total_duration_s"] = round(time.perf_counter() - t0, 2)
    return result


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_report(results: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "pass_rate": round(
            sum(1 for r in results if r["passed"]) / len(results) if results else 0,
            4,
        ),
        "results": results,
    }
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    logger.info("Report written to %s", output_path)


# ---------------------------------------------------------------------------
# ASCII summary table
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]) -> None:
    col_w = [8, 54, 7, 10, 10, 10]
    header = ["ID", "Description", "Pass?", "Recs", "Graph(s)", "Eval(s)"]
    sep = "  ".join("─" * w for w in col_w)

    print()
    print("  ".join(h.ljust(w) for h, w in zip(header, col_w)))
    print(sep)

    for r in results:
        auto = r.get("automated_checks") or {}
        recs = auto.get("recommendation_count", "?")
        graph_s = f"{r.get('graph_duration_s', '?')}"
        eval_s = f"{r.get('eval_duration_s', '?')}"
        status = "✓ PASS" if r["passed"] else "✗ FAIL"
        desc = r["description"][:col_w[1]]
        print(
            "  ".join([
                r["id"].ljust(col_w[0]),
                desc.ljust(col_w[1]),
                status.ljust(col_w[2]),
                str(recs).ljust(col_w[3]),
                graph_s.ljust(col_w[4]),
                eval_s.ljust(col_w[5]),
            ])
        )

    print(sep)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    rate = passed / total * 100 if total else 0
    print(f"\n  Results: {passed}/{total} passed  ({rate:.0f}%)")
    if rate >= 70:
        print("  ✓ Pass threshold met (≥70%)")
    else:
        print("  ✗ Pass threshold NOT met (<70%)")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(
    test_id: str | None = None,
    verbose: bool = False,
    candidate_limit: int | None = None,
    cooldown_s: float = 0.0,
    stagger_s: float = 0.0,
) -> int:
    """
    Main eval runner.  Returns 0 on success (pass rate ≥70%), 1 otherwise.

    Parameters
    ----------
    test_id:
        Run only the test case with this ID (optional).
    verbose:
        Enable debug logging.
    candidate_limit:
        Override the number of candidate species passed to each scoring agent.
        Use ~20 when the Anthropic org token budget is tight (default: graph's
        own CANDIDATE_LIMIT=50).  20 candidates keeps token usage to ~3,600
        output tokens per test case — well under the 8k/min rate limit.
    cooldown_s:
        Seconds to wait between test cases.  Useful when rate-limited.
    stagger_s:
        Seconds between staggered scoring agent calls.  When > 0, the 6
        parallel scoring agents are delayed by i * stagger_s seconds each
        (0, N, 2N, …, 5N), spreading the token burst over time.
        Recommended: 10 s → agents spread over 50 s → ~1,200 tokens per
        10-second window, well under the 8k/min rate limit.
    """
    # Load test cases
    if not TEST_CASES_PATH.exists():
        logger.error("Test cases file not found: %s", TEST_CASES_PATH)
        return 1

    with TEST_CASES_PATH.open() as fh:
        test_cases: list[dict] = json.load(fh)

    if test_id:
        test_cases = [tc for tc in test_cases if tc["id"] == test_id]
        if not test_cases:
            logger.error("No test case with id=%r", test_id)
            return 1

    if candidate_limit:
        logger.info(
            "Running %d test case(s) with candidate_limit=%d (rate-limit-safe mode).",
            len(test_cases),
            candidate_limit,
        )
    else:
        logger.info("Running %d test case(s)", len(test_cases))

    if stagger_s > 0:
        logger.info(
            "Stagger mode: scoring agents spread %.0fs apart (6 agents × %.0fs = ~%.0fs per test case).",
            stagger_s,
            stagger_s,
            stagger_s * 5,
        )

    # Run sequentially to avoid hammering the API / overwhelming logs
    results: list[dict] = []
    for i, tc in enumerate(test_cases):
        result = await run_test_case(
            tc, verbose=verbose, candidate_limit=candidate_limit, stagger_s=stagger_s
        )
        results.append(result)
        if cooldown_s > 0 and i < len(test_cases) - 1:
            logger.info("  ⏱  Cooling down for %.0fs before next case…", cooldown_s)
            await asyncio.sleep(cooldown_s)

    # Write report
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_DIR / f"eval_{ts}.json"
    write_report(results, report_path)

    # Print summary table
    print_summary(results)

    # Exit code
    passed = sum(1 for r in results if r["passed"])
    pass_rate = passed / len(results) if results else 0
    return 0 if pass_rate >= 0.70 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Jardin garden planner evals")
    parser.add_argument(
        "--test-id",
        metavar="ID",
        help="Run a single test case by id (e.g. tc_001)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging and verbose evaluator output",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=20,
        metavar="N",
        help=(
            "Number of candidate species to score per test case (default: 20). "
            "Use 20 to stay under the 8k output-token/min rate limit; "
            "use 50 (the production default) for the most realistic eval."
        ),
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=15.0,
        metavar="SECS",
        help="Seconds to wait between test cases (default: 15). Increase if rate-limited.",
    )
    parser.add_argument(
        "--stagger",
        type=float,
        default=10.0,
        metavar="SECS",
        help=(
            "Seconds between staggered scoring agent calls (default: 10). "
            "Each of the 6 parallel scoring agents is delayed by i * SECS "
            "before firing, spreading the token burst over time and preventing "
            "the 8k output-token/min rate limit from being hit. "
            "Use 0 to disable staggering (parallel burst mode)."
        ),
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    exit_code = asyncio.run(
        main(
            test_id=args.test_id,
            verbose=args.verbose,
            candidate_limit=args.candidates,
            cooldown_s=args.cooldown,
            stagger_s=args.stagger,
        )
    )
    sys.exit(exit_code)
