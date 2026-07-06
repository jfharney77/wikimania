import asyncio
import json
import os
import zipfile
import io

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi import Query as QParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

import auth
import db

_use_langgraph = os.getenv("USE_LANGGRAPH", "false").lower() == "true"
if _use_langgraph:
    import pipeline_lg as wiki_pipeline
else:
    import pipeline as wiki_pipeline

import pipeline_critic as _critic_pipeline
import pipeline_brainstorm as _brainstorm_pipeline
import graph_eval
import graph_path as graph_path_mod
import ingest as ingest_mod
import gmail_client
import gmail_sync
import email_ingest


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    authorization: str = Header(None),
    token: str = QParam(None),
) -> dict:
    """Accepts token from Authorization: Bearer header or ?token= (SSE fallback)."""
    t = None
    if authorization and authorization.startswith("Bearer "):
        t = authorization[7:]
    elif token:
        t = token
    if not t:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = auth.decode_token(t)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Purpose-scoped tokens (e.g. the Gmail OAuth state, which transits
    # browser URLs) are not session tokens.
    if payload.get("purpose"):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required.")
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    await asyncio.wait_for(db.init_pool(database_url), timeout=30)

    # Fail any jobs left 'running' by a prior crash/restart so the UI doesn't spin forever.
    orphaned = await db.mark_orphaned_jobs()
    if orphaned:
        print(f"[wikimania] Marked {orphaned} orphaned job(s) as errored on startup.")

    # Bootstrap first admin if no users exist
    first_admin = os.getenv("FIRST_ADMIN_USERNAME", "").strip()
    first_pass = os.getenv("FIRST_ADMIN_PASSWORD", "").strip()
    if first_admin and first_pass:
        if await db.count_users() == 0:
            await db.create_user(first_admin, auth.hash_password(first_pass), role="admin")
            print(f"[wikimania] Created initial admin: {first_admin}")
    elif await db.count_users() == 0:
        print("[wikimania] WARNING: No users exist and FIRST_ADMIN_USERNAME/PASSWORD are not set.")

    # Background Gmail label/forward sync (Phase 2/4). Only runs when OAuth
    # credentials are configured; state persists in Postgres, so it resumes
    # cleanly across restarts.
    if gmail_client.is_configured():
        gmail_sync.start()
        print(f"[wikimania] Gmail sync loop started (every {gmail_sync.SYNC_INTERVAL}s).")
    else:
        print("[wikimania] Gmail sync disabled — set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET to enable.")

    yield
    await gmail_sync.stop()
    await db.close_pool()


app = FastAPI(title="Wikimania API", lifespan=lifespan)

_cors_origins = [
    o.strip().rstrip("/")
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health (public)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth endpoints (public)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register", status_code=201)
async def register(req: RegisterRequest):
    if len(req.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters.")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    try:
        user = await db.create_user(
            req.username.strip(),
            auth.hash_password(req.password),
        )
        return {"id": user["id"], "username": user["username"], "role": user["role"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = await db.get_user_by_username(req.username.strip())
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = auth.create_token(user["id"], user["username"], user["role"])
    return {"token": token, "username": user["username"], "role": user["role"]}


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["sub"], "username": user["username"], "role": user["role"]}


# ---------------------------------------------------------------------------
# Admin: user management
# ---------------------------------------------------------------------------

class AdminCreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class UpdateRoleRequest(BaseModel):
    role: str


@app.get("/api/admin/users")
async def admin_list_users(_admin: dict = Depends(require_admin)):
    return {"users": await db.list_users()}


@app.post("/api/admin/users", status_code=201)
async def admin_create_user(req: AdminCreateUserRequest, _admin: dict = Depends(require_admin)):
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'.")
    if len(req.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters.")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    try:
        user = await db.create_user(
            req.username.strip(),
            auth.hash_password(req.password),
            req.role,
        )
        return {"id": user["id"], "username": user["username"], "role": user["role"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.patch("/api/admin/users/{user_id}/role")
async def admin_update_role(user_id: int, req: UpdateRoleRequest, admin: dict = Depends(require_admin)):
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'.")
    if str(user_id) == admin.get("sub"):
        raise HTTPException(status_code=400, detail="Cannot change your own role.")
    user = await db.update_user_role(user_id, req.role)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    if str(user_id) == admin.get("sub"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    deleted = await db.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": "User deleted."}


# ---------------------------------------------------------------------------
# Wikis
# ---------------------------------------------------------------------------

class WikiCreate(BaseModel):
    name: str


@app.get("/api/wikis")
async def list_wikis(_user: dict = Depends(get_current_user)):
    return {"wikis": await db.list_wikis()}


@app.post("/api/wikis", status_code=201)
async def create_wiki(req: WikiCreate, _user: dict = Depends(get_current_user)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Wiki name is required.")
    return await db.create_wiki(req.name.strip())


@app.delete("/api/wikis/{wiki_id}")
async def delete_wiki(wiki_id: int, _user: dict = Depends(get_current_user)):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    count = await db.delete_wiki(wiki_id)
    return {"message": f"Wiki '{wiki['name']}' deleted.", "articles_deleted": count}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.post("/api/wikis/{wiki_id}/documents/upload")
async def upload_document(
    wiki_id: int,
    file: UploadFile = File(...),
    parallel_writes: int = Form(1),
    _user: dict = Depends(get_current_user),
):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    if not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are accepted.")

    content = (await file.read()).decode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="File is empty.")

    # Phase 0: all ingestion goes through the IngestItem contract.
    # (wiki_id, 'upload', filename) is the identity key — re-uploading the same
    # file updates the existing document instead of duplicating it.
    item = ingest_mod.IngestItem(
        source="upload",
        source_id=file.filename,
        title=file.filename,
        body_text=content,
        metadata={"content_type": file.content_type or "text/markdown"},
    )
    result = await ingest_mod.ingest_item(wiki_id, item, parallel_writes=parallel_writes)

    return {
        "job_id": result.job_id,
        "doc_id": result.doc_id,
        "filename": file.filename,
        "action": result.action,
    }


@app.get("/api/wikis/{wiki_id}/documents")
async def list_documents(wiki_id: int, _user: dict = Depends(get_current_user)):
    return {"documents": await db.list_documents(wiki_id)}


# ---------------------------------------------------------------------------
# Jobs / SSE
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: int, _user: dict = Depends(get_current_user)):
    async def event_generator():
        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

        if not await db.get_job(job_id):
            yield f"data: {json.dumps({'type': 'unknown', 'message': 'Job unknown'})}\n\n"
            return

        # Poll the durable event log. Safe across restarts and worker processes
        # because every worker reads the same Postgres rows. Events replay from
        # seq 0, so a late or reconnecting client still sees the full history.
        seq = 0
        while True:
            events = await db.get_job_events_after(job_id, seq)
            if events:
                for ev in events:
                    yield f"data: {ev['event_json']}\n\n"
                    seq = ev["seq"]
                    if json.loads(ev["event_json"]).get("type") in ("done", "error"):
                        return
            else:
                job = await db.get_job(job_id)
                if job and job["status"] in ("done", "error"):
                    # Terminal status with no further events to drain — stop streaming.
                    return
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int, _user: dict = Depends(get_current_user)):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: int, _user: dict = Depends(get_current_user)):
    job = await db.get_job(job_id)
    if not job or job["status"] != "paused":
        raise HTTPException(status_code=400, detail="Job is not paused.")

    state = json.loads(job["paused_state"])
    doc = await db.get_document(state["doc_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Source document not found.")

    asyncio.create_task(
        wiki_pipeline.resume_wiki(
            wiki_id=job["wiki_id"],
            job_id=job_id,
            doc_id=state["doc_id"],
            concepts=state["remaining_concepts"],
            content=doc["content"],
            created_so_far=state["created"],
            updated_so_far=state["updated"],
            parallel_writes=state.get("parallel_writes", 1),
        )
    )

    return {"job_id": job_id}


# ---------------------------------------------------------------------------
# Wiki articles
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/articles")
async def list_articles(wiki_id: int, _user: dict = Depends(get_current_user)):
    return {"articles": await db.list_articles(wiki_id)}


@app.get("/api/wikis/{wiki_id}/articles/{article_id}")
async def get_article(wiki_id: int, article_id: int, _user: dict = Depends(get_current_user)):
    article = await db.get_article_by_id(article_id)
    if not article or article["wiki_id"] != wiki_id:
        raise HTTPException(status_code=404, detail="Article not found.")
    return article


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str


@app.post("/api/wikis/{wiki_id}/query")
async def query_wiki(wiki_id: int, req: QueryRequest, _user: dict = Depends(get_current_user)):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    return await wiki_pipeline.answer_query(wiki_id, req.question)


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/graph")
async def get_graph(wiki_id: int, _user: dict = Depends(get_current_user)):
    graph_json = await db.get_latest_graph(wiki_id)
    if not graph_json:
        return {"graph": None, "message": "No graph yet — upload a document first."}
    return {"graph": json.loads(graph_json)}


@app.get("/api/wikis/{wiki_id}/graph/eval")
async def eval_graph(wiki_id: int, _user: dict = Depends(get_current_user)):
    graph_json = await db.get_latest_graph(wiki_id)
    if not graph_json:
        return {"eval": None, "message": "No graph yet — upload a document first."}
    return {"eval": graph_eval.evaluate_graph(json.loads(graph_json))}


@app.get("/api/wikis/{wiki_id}/graph/path")
async def graph_path(
    wiki_id: int,
    source: str = QParam(...),
    target: str = QParam(...),
    _user: dict = Depends(get_current_user),
):
    graph_json = await db.get_latest_graph(wiki_id)
    if not graph_json:
        return {"path": None, "message": "No graph yet — upload a document first."}
    graph = json.loads(graph_json)

    source_id = graph_path_mod.resolve_node(graph, source)
    if source_id is None:
        raise HTTPException(status_code=404, detail=f"Source node not found: {source!r}")
    target_id = graph_path_mod.resolve_node(graph, target)
    if target_id is None:
        raise HTTPException(status_code=404, detail=f"Target node not found: {target!r}")

    return {"path": graph_path_mod.shortest_path(graph, source_id, target_id)}


# ---------------------------------------------------------------------------
# Obsidian export
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/export")
async def export_vault(wiki_id: int, _user: dict = Depends(get_current_user)):
    wiki = await db.get_wiki(wiki_id)
    articles = await db.get_all_articles(wiki_id)
    if not articles:
        raise HTTPException(status_code=404, detail="No articles to export.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for article in articles:
            filename = article["title"].replace("/", "-") + ".md"
            zf.writestr(filename, article["content"])
    buf.seek(0)

    safe_name = (wiki["name"] if wiki else "wiki").replace(" ", "-")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={safe_name}-vault.zip"},
    )


# ---------------------------------------------------------------------------
# Critic agent
# ---------------------------------------------------------------------------

@app.post("/api/wikis/{wiki_id}/critic")
async def start_critic(wiki_id: int, _user: dict = Depends(get_current_user)):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    job_id = await db.create_job(wiki_id)
    asyncio.create_task(_run_critic_task(wiki_id, job_id))
    return {"job_id": job_id}


async def _run_critic_task(wiki_id: int, job_id: int):
    try:
        await _critic_pipeline.run_critic(wiki_id, job_id)
    except Exception as e:
        await db.append_job_event(job_id, {"type": "error", "message": str(e)})
        await db.update_job_status(job_id, "error", str(e))


# ---------------------------------------------------------------------------
# Brainstorm agent (read-only; open-source model via Ollama)
# ---------------------------------------------------------------------------

class BrainstormRequest(BaseModel):
    mode: str  # "stability" | "conflicts" | "ideas"


@app.post("/api/wikis/{wiki_id}/brainstorm")
async def start_brainstorm(wiki_id: int, req: BrainstormRequest, _user: dict = Depends(get_current_user)):
    if req.mode not in _brainstorm_pipeline.MODES:
        raise HTTPException(status_code=400, detail="Invalid mode.")
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    job_id = await db.create_job(wiki_id)
    asyncio.create_task(_run_brainstorm_task(wiki_id, job_id, req.mode))
    return {"job_id": job_id}


async def _run_brainstorm_task(wiki_id: int, job_id: int, mode: str):
    try:
        await _brainstorm_pipeline.run_brainstorm(wiki_id, job_id, mode)
    except Exception as e:
        await db.append_job_event(job_id, {"type": "error", "message": str(e)})
        await db.update_job_status(job_id, "error", str(e))


# ---------------------------------------------------------------------------
# Gmail connection (Phase 1 — OAuth, read-only)
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/gmail/auth-url")
async def gmail_auth_url(wiki_id: int, _user: dict = Depends(get_current_user)):
    if not gmail_client.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Gmail is not configured — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    if not await db.get_wiki(wiki_id):
        raise HTTPException(status_code=404, detail="Wiki not found.")
    state = auth.create_state_token({"purpose": "gmail_oauth", "wiki_id": wiki_id})
    return {"url": gmail_client.auth_url(state)}


@app.get("/api/gmail/oauth/callback")
async def gmail_oauth_callback(code: str = QParam(None), state: str = QParam(None), error: str = QParam(None)):
    """Public endpoint — Google redirects the user's browser here."""
    from html import escape as _esc

    def _page(message: str) -> HTMLResponse:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<p>{message}</p><p>You can close this tab and return to Wikimania.</p>"
            f"<script>setTimeout(()=>window.close(), 2500)</script></body></html>"
        )

    if error:
        return _page(f"Gmail connection failed: {_esc(error)}")
    if not code or not state:
        return _page("Gmail connection failed: missing code or state.")
    try:
        payload = auth.decode_state_token(state)
        if payload.get("purpose") != "gmail_oauth":
            raise ValueError("wrong state purpose")
        wiki_id = int(payload["wiki_id"])
    except (ValueError, KeyError, TypeError) as e:
        return _page(f"Gmail connection failed: invalid state ({_esc(str(e))}).")

    try:
        tokens = await gmail_client.exchange_code(code)
        profile = await gmail_client.get_profile_with_token(tokens["access_token"])
        await db.upsert_gmail_account(
            wiki_id=wiki_id,
            email=profile["emailAddress"],
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expiry=tokens["expiry"],
        )
    except gmail_client.GmailError as e:
        return _page(f"Gmail connection failed: {_esc(str(e))}")

    return _page(f"Gmail account <strong>{_esc(profile['emailAddress'])}</strong> connected.")


@app.get("/api/wikis/{wiki_id}/gmail/status")
async def gmail_status(wiki_id: int, _user: dict = Depends(get_current_user)):
    account = await db.get_gmail_account(wiki_id)
    if not account:
        return {"connected": False, "configured": gmail_client.is_configured()}
    return {
        "connected": True,
        "configured": True,
        "email": account["email"],
        "label_name": account["label_name"],
        "sync_enabled": account["sync_enabled"],
        "sync_interval": gmail_sync.SYNC_INTERVAL,
        "ingest_alias": account["email"].replace("@", f"{email_ingest.INGEST_SUFFIX}@"),
        "last_sync_at": account["last_sync_at"],
        "last_error": account["last_error"],
    }


class GmailSettingsRequest(BaseModel):
    label_name: str | None = None
    sync_enabled: bool | None = None


@app.patch("/api/wikis/{wiki_id}/gmail/settings")
async def gmail_settings(wiki_id: int, req: GmailSettingsRequest, _user: dict = Depends(get_current_user)):
    account = await db.update_gmail_settings(wiki_id, req.label_name, req.sync_enabled)
    if not account:
        raise HTTPException(status_code=404, detail="No Gmail account connected to this wiki.")
    return {"label_name": account["label_name"], "sync_enabled": account["sync_enabled"]}


@app.delete("/api/wikis/{wiki_id}/gmail")
async def gmail_disconnect(wiki_id: int, _user: dict = Depends(get_current_user)):
    if not await db.delete_gmail_account(wiki_id):
        raise HTTPException(status_code=404, detail="No Gmail account connected to this wiki.")
    return {"message": "Gmail account disconnected."}


async def _require_gmail_account(wiki_id: int) -> dict:
    account = await db.get_gmail_account(wiki_id)
    if not account:
        raise HTTPException(status_code=400, detail="No Gmail account connected to this wiki.")
    return account


# ---------------------------------------------------------------------------
# Gmail browse + manual import (Phase 1)
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/gmail/threads")
async def gmail_list_threads(
    wiki_id: int,
    q: str = QParam(""),
    page_token: str = QParam(None),
    _user: dict = Depends(get_current_user),
):
    account = await _require_gmail_account(wiki_id)
    try:
        data = await gmail_client.list_threads(account, q=q, max_results=15, page_token=page_token)
    except gmail_client.GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))

    imported = {t["thread_id"]: t for t in await db.list_email_threads(wiki_id)}
    threads = []
    for t in data.get("threads", []):
        try:
            meta = await gmail_client.get_thread(account, t["id"], fmt="metadata")
            msgs = meta.get("messages", [])
            headers = {
                h["name"].lower(): h.get("value", "")
                for h in (msgs[0].get("payload", {}).get("headers", []) if msgs else [])
            }
            threads.append({
                "thread_id": t["id"],
                "subject": headers.get("subject", "(no subject)"),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
                "message_count": len(msgs),
                "snippet": t.get("snippet", ""),
                "imported": t["id"] in imported,
                "article_id": imported.get(t["id"], {}).get("article_id"),
            })
        except gmail_client.GmailError:
            continue
    return {"threads": threads, "next_page_token": data.get("nextPageToken")}


@app.post("/api/wikis/{wiki_id}/gmail/threads/{thread_id}/import")
async def gmail_import_thread(wiki_id: int, thread_id: str, _user: dict = Depends(get_current_user)):
    account = await _require_gmail_account(wiki_id)
    if not await db.get_wiki(wiki_id):
        raise HTTPException(status_code=404, detail="Wiki not found.")
    try:
        return await email_ingest.ingest_thread(wiki_id, account, thread_id, trigger="manual")
    except gmail_client.GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/wikis/{wiki_id}/gmail/sync")
async def gmail_sync_now(wiki_id: int, _user: dict = Depends(get_current_user)):
    account = await _require_gmail_account(wiki_id)
    try:
        stats = await gmail_sync.sync_account(account)
    except gmail_client.GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"stats": stats}


# ---------------------------------------------------------------------------
# Email ingestion status, search, metrics (Phases 2, 4, 5)
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/email/status")
async def email_status(wiki_id: int, _user: dict = Depends(get_current_user)):
    return {
        "log": await db.get_sync_log(wiki_id, limit=50),
        "threads": await db.list_email_threads(wiki_id),
    }


@app.get("/api/wikis/{wiki_id}/email/metrics")
async def email_metrics(wiki_id: int, _user: dict = Depends(get_current_user)):
    return {"metrics": await db.get_email_metrics(wiki_id)}


@app.get("/api/wikis/{wiki_id}/email/search")
async def email_search(
    wiki_id: int,
    q: str = QParam(None),
    sender: str = QParam(None),
    after: str = QParam(None),
    before: str = QParam(None),
    has_attachment: bool = QParam(None),
    topic: str = QParam(None),
    _user: dict = Depends(get_current_user),
):
    try:
        results = await db.search_email_threads(
            wiki_id, q=q, sender=sender, after=after, before=before,
            has_attachment=has_attachment, topic=topic,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Search failed: {e}")
    return {"results": results}


# ---------------------------------------------------------------------------
# Hygiene & privacy (Phase 5)
# ---------------------------------------------------------------------------

class DenylistRequest(BaseModel):
    pattern: str


@app.get("/api/wikis/{wiki_id}/email/denylist")
async def get_denylist(wiki_id: int, _user: dict = Depends(get_current_user)):
    return {"denylist": await db.list_denylist(wiki_id)}


@app.post("/api/wikis/{wiki_id}/email/denylist", status_code=201)
async def add_denylist_entry(wiki_id: int, req: DenylistRequest, _user: dict = Depends(get_current_user)):
    if not req.pattern.strip():
        raise HTTPException(status_code=400, detail="Pattern is required.")
    return await db.add_denylist(wiki_id, req.pattern)


@app.delete("/api/wikis/{wiki_id}/email/denylist/{entry_id}")
async def delete_denylist_entry(wiki_id: int, entry_id: int, _user: dict = Depends(get_current_user)):
    if not await db.remove_denylist(wiki_id, entry_id):
        raise HTTPException(status_code=404, detail="Denylist entry not found.")
    return {"message": "Denylist entry removed."}


class PurgeSenderRequest(BaseModel):
    sender: str
    add_to_denylist: bool = True


@app.post("/api/wikis/{wiki_id}/email/purge-sender")
async def purge_sender(wiki_id: int, req: PurgeSenderRequest, _user: dict = Depends(get_current_user)):
    if not req.sender.strip():
        raise HTTPException(status_code=400, detail="Sender is required.")
    return await email_ingest.purge_sender(wiki_id, req.sender.strip(), req.add_to_denylist)


@app.delete("/api/wikis/{wiki_id}/articles/{article_id}")
async def delete_article(wiki_id: int, article_id: int, _user: dict = Depends(get_current_user)):
    """Delete a wiki page. Email-derived pages also remove their source
    documents, attachment pages, and orphaned extracted entities (Phase 5)."""
    article = await db.get_article_by_id(article_id)
    if not article or article["wiki_id"] != wiki_id:
        raise HTTPException(status_code=404, detail="Article not found.")
    if article["kind"] in ("email", "newsletter_issue") or article.get("source_doc_id"):
        result = await email_ingest.delete_email_page(wiki_id, article_id)
        return {"message": f"'{article['title']}' deleted.", **result}
    await db.delete_article_by_id(article_id)
    return {"message": f"'{article['title']}' deleted.", "pages_deleted": 1, "entities_removed": 0}


# ---------------------------------------------------------------------------
# Reset wiki content (keep wiki record)
# ---------------------------------------------------------------------------

@app.delete("/api/wikis/{wiki_id}/content")
async def reset_wiki_content(wiki_id: int, _user: dict = Depends(get_current_user)):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    result = await db.reset_wiki_content(wiki_id)
    return {"message": f"{result['articles_deleted']} articles deleted.", **result}
