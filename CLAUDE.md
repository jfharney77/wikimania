# CLAUDE.md

## Architecture

React (Vite) frontend + FastAPI backend. The Vite dev server proxies `/api/*` to the FastAPI server, so there are no CORS issues during development.

- `frontend/` — Vite + React 18 SPA (four tabs: Upload, Wiki, Graph, Query)
- `backend/` — FastAPI app; `main.py` exposes all endpoints, `pipeline.py` runs wiki generation

## Key design decisions

- **Wiki generation is async** — upload returns a `job_id` immediately; the frontend streams progress via SSE from `/api/jobs/{id}/stream`.
- **Two LLM tiers** — `MODEL_FAST` (llama-3.1-8b-instant) for concept extraction; `MODEL_REASONING` (qwen/qwen3-32b) for article writing and query answering.
- **graphify used as a Python library** — after every upload, `pipeline.py` imports `graphify.build`, `graphify.cluster`, `graphify.export` directly (no subprocess). The resulting graph JSON is stored in the `graph_snapshots` table.
- **Obsidian-compatible wikilinks** — articles are stored as markdown with `[[Article Title]]` syntax. `GET /api/wiki/export` zips all articles for download as an Obsidian vault.
- **No embeddings / vector search** — queries use PostgreSQL `ILIKE` to find relevant articles, then pass them to the reasoning LLM.

## Running locally

**Backend** (port 8000):
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEY and DATABASE_URL
.venv/bin/uvicorn main:app --reload --port 8000
```

**Frontend** (port 5173):
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Prerequisites

- PostgreSQL running with a `wikimania` database
- Groq API key **or** Ollama running locally
- `graphifyy` Python package (installed via requirements.txt)

For local Ollama (WSL), set in `backend/.env`:
```
PROVIDER=ollama
MODEL_FAST=qwen2.5
MODEL_REASONING=qwen2.5:32b
OLLAMA_BASE_URL=http://172.30.48.1:11434
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/documents/upload` | Upload a .md file, start wiki generation |
| GET | `/api/documents` | List uploaded documents |
| GET | `/api/jobs/{id}/stream` | SSE stream of generation progress |
| GET | `/api/jobs/{id}` | Job status |
| GET | `/api/wiki/articles` | List all wiki articles |
| GET | `/api/wiki/articles/{id}` | Full article content |
| POST | `/api/wiki/query` | Ask a question → LLM-synthesized answer |
| GET | `/api/wiki/graph` | Latest graphify graph JSON |
| GET | `/api/wiki/export` | Download Obsidian vault zip |

## SSE event types

| type | payload | meaning |
|------|---------|---------|
| `phase` | `{phase, message}` | pipeline phase started |
| `concepts` | `{titles, count}` | concept list extracted |
| `article` | `{title, status, n, total}` | article written/updated |
| `graph_done` | `{message}` | graphify rebuild complete → triggers browser notification |
| `done` | `{articles_created, articles_updated}` | job finished |
| `error` | `{message}` | job failed |
| `heartbeat` | — | keep-alive (every 25 s) |

## Database tables

- `source_documents` — uploaded files + status
- `wiki_articles` — generated articles (markdown with [[wikilinks]])
- `article_links` — parsed wikilink edges (from_id → to_title)
- `generation_jobs` — job status + error log
- `graph_snapshots` — graphify JSON output (latest is served to frontend)
