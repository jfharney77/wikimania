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

_job_queues: dict[int, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required.")
    # Railway emits postgres:// but asyncpg requires postgresql://
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
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
# Wikis
# ---------------------------------------------------------------------------

class WikiCreate(BaseModel):
    name: str


@app.get("/api/wikis")
async def list_wikis():
    return {"wikis": await db.list_wikis()}


@app.post("/api/wikis", status_code=201)
async def create_wiki(req: WikiCreate):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Wiki name is required.")
    return await db.create_wiki(req.name.strip())


@app.delete("/api/wikis/{wiki_id}")
async def delete_wiki(wiki_id: int):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    count = await db.delete_wiki(wiki_id)
    return {"message": f"Wiki '{wiki['name']}' deleted.", "articles_deleted": count}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.post("/api/wikis/{wiki_id}/documents/upload")
async def upload_document(wiki_id: int, file: UploadFile = File(...)):
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
        wiki_pipeline.generate_wiki(wiki_id, job_id, doc_id, content, queue)
    )

    return {"job_id": job_id, "doc_id": doc_id, "filename": file.filename}


@app.get("/api/wikis/{wiki_id}/documents")
async def list_documents(wiki_id: int):
    return {"documents": await db.list_documents(wiki_id)}


# ---------------------------------------------------------------------------
# Jobs / SSE
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: int):
    async def event_generator():
        queue = _job_queues.get(job_id)
        if not queue:
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

@app.get("/api/wikis/{wiki_id}/articles")
async def list_articles(wiki_id: int):
    return {"articles": await db.list_articles(wiki_id)}


@app.get("/api/wikis/{wiki_id}/articles/{article_id}")
async def get_article(wiki_id: int, article_id: int):
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
async def query_wiki(wiki_id: int, req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    return await wiki_pipeline.answer_query(wiki_id, req.question)


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/graph")
async def get_graph(wiki_id: int):
    graph_json = await db.get_latest_graph(wiki_id)
    if not graph_json:
        return {"graph": None, "message": "No graph yet — upload a document first."}
    return {"graph": json.loads(graph_json)}


# ---------------------------------------------------------------------------
# Obsidian export
# ---------------------------------------------------------------------------

@app.get("/api/wikis/{wiki_id}/export")
async def export_vault(wiki_id: int):
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
# Reset wiki content (keep wiki record)
# ---------------------------------------------------------------------------

@app.delete("/api/wikis/{wiki_id}/content")
async def reset_wiki_content(wiki_id: int):
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    result = await db.reset_wiki_content(wiki_id)
    return {"message": f"{result['articles_deleted']} articles deleted.", **result}
