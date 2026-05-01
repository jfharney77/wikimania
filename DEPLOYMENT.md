# Railway Deployment

## One-time setup

### 1. Create a Railway project

Go to [railway.app](https://railway.app) → **New Project** → **Empty Project**.

---

### 2. Add a PostgreSQL database

Inside the project → **+ Add service** → **Database** → **PostgreSQL**.

Railway will provision a Postgres instance and inject `DATABASE_URL` automatically into any service you link it to.

---

### 3. Deploy the backend

**+ Add service** → **GitHub Repo** → select `wikimania`.

In the service settings:
- **Root Directory**: `backend`
- **Dockerfile Path**: `Dockerfile` (auto-detected)

Under **Variables**, add:

| Variable | Value |
|----------|-------|
| `PROVIDER` | `groq` |
| `MODEL_FAST` | `llama-3.1-8b-instant` |
| `MODEL_REASONING` | `qwen/qwen3-32b` |
| `GROQ_API_KEY` | *(your Groq key — never commit this)* |
| `CORS_ORIGINS` | *(leave blank for now — fill in after step 4)* |

`DATABASE_URL` is injected automatically from the Postgres plugin — no need to add it manually.

Railway injects `PORT` at runtime; the backend Dockerfile already reads it with `${PORT:-8000}`.

Deploy the service and wait for it to go green. Copy the generated URL (e.g. `https://wikimania-backend-production.up.railway.app`).

---

### 4. Deploy the frontend

**+ Add service** → **GitHub Repo** → select `wikimania` again.

In the service settings:
- **Root Directory**: `frontend`
- **Dockerfile Path**: `Dockerfile` (auto-detected)

Under **Variables**, add:

| Variable | Value |
|----------|-------|
| `VITE_API_URL` | `https://wikimania-backend-production.up.railway.app` *(backend URL from step 3)* |

> `VITE_API_URL` is a **build-time** variable — it gets baked into the static bundle. If you change the backend URL later, you must redeploy the frontend.

Deploy and wait for it to go green. Copy the frontend URL.

---

### 5. Set CORS_ORIGINS on the backend

Go back to the **backend** service → **Variables**.

Set:

| Variable | Value |
|----------|-------|
| `CORS_ORIGINS` | `https://your-frontend.up.railway.app` |

Redeploy the backend. The app is now live.

---

## Deploying new code

Push to `main` — Railway auto-deploys both services on every push.

---

## Local dev

```bash
# Backend (port 8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY and DATABASE_URL
.venv/bin/uvicorn main:app --reload --port 8000

# Frontend (port 5173)
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```

---

## LLM providers

### Groq (production default)

| Model var | Value |
|-----------|-------|
| `MODEL_FAST` | `llama-3.1-8b-instant` |
| `MODEL_REASONING` | `qwen/qwen3-32b` |

### Ollama (local dev — WSL)

```
PROVIDER=ollama
MODEL_FAST=qwen2.5
MODEL_REASONING=qwen2.5:32b
OLLAMA_BASE_URL=http://172.30.48.1:11434
```

---

## Notes

- Do NOT use the `groq` Python SDK — use `httpx` directly (SDK has connection issues inside Railway containers).
- `PORT` is injected by Railway at runtime; both Dockerfiles read it with `${PORT:-<default>}`.
- The frontend Dockerfile is a multi-stage build: Node 20 builds the Vite bundle, then `serve` serves the static files.
- Railway's Postgres plugin may emit `postgres://` URLs — the backend normalizes these to `postgresql://` automatically.
