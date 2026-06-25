# Wikimania Brainstorm Agent — Spec

## Goal

Add a **Brainstorm** button to the Wiki view that calls a backend agent. The agent reads
the current wiki and brainstorms ideas in one of three modes:

1. **Stability** — suggestions to improve the stability/health of the current wiki
   (thin stubs, orphan articles, missing links, shallow coverage, inconsistent depth).
2. **Conflict resolution** — proposals to resolve pages that conflict or overlap
   (contradictions, duplicate-but-distinct framings, competing definitions).
3. **Ideas** — net-new ideas suggested by the wiki, especially when the wiki documents a
   project under construction (new features, next steps, missing components, open questions).

Unlike the **critic** pipeline (`pipeline_critic.py`), which *mutates* articles in place,
the brainstorm agent is **read-only and non-destructive**: it produces a list of
suggestions streamed to the UI. Nothing is written back to `wiki_articles`.

The agent runs against an **open-source model on Ollama** (e.g. `qwen2.5`, `llama3.1`,
`gpt-oss`), independent of whatever `PROVIDER` the rest of the app uses.

## Why this is a good fit for the existing architecture

The **critic** feature is the exact template:

- `POST /api/wikis/{wiki_id}/critic` creates a job + queue and spawns a background task
  (`main.py:436`).
- Progress streams over the shared SSE endpoint `GET /api/jobs/{job_id}/stream`.
- `WikiBrowser.jsx` has a "Run Critic" button (`handleRunCritic`, line 70) and a
  `.critic-panel` event list.

Brainstorm reuses all of this plumbing. The only genuinely new pieces are: a LangGraph
agent module, a dedicated Ollama call path, and a mode selector in the UI.

---

## Approach

### 1. Dedicated Ollama call path (`backend/llm.py`)

The app's global `PROVIDER` may be `cerebras`/`groq`. Brainstorm should always be able to
use a local open-source model regardless. Add a small, self-contained helper that targets
Ollama directly without touching the existing `call_fast` / `call_reasoning` routing.

```python
# llm.py — new config
MODEL_BRAINSTORM = os.getenv("MODEL_BRAINSTORM", "qwen2.5")
BRAINSTORM_PROVIDER = os.getenv("BRAINSTORM_PROVIDER", "ollama")  # ollama | inherit

async def call_brainstorm(system: str, user: str) -> str:
    """Run the brainstorm model. Defaults to Ollama (open-source) regardless of PROVIDER."""
    if BRAINSTORM_PROVIDER == "ollama":
        return await _call_ollama(MODEL_BRAINSTORM, f"{system}\n\n{user}", timeout=240.0)
    return await call_reasoning(system, user)
```

`_call_ollama` already exists (`llm.py:59`). No new HTTP code needed.

`.env.example` additions:
```
# Brainstorm agent (open-source model via Ollama)
BRAINSTORM_PROVIDER=ollama
MODEL_BRAINSTORM=qwen2.5
OLLAMA_BASE_URL=http://172.30.48.1:11434   # WSL → Windows host
```

### 2. The agent (`backend/pipeline_brainstorm.py`)

Implemented as a **LangGraph** agent (mirroring `pipeline_lg.py`) so the three modes share
one graph with a routing node. This satisfies "utilizing an agent in the backend" and keeps
the structure familiar.

**State**
```python
class BrainstormState(TypedDict):
    wiki_id: int
    job_id: int
    mode: str               # "stability" | "conflicts" | "ideas"
    queue: Any              # asyncio.Queue (same SSE bridge as critic)
    articles: list          # loaded wiki articles
    graph: dict             # latest graphify snapshot (links/clusters)
    ideas: list             # final structured suggestions
    error: Optional[str]
```

**Nodes**
1. `node_load_context` — load articles via `db.get_all_articles(wiki_id)` and the latest
   graph via `db.get_latest_graph(wiki_id)`. Emit a `phase` event. Build a compact
   context string: title list, per-article length, wikilink counts, orphan/stub flags,
   and (for conflicts) truncated bodies. Cap total context (e.g. ~12k chars) and batch if
   needed, like the critic's `BATCH = 12`.
2. `node_route` — conditional edge dispatching on `state["mode"]` to one of the three
   brainstorm nodes.
3. `node_brainstorm_stability` / `node_brainstorm_conflicts` / `node_brainstorm_ideas` —
   call `llm.call_brainstorm(system, user)` with the mode-specific prompt; parse a JSON
   array of suggestions; emit one `idea` event per suggestion.
4. `node_finalize` — emit `done` with counts; set job status.

**Graph shape**
```
load_context → route → {stability | conflicts | ideas} → finalize → END
```

**Output contract** — every brainstorm node returns a JSON array of objects:
```json
[
  {
    "title": "Short suggestion headline",
    "rationale": "1–2 sentences on why",
    "related_articles": ["Article A", "Article B"],
    "priority": "high | medium | low"
  }
]
```
Parse with a `<think>`-stripping JSON extractor (reuse `_parse_json_list` from
`pipeline_critic.py` — move it to a shared helper or duplicate the ~8 lines).

**Prompts (sketch)**

- *Stability* — "You are a wiki maintainer. Given this inventory of articles (titles,
  lengths, link counts, stubs, orphans), suggest concrete actions to improve the wiki's
  structural health and coverage balance. Do NOT rewrite articles — propose work items."
- *Conflicts* — "You are a wiki editor. Given these articles, identify pages that conflict,
  overlap, or compete, and for each propose how to resolve them (merge, split, disambiguate,
  reconcile). Only flag genuine conflicts." (Reuses the spirit of
  `FIND_CONTRADICTIONS_PROMPT` but returns *proposals*, not in-place fixes.)
- *Ideas* — "This wiki documents a project that is being built. Given its current contents,
  brainstorm new ideas: missing features, logical next steps, components implied but not yet
  documented, and open questions worth resolving."

### 3. API (`backend/main.py`)

Add an endpoint mirroring `start_critic` (`main.py:436`):

```python
class BrainstormRequest(BaseModel):
    mode: str  # "stability" | "conflicts" | "ideas"

@app.post("/api/wikis/{wiki_id}/brainstorm")
async def start_brainstorm(wiki_id: int, req: BrainstormRequest,
                           _user: dict = Depends(get_current_user)):
    if req.mode not in ("stability", "conflicts", "ideas"):
        raise HTTPException(400, "invalid mode")
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(404, "wiki not found")
    job_id = await db.create_job(wiki_id)
    queue = asyncio.Queue()
    _job_queues[job_id] = queue
    asyncio.create_task(_run_brainstorm_task(wiki_id, job_id, req.mode, queue))
    return {"job_id": job_id}

async def _run_brainstorm_task(wiki_id, job_id, mode, queue):
    try:
        await _brainstorm_pipeline.run_brainstorm(wiki_id, job_id, mode, queue)
    finally:
        _job_queues.pop(job_id, None)   # no 300s sleep — see CRITIQUE_SPEC_2 phase 4
```

Streaming reuses the existing `GET /api/jobs/{job_id}/stream` unchanged.

> Note: this inherits the in-process `_job_queues` limitation called out in
> `CRITIQUE_SPEC_2.md`. If that refactor lands first, brainstorm gets persistence for free.

### 4. SSE event types (new)

| type | payload | meaning |
|------|---------|---------|
| `phase` | `{message}` | agent phase started (reuses critic convention) |
| `idea` | `{title, rationale, related_articles, priority}` | one brainstormed suggestion |
| `done` | `{mode, count, message}` | agent finished |
| `error` | `{message}` | agent failed |

### 5. Frontend (`frontend/src/components/WikiBrowser.jsx` + `index.css`)

Mirror the critic UI:

- A **Brainstorm** split button with a 3-way mode picker (Stability / Resolve Conflicts /
  New Ideas) — a small `<select>` or three menu items next to the existing critic button.
- `handleRunBrainstorm(mode)` — `POST /api/wikis/{wikiId}/brainstorm` with `{mode}`, then
  open `EventSource(streamUrl('/api/jobs/' + job_id + '/stream'))` (same pattern as
  `handleRunCritic`, line 70).
- A `.brainstorm-panel` (clone of `.critic-panel`, lines 186–194) listing `idea` events as
  cards: priority chip + title + rationale + related-article links.
- State: `brainstormRunning`, `brainstormMode`, `brainstormIdeas`, `showBrainstorm`.
- Because output is advisory, each idea card can offer a non-committal action later
  (e.g. "Create stub", "Open critic") — out of scope for v1.

---

## Specific file changes

| File | Change |
|------|--------|
| `backend/llm.py` | Add `MODEL_BRAINSTORM`, `BRAINSTORM_PROVIDER`, `call_brainstorm()` |
| `backend/pipeline_brainstorm.py` | **New** — LangGraph agent, 3 mode nodes, prompts |
| `backend/main.py` | Add `BrainstormRequest`, `POST /api/wikis/{id}/brainstorm`, `_run_brainstorm_task`; import `pipeline_brainstorm` |
| `backend/.env.example` | Add brainstorm/Ollama env vars |
| `frontend/src/components/WikiBrowser.jsx` | Brainstorm button + mode picker + handler + panel |
| `frontend/src/index.css` | `.brainstorm-panel` styles (clone of `.critic-panel`) |
| `CLAUDE.md` | Document the new endpoint, event types, and Ollama brainstorm model |

No database schema changes (reuses `generation_jobs`; no writes to `wiki_articles`).

---

## Acceptance criteria

1. With `BRAINSTORM_PROVIDER=ollama` and Ollama reachable, clicking **Brainstorm →
   Stability** streams `idea` events and ends with `done`, without modifying any article.
2. Each of the three modes returns suggestions tailored to its goal (stability items reference
   stubs/orphans; conflict items reference overlapping pairs; idea items propose net-new work).
3. The brainstorm model is the configured Ollama model even when global `PROVIDER=cerebras`
   (verified by setting an invalid `API_KEY` and confirming brainstorm still works while
   normal generation fails).
4. An empty wiki returns a graceful `done` with `count: 0` and a "nothing to brainstorm yet"
   message.
5. If Ollama is unreachable, the job ends with a clear `error` event (503 surfaced from
   `_call_ollama`) and the button re-enables.
6. The brainstorm panel renders priority + title + rationale + clickable related-article
   links, and closing the panel/tab does not corrupt wiki state (read-only guarantee).

---

## Open questions

- **Persisting results?** v1 streams ephemeral suggestions. A follow-up could store them in a
  `brainstorm_suggestions` table so they survive a refresh and can be checked off.
- **Acting on ideas.** Should "New Ideas" be able to create stub articles directly (hand off
  to `_create_stubs`)? Deferred — keep v1 advisory.
- **Model size.** Reasoning-heavy modes (conflicts) may want `qwen2.5:32b`; stability/ideas
  run fine on the 7B default. Could expose `MODEL_BRAINSTORM` per-mode later.
