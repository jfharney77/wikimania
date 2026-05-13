import asyncio
import json
import os
import zipfile
import io

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi import Query as QParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

_job_queues: dict[int, asyncio.Queue] = {}


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
        return auth.decode_token(t)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


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

    # Bootstrap first admin if no users exist
    first_admin = os.getenv("FIRST_ADMIN_USERNAME", "").strip()
    first_pass = os.getenv("FIRST_ADMIN_PASSWORD", "").strip()
    if first_admin and first_pass:
        if await db.count_users() == 0:
            await db.create_user(first_admin, auth.hash_password(first_pass), role="admin")
            print(f"[wikimania] Created initial admin: {first_admin}")
    elif await db.count_users() == 0:
        print("[wikimania] WARNING: No users exist and FIRST_ADMIN_USERNAME/PASSWORD are not set.")

    yield
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

    doc_id = await db.save_document(wiki_id=wiki_id, filename=file.filename, content=content)
    job_id = await db.create_job(wiki_id=wiki_id, doc_id=doc_id)

    queue: asyncio.Queue = asyncio.Queue()
    _job_queues[job_id] = queue

    asyncio.create_task(
        wiki_pipeline.generate_wiki(wiki_id, job_id, doc_id, content, queue, parallel_writes=parallel_writes)
    )

    return {"job_id": job_id, "doc_id": doc_id, "filename": file.filename}


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

        queue = _job_queues.get(job_id)
        if not queue:
            job = await db.get_job(job_id)
            status = job["status"] if job else "unknown"
            yield f"data: {json.dumps({'type': status, 'message': f'Job {status}'})}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                continue

            yield f"data: {json.dumps(event, default=str)}\n\n"

            if event.get("type") in ("done", "error"):
                _job_queues.pop(job_id, None)
                break

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

    queue: asyncio.Queue = asyncio.Queue()
    _job_queues[job_id] = queue

    asyncio.create_task(
        wiki_pipeline.resume_wiki(
            wiki_id=job["wiki_id"],
            job_id=job_id,
            doc_id=state["doc_id"],
            concepts=state["remaining_concepts"],
            content=doc["content"],
            queue=queue,
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
    if not article:
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
    queue: asyncio.Queue = asyncio.Queue()
    _job_queues[job_id] = queue
    asyncio.create_task(_run_critic_task(wiki_id, job_id, queue))
    return {"job_id": job_id}


async def _run_critic_task(wiki_id: int, job_id: int, queue: asyncio.Queue):
    try:
        await _critic_pipeline.run_critic(wiki_id, job_id, queue)
    except Exception as e:
        await queue.put({"type": "error", "message": str(e)})
        await db.update_job_status(job_id, "error", str(e))
    finally:
        await asyncio.sleep(300)
        _job_queues.pop(job_id, None)


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
