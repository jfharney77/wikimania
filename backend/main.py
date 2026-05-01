import asyncio
import json
import os
import zipfile
import io

from contextlib import asynccontextmanager
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

import db
import pipeline as wiki_pipeline

# In-memory SSE queues keyed by job_id
_job_queues: dict[int, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required.")
    await db.init_pool(database_url)
    yield
    await db.close_pool()


app = FastAPI(title="Wikimania API", lifespan=lifespan)

_cors_origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://localhost:5174"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are accepted.")

    content = (await file.read()).decode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="File is empty.")

    doc_id = await db.save_document(filename=file.filename, content=content)
    job_id = await db.create_job(doc_id=doc_id)

    queue: asyncio.Queue = asyncio.Queue()
    _job_queues[job_id] = queue

    asyncio.create_task(wiki_pipeline.generate_wiki(job_id, doc_id, content, queue))

    return {"job_id": job_id, "doc_id": doc_id, "filename": file.filename}


@app.get("/api/documents")
async def list_documents():
    return {"documents": await db.list_documents()}


# ---------------------------------------------------------------------------
# Jobs / SSE
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: int):
    async def event_generator():
        queue = _job_queues.get(job_id)
        if not queue:
            # Job already completed or unknown — return stored status
            job = await db.get_job(job_id)
            status = job["status"] if job else "unknown"
            yield f"data: {json.dumps({'type': status, 'message': f'Job {status}'})}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# ---------------------------------------------------------------------------
# Wiki articles
# ---------------------------------------------------------------------------

@app.get("/api/wiki/articles")
async def list_articles():
    return {"articles": await db.list_articles()}


@app.get("/api/wiki/articles/{article_id}")
async def get_article(article_id: int):
    article = await db.get_article_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found.")
    return article


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str


@app.post("/api/wiki/query")
async def query_wiki(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    return await wiki_pipeline.answer_query(req.question)


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

@app.get("/api/wiki/graph")
async def get_graph():
    graph_json = await db.get_latest_graph()
    if not graph_json:
        return {"graph": None, "message": "No graph yet — upload a document first."}
    return {"graph": json.loads(graph_json)}


# ---------------------------------------------------------------------------
# Obsidian export
# ---------------------------------------------------------------------------

@app.get("/api/wiki/export")
async def export_vault():
    articles = await db.get_all_articles()
    if not articles:
        raise HTTPException(status_code=404, detail="No articles to export.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for article in articles:
            filename = article["title"].replace("/", "-") + ".md"
            zf.writestr(filename, article["content"])
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=wikimania-vault.zip"},
    )
