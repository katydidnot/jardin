# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository (jardin).

## Monorepo Structure

```
jardin/
  backend/          # Python FastAPI + LangGraph agent service
  frontend/         # Next.js 16 app (TypeScript, Tailwind, App Router)
  evals/            # Agent evaluation scripts
  docker-compose.yml
  .env.example
```

## Backend (Python / FastAPI)

**Python 3.11**, virtualenv at `backend/.venv/`.

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload          # dev server on :8000
pip install -r requirements.txt        # install deps
alembic upgrade head                   # run DB migrations
```

App entry point: `backend/app/main.py`. Internal layout:
- `app/routers/` — FastAPI route handlers
- `app/agents/` — LangGraph agent definitions
- `app/models/` — SQLAlchemy models
- `app/db/` — database session / connection setup

Key dependencies: `fastapi`, `uvicorn`, `langgraph`, `langchain-anthropic`, `anthropic`, `pgvector`, `sqlalchemy`, `alembic`, `pydantic`, `httpx`.

## Frontend (Next.js)

**Next.js 16**, App Router, TypeScript, Tailwind CSS.

```bash
cd frontend
npm run dev          # dev server on :3000
npm run build
npm run lint
```

Key dependencies: `@tanstack/react-query`, `lucide-react`.

## Running with Docker

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY
docker compose up --build
```

Services: `db` (Postgres 16 + pgvector on :5432), `backend` (:8000), `frontend` (:3000).

## Environment Variables

See `.env.example` for all required variables. `ANTHROPIC_API_KEY` is required for agent features. `DATABASE_URL` defaults to the local Postgres instance. `GBIF_API_BASE` points to the GBIF species API.
