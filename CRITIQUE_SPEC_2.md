# Wikimania Critique & Spec — #2

## Criticism

**Job progress is stored in an in-process Python dict, making every multi-worker deployment silent and every server restart a permanent data loss.**

`_job_queues: dict[int, asyncio.Queue]` in `main.py:28` is the sole bridge between the pipeline task that produces events and the SSE endpoint that streams them to the browser. This design has four concrete failure modes, all reproducible today:

**1. Restart lossiness (always present in production)**  
A pipeline job can take 2–10 minutes. Any server restart during that window — a new deploy, an OOM kill, `--reload` reacting to a code change — destroys the dict. The `asyncio.Task` running the pipeline is cancelled, so no `done`/`error` event is ever emitted. The database record stays at `status='running'` forever (`db.py:274–285` only sets `completed_at` on terminal transitions). The frontend spinner never resolves; the job is silently dead with no recovery path.

**2. Multi-worker unsafety (always present in production)**  
Deploying with `uvicorn --workers 2` (the Railway/Gunicorn default for any non-trivial load) gives each worker process its own `_job_queues`. The `POST /api/wikis/{wiki_id}/documents/upload` that creates the queue lands on worker A; the `GET /api/jobs/{job_id}/stream` SSE request may land on worker B, which has no entry for that `job_id`. `main.py:295-298` then falls through to the static "Job running" message and closes the connection. The user sees one heartbeat then silence, and the upload silently never completes from their perspective.

**3. Critic-pipeline queue leak**  
`main.py:455-456` unconditionally `await asyncio.sleep(300)` before calling `_job_queues.pop(job_id, None)`. Every finished critic run holds a dead `asyncio.Queue` object in the process for five minutes regardless of whether any client is still connected. The standard wiki pipeline removes its entry on the `done`/`error` event (`main.py:309`), but the critic wrapper diverges from this pattern without explanation.

**4. No client-disconnect cleanup**  
When the browser tab is closed mid-stream, FastAPI cancels the `event_generator` coroutine, but the underlying pipeline `asyncio.Task` continues running and keeps `put`-ing events onto the queue. The orphaned queue entry lives in `_job_queues` until the task finishes. For a 50-article job, that may be several minutes of queued writes to a queue with no reader.

Evidence summary:
- `main.py:28` — `_job_queues: dict[int, asyncio.Queue]` (module-level, in-process only)
- `main.py:268-274` — queue created and task spawned in upload handler
- `main.py:287-320` — SSE consumer looks up queue by id, no cross-process fallback
- `main.py:448-456` — critic wrapper: 300-second sleep before cleanup
- `db.py:274-285` — `update_job_status` never marks a job failed on restart

The two sample documents complete in under a minute on a lightly loaded machine, which is why this has not surfaced during development. Any real deployment — a 40-article wiki, a slow reasoning model, two simultaneous workers — hits all four paths regularly.

---

## Spec

### Goal

Job progress must survive server restarts, be accessible from any worker process, and release resources promptly when the client disconnects. The user-visible API (SSE event types, job status endpoint, resume flow) must not change.

### Approach

**Phase 1 — Persist events to the database**

1. Add a `job_events` table:
   ```sql
   CREATE TABLE job_events (
       id         BIGSERIAL PRIMARY KEY,
       job_id     INT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
       seq        INT NOT NULL,
       event_json TEXT NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
   );
   CREATE INDEX ON job_events (job_id, seq);
   ```
2. Replace `await queue.put(event)` throughout `pipeline.py`, `pipeline_lg.py`, and `pipeline_critic.py` with a `db.append_job_event(job_id, event)` call that inserts into `job_events` and increments a per-job sequence counter (stored in a module-level dict or in `generation_jobs.progress` as a counter).
3. The queue object and `_job_queues` dict are no longer needed for persistence — remove them.

**Phase 2 — SSE streams by polling the database**

Replace the `asyncio.Queue`-based event loop in `stream_job` with a database-polling loop:

```python
async def event_generator(job_id: int):
    seq = 0
    yield heartbeat_event()
    while True:
        new_events = await db.get_job_events_after(job_id, seq)
        for ev in new_events:
            yield f"data: {ev['event_json']}\n\n"
            seq = ev['seq']
            if json.loads(ev['event_json']).get('type') in ('done', 'error'):
                return
        if not new_events:
            job = await db.get_job(job_id)
            if job and job['status'] in ('done', 'error'):
                return
            yield heartbeat_event()
        await asyncio.sleep(1.0)
```

This is safe to run in any worker because all workers read from the same Postgres instance.

**Phase 3 — Orphan recovery on startup**

In `lifespan` (before `yield`), add:
```python
await db.mark_orphaned_jobs()
```
which runs:
```sql
UPDATE generation_jobs
SET status = 'error', error = 'Server restarted while job was running', completed_at = now()
WHERE status IN ('running', 'paused_implicit')
  AND created_at < now() - interval '1 hour'
```
This ensures stale `status='running'` rows from prior restarts do not confuse users. The 1-hour threshold is conservative; jobs that genuinely run longer should be re-queried by their doc_id.

**Phase 4 — Remove the critic sleep**

In `main.py:_run_critic_task`, delete the `await asyncio.sleep(300)` and `_job_queues.pop` call. Cleanup is now implicit — there is no queue to release.

### Specific file changes

| File | Change |
|------|--------|
| `backend/db.py` | Add `job_events` table in `init_pool`; add `append_job_event`, `get_job_events_after`, `mark_orphaned_jobs` |
| `backend/main.py` | Remove `_job_queues` dict; update `stream_job` to poll DB; update `lifespan` to call `mark_orphaned_jobs`; remove critic sleep |
| `backend/pipeline.py` | Replace `queue.put(...)` with `db.append_job_event(job_id, ...)`; remove `Queue` parameter from all signatures |
| `backend/pipeline_lg.py` | Same queue→db replacement |
| `backend/pipeline_critic.py` | Same queue→db replacement |

No API changes, no schema changes beyond the new `job_events` table.

### Acceptance criteria

1. Start a wiki generation job; kill and restart the server mid-job; the job appears as `status='error'` (not `'running'`) after restart, and the frontend shows a clear failure message rather than an infinite spinner.
2. Start the uvicorn server with `--workers 2`; upload a document; open the SSE stream in a second browser tab; both tabs receive all events in real time.
3. Refresh the SSE stream page after a job has already completed; events replay from the beginning (or at minimum the terminal `done`/`error` event is returned) without requiring the pipeline to still be running.
4. The critic pipeline no longer holds any in-memory state after the `done` event is emitted.
5. Closing the browser mid-stream does not prevent the background pipeline task from completing and writing its `done` event to the database.
