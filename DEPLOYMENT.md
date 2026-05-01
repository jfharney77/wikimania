# Deployment Notes

## Railway Deployment

Same two-service pattern as `read_claude`.

### Services

| Service | Build context | Dockerfile |
|---------|--------------|------------|
| `wikimania-backend` | `backend/` | `backend/Dockerfile` |
| `wikimania-frontend` | `frontend/` | `frontend/Dockerfile` |

Add a **PostgreSQL** plugin to the Railway project. Railway injects `DATABASE_URL` automatically into the backend service.

### Backend environment variables

| Var | Value |
|-----|-------|
| `PROVIDER` | `groq` |
| `MODEL_FAST` | `llama-3.1-8b-instant` |
| `MODEL_REASONING` | `qwen/qwen3-32b` |
| `GROQ_API_KEY` | *(set in Railway dashboard — never commit)* |
| `DATABASE_URL` | *(injected by Railway Postgres plugin)* |
| `CORS_ORIGINS` | `https://your-frontend.up.railway.app` |

### Frontend build variable

| Var | Value |
|-----|-------|
| `VITE_API_URL` | `https://your-backend.up.railway.app` |

`VITE_API_URL` must be set as a **build variable** in Railway (it is baked into the static bundle at build time).

### Deploying new code

Railway's `redeploy` reuses the last snapshot. To deploy the latest GitHub commit, trigger via the Railway dashboard or use the Railway MCP agent with an explicit commit hash.

### Important notes

- Do NOT use the `groq` Python SDK — use `httpx` directly. The SDK has connection issues inside Railway containers.
- `PORT` is injected by Railway at runtime; the backend Dockerfile uses `${PORT:-8000}`.
- The frontend Dockerfile uses a multi-stage build: Node 20 builds the Vite bundle, then `serve` serves it.

## LLM Providers

### Groq (production)

Available verified models:
- `llama-3.1-8b-instant` — fast concept extraction
- `qwen/qwen3-32b` — reasoning, article writing
- `llama-3.3-70b-versatile` — best quality alternative

### Ollama (local dev — WSL)

Ollama runs on the Windows host. From WSL the host IP is typically `172.30.48.1`.

```
PROVIDER=ollama
MODEL_FAST=qwen2.5
MODEL_REASONING=qwen2.5:32b
OLLAMA_BASE_URL=http://172.30.48.1:11434
```

To expose local Ollama to Railway via ngrok:
```bash
ngrok http http://172.30.48.1:11434
# Set OLLAMA_BASE_URL to the ngrok HTTPS URL
```

### Stub mode (no LLM)

Set `MODEL_FAST=no_model` — not currently wired but easy to add for local UI development.

## Local Dev Quick Start

```bash
# 1. Start Postgres (if not running)
# 2. Backend
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env  # edit DATABASE_URL and GROQ_API_KEY
.venv/bin/uvicorn main:app --reload --port 8000

# 3. Frontend
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```
