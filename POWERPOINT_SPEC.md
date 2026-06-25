# PowerPoint Spec — wikimania

A spec for a slide deck that explains the wikimania project.

## Goal & audience
~10 slides / ~8 min for engineers interested in doc→knowledge-base pipelines. Show upload→wiki→graph
→query and the design choices.

## Build notes
16:9; **python-pptx** or hand-author. `slides2video` can narrate it. Source: `CLAUDE.md`,
`DEPLOYMENT.md` / `AWS_DEPLOYMENT.md`.

## Slide outline
1. **Title** — "wikimania — turn uploaded documents into a linked, queryable wiki."
2. **What it is** — Upload docs → it generates a wiki of interlinked articles you can browse, view as
   a graph, and query. Four tabs: Upload, Wiki, Graph, Query.
3. **Architecture** — React (Vite) SPA + FastAPI backend; the dev server proxies `/api/*` to FastAPI
   (no CORS friction). `main.py` exposes endpoints; `pipeline.py` runs generation.
4. **Async generation** — upload returns a `job_id` immediately; the frontend streams progress via
   SSE from `/api/jobs/{id}/stream`.
5. **Two LLM tiers** — `MODEL_FAST` (llama-3.1-8b-instant) for concept extraction; `MODEL_REASONING`
   (qwen3-32b) for article writing and query answering (via Groq).
6. **Knowledge graph** — uses the **graphify** library directly (`build`/`cluster`/`export`); graph
   JSON stored in `graph_snapshots` (Postgres).
7. **Obsidian-compatible** — articles stored as markdown with `[[wikilinks]]`; `GET /api/wiki/export`
   zips them as an Obsidian vault.
8. **Query** — no embeddings: Postgres `ILIKE` finds relevant articles, then the reasoning LLM answers.
9. **Deploy** — AWS deployment documented (`AWS_DEPLOYMENT.md` / `DEPLOYMENT.md`).
10. **Demo + closing** — upload sample docs → browse wiki/graph → ask a question; run steps via `CLAUDE.md`.

## Assets to capture
Screenshots of the four tabs (especially Graph), the SSE progress stream, and a diagram
(upload → fast-tier extract → graphify → reasoning-tier articles → wiki/graph/query).
