# 🌿 jardin

AI-powered native plant recommendations. Tell jardin where your garden is and what you care about — pollinators, soil health, food, biodiversity — and it returns a ranked list of native species scored by **six specialist AI agents** running in parallel.

---

## Running with Docker Compose

**Requirements:** Docker Desktop (or Docker + Compose plugin), an Anthropic API key.

```bash
git clone <repo-url> && cd jardin
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

| | |
|---|---|
| **App** | http://localhost:3000 |
| **API** | http://localhost:8000 |
| **API docs** | http://localhost:8000/docs |
| **Health** | http://localhost:8000/health |

The first build downloads the `BAAI/bge-base-en-v1.5` embedding model (~400 MB). Subsequent starts use the cached image.

### Seed the species database

The app needs species data to make recommendations. Run this after `docker compose up`:

```bash
# Load 200 French native species and AI-score them
docker compose exec backend python -m scripts.load_species --country FR --limit 200 --score

# Add more countries
docker compose exec backend python -m scripts.load_species \
  --country GB --country DE --country IE --limit 300 --score
```

The `--score` flag calls Claude to assign ecological scores. Without it, placeholder scores (0.5) are stored and the recommendation pipeline still works but ranks will be less meaningful.

### Stop / reset

```bash
docker compose down          # stop containers, keep database volume
docker compose down -v       # stop and delete the database volume (full reset)
```

---

## Architecture

```
Browser  →  Next.js (port 3000)  →  FastAPI (port 8000)  →  Postgres + pgvector
                                          │
                              POST /api/recommendations
                                          │
                                  Background task
                                          │
                         ┌────── fetch_candidates ──────┐
                         │   top-50 species from DB      │
                         └──────────────┬────────────────┘
                                        │  fan-out (parallel)
          ┌──────────┬──────────┬───────┼───────┬────────────┬──────────┐
          ▼          ▼          ▼       ▼       ▼            ▼          ▼
     Pollinators  Insects     Soil  Environment Food/Utility  Size
       agent       agent     agent    agent       agent      agent
          └──────────┴──────────┴───────┼───────┴────────────┴──────────┘
                                        │  fan-in
                                 merge_and_rank
                                 (top-20, weighted)
                                        │
                              SSE stream → browser
```

Results stream live to the browser as each agent finishes via Server-Sent Events. Each species detail page has a streaming chat interface backed by the same Claude model.

### Scoring dimensions

| Dimension | What the agent evaluates |
|---|---|
| **Pollinators** | Value to bees, butterflies, moths, hoverflies |
| **Insects** | Host plant for caterpillars, beetles, other invertebrates |
| **Soil** | Nitrogen fixation, mycorrhizal networks, organic matter |
| **Environment** | Carbon sequestration, erosion control, microclimate |
| **Food / utility** | Edible parts, medicinal uses, practical harvests |
| **Size** | Suitability for compact spaces and containers (high = compact, low = large tree) |

Each priority can be weighted 0–1 by the user; the final score is a weighted average.

### Key components

| Path | What it does |
|---|---|
| `backend/agents/graph.py` | LangGraph pipeline — 8 nodes, parallel fan-out/fan-in |
| `backend/agents/prompts.py` | Versioned system prompts for all 6 scoring agents |
| `backend/core/logging.py` | Structlog JSON logging (human-readable in dev, JSON in prod) |
| `backend/core/tracing.py` | Per-run traces written to `backend/traces/YYYYMMDD/` |
| `backend/core/resilience.py` | `@with_retry` — exponential backoff on Anthropic 429s |
| `backend/app/routers/recommendations.py` | POST + SSE stream endpoints |
| `backend/app/routers/chat.py` | Streaming species chat endpoint |
| `backend/app/services/recommendation_cache.py` | Content-hash caching — avoids re-running the full agent graph for identical requests |
| `backend/services/gbif_loader.py` | GBIF fetch + sentence-transformers embeddings |
| `backend/services/species_scorer.py` | Per-species Claude scoring (used by `--score`) |
| `frontend/components/ScoreBar.tsx` | Shared score bar + colour logic used across cards and detail pages |

---

## Environment variables

Copy `.env.example` to `.env` and fill in the values before running.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | — | Anthropic API key |
| `DATABASE_URL` | No | `postgresql://postgres:postgres@db:5432/garden_planner` | Postgres connection string |
| `GBIF_API_BASE` | No | `https://api.gbif.org/v1` | GBIF API base URL |
| `NEXT_PUBLIC_API_URL` | No | `http://localhost:8000` | API URL seen by the browser |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `LOG_FORMAT` | No | auto | `json` forces JSON logs; omit for auto-detect |

---

## Running tests

### Backend (pytest)

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

Tests cover `agents/graph.py` (scoring, ranking, JSON parsing), `services/gbif_loader.py` (kingdom filtering, name extraction), `app/services/recommendation_cache.py` (content-hash determinism), and the FastAPI endpoints via `TestClient`.

### Frontend (vitest)

```bash
cd frontend
npm test
```

Tests cover `ScoreBar` colour thresholds and the API client helpers (`makeStreamUrl`, `createRecommendation`, `geocode`).

---

## Running evals

The eval suite tests the full agent pipeline against **11 real-world garden scenarios** across Europe and uses Claude as an automated evaluator.

```bash
# Using the shell wrapper (handles venv activation and env export)
./evals/run_evals.sh

# Single test case with verbose output
./evals/run_evals.sh --test-id tc_001 --verbose

# Rate-limit-safe run: stagger agents 10 s apart, 20 candidates, 60 s cooldown between cases
./evals/run_evals.sh --candidates 20 --stagger 10 --cooldown 60
```

Or call the Python script directly (note: use `set -a` to export env vars):

```bash
cd backend && source .venv/bin/activate
set -a && source ../.env && set +a
python ../evals/run_evals.py --candidates 20 --stagger 10 --cooldown 60
```

### Eval CLI flags

| Flag | Default | Description |
|---|---|---|
| `--test-id ID` | all | Run only a single test case (e.g. `tc_001`) |
| `--verbose` | off | Enable debug logging and show evaluator prompt details |
| `--candidates N` | 20 | Species per agent call. Use 20 for rate-limit safety; 50 for production-realistic runs |
| `--stagger SECS` | 10 | Delay between agent calls. Spreads the 6 parallel agents over `SECS × 5` seconds to stay under the Anthropic output-token rate limit |
| `--cooldown SECS` | 15 | Wait between test cases |

The suite exits with code 1 if the pass rate falls below 70%. Reports are written to `evals/reports/eval_{timestamp}.json`.

### Test case coverage

| ID | Location | Priority focus |
|---|---|---|
| tc_001 | Brittany, France | High pollinators |
| tc_002 | Yorkshire Dales, UK | High insect hosts |
| tc_003 | Bavaria, Germany | Soil health |
| tc_004 | Amsterdam, Netherlands | Environmental resilience |
| tc_005 | Connemara, Ireland | Food & medicinal utility |
| tc_006 | Seville, Spain | All dimensions balanced |
| tc_007 | Kiruna, Sweden | Subarctic conditions |
| tc_008 | Scottish Highlands | Extreme pollinator focus |
| tc_009 | Strasbourg, France | Soil only (near-zero other) |
| tc_010 | Bastogne, Belgium | Pollinators + food utility |
| tc_011 | Paris, France | Compact / container garden (high size priority) |

---

## Project layout

```
jardin/
├── backend/
│   ├── agents/
│   │   ├── graph.py          # LangGraph multi-agent pipeline (6 scoring agents)
│   │   ├── prompts.py        # Versioned system prompts (v1.0.0)
│   │   └── runner.py         # run_garden_planner() — programmatic wrapper
│   ├── app/
│   │   ├── main.py           # FastAPI app + /health endpoint
│   │   ├── models/           # SQLAlchemy models (species, garden_request, recommendation)
│   │   ├── db/               # Database session / connection setup
│   │   ├── services/
│   │   │   └── recommendation_cache.py  # Content-hash cache logic
│   │   └── routers/
│   │       ├── recommendations.py
│   │       └── chat.py
│   ├── core/
│   │   ├── logging.py        # Structlog JSON setup
│   │   ├── tracing.py        # Node traces → backend/traces/
│   │   └── resilience.py     # @with_retry decorator
│   ├── services/
│   │   ├── gbif_loader.py    # GBIF + embeddings
│   │   └── species_scorer.py # Per-species Claude scoring
│   ├── scripts/
│   │   └── load_species.py   # Data loading CLI
│   ├── tests/
│   │   ├── conftest.py       # Shared fixtures and path bootstrap
│   │   ├── test_graph.py     # _safe_score, _parse_json_response, merge_and_rank
│   │   ├── test_gbif_loader.py
│   │   ├── test_recommendation_cache.py
│   │   └── test_api_integration.py
│   └── alembic/              # DB migrations
├── frontend/
│   ├── app/
│   │   ├── page.tsx          # 3-step garden wizard
│   │   ├── results/[requestId]/page.tsx
│   │   └── species/[id]/page.tsx
│   ├── components/
│   │   ├── ScoreBar.tsx      # Shared score bar + colour logic
│   │   ├── SpeciesCard.tsx
│   │   └── PrioritySlider.tsx
│   └── __tests__/
│       ├── scoreBar.test.ts
│       └── api.test.ts
├── evals/
│   ├── test_cases.json       # 11 test cases
│   ├── run_evals.py          # Eval harness (direct graph invocation, no HTTP)
│   └── run_evals.sh          # Shell wrapper (handles venv + env export)
└── docker-compose.yml
```
