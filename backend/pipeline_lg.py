"""
LangGraph-based wiki generation pipeline.

Exposes the same interface as pipeline.py (generate_wiki, resume_wiki,
answer_query) so main.py can swap between implementations via USE_LANGGRAPH.

Shared utilities (prompts, helpers, LLM wrappers) are imported from pipeline.py
so there is no duplication.
"""

import asyncio
import json
import os
from asyncio import Queue
from typing import Any, Optional, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, StateGraph

import db
import llm
from pipeline import (
    BATCH_DELAY,
    _call_one,
    _create_stubs,
    _fill,
    _parse_wikilinks,
    _rebuild_graph,
    EXTRACT_CONCEPTS_PROMPT,
    answer_query,   # noqa: F401  (re-exported so callers can import from here)
    resume_wiki,    # noqa: F401
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class WikiState(TypedDict):
    wiki_id: int
    job_id: int
    doc_id: int
    content: str
    parallel_writes: int
    queue: Any          # asyncio.Queue — not serialised; fine without checkpointing
    concepts: list
    created: int
    updated: int
    stubs: int
    paused: bool
    error: Optional[str]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def node_extract_concepts(state: WikiState) -> dict:
    queue = state["queue"]
    wiki_id = state["wiki_id"]

    await queue.put({"type": "phase", "phase": 1, "message": "Extracting concepts from document..."})

    existing_titles = await db.list_article_titles(wiki_id)
    titles_preview = ", ".join(existing_titles[:50]) or "none yet"
    prompt = _fill(EXTRACT_CONCEPTS_PROMPT, existing_titles=titles_preview, content=state["content"][:8000])
    raw = await llm.call_fast(prompt)
    concepts = llm.parse_json_array(raw)

    if concepts:
        await queue.put({"type": "concepts", "titles": concepts, "count": len(concepts)})

    return {"concepts": concepts}


async def node_write_articles(state: WikiState) -> dict:
    wiki_id = state["wiki_id"]
    job_id = state["job_id"]
    doc_id = state["doc_id"]
    content = state["content"]
    queue = state["queue"]
    concepts = state["concepts"]
    parallel_writes = state["parallel_writes"]
    created = state.get("created", 0)
    updated = state.get("updated", 0)

    await queue.put({"type": "phase", "phase": 2, "message": f"Writing {len(concepts)} wiki articles..."})

    all_titles = await db.list_article_titles(wiki_id)
    related = ", ".join(all_titles[:80])
    total = len(concepts)
    n_done = 0
    delay = BATCH_DELAY if parallel_writes == 1 else 2

    for batch_start in range(0, total, parallel_writes):
        if batch_start > 0:
            await asyncio.sleep(delay)

        batch = concepts[batch_start:batch_start + parallel_writes]
        results = await asyncio.gather(
            *[_call_one(wiki_id, title, content, related) for title in batch],
            return_exceptions=True,
        )

        for j, result in enumerate(results):
            title = batch[j]

            if isinstance(result, HTTPException) and result.status_code == 429:
                remaining = concepts[batch_start + j:]
                await db.save_paused_state(job_id, json.dumps({
                    "remaining_concepts": remaining,
                    "created": created,
                    "updated": updated,
                    "doc_id": doc_id,
                    "parallel_writes": parallel_writes,
                }))
                await db.set_document_status(doc_id, "paused")
                await queue.put({
                    "type": "paused",
                    "remaining": len(remaining),
                    "created": created,
                    "updated": updated,
                    "message": f"Rate limit reached — {created} new, {updated} expanded, {len(remaining)} article(s) remaining.",
                })
                return {"paused": True, "created": created, "updated": updated}

            if isinstance(result, Exception):
                raise result

            article_id, was_created = await db.upsert_article(wiki_id, title, result)
            await db.replace_article_links(article_id, _parse_wikilinks(result))

            if was_created:
                created += 1
            else:
                updated += 1
            n_done += 1

            await queue.put({
                "type": "article",
                "title": title,
                "status": "created" if was_created else "updated",
                "n": n_done,
                "total": total,
            })

    return {"paused": False, "created": created, "updated": updated}


async def node_create_stubs(state: WikiState) -> dict:
    stubs = await _create_stubs(state["wiki_id"], state["queue"])
    return {"stubs": stubs}


async def node_rebuild_graph(state: WikiState) -> dict:
    queue = state["queue"]
    await queue.put({"type": "phase", "phase": 3, "message": "Rebuilding knowledge graph..."})
    await _rebuild_graph(state["wiki_id"])
    await queue.put({"type": "graph_done", "message": "Knowledge graph updated."})
    return {}


async def node_finalize(state: WikiState) -> dict:
    job_id = state["job_id"]
    doc_id = state["doc_id"]
    queue = state["queue"]
    created = state.get("created", 0)
    updated = state.get("updated", 0)
    stubs = state.get("stubs", 0)

    if not state.get("concepts"):
        await queue.put({"type": "warning", "message": "No new concepts found in this document."})
        msg = "No new concepts found."
    else:
        msg = f"Wiki updated — {created} new, {updated} expanded, {stubs} stubs."

    await db.update_job_status(job_id, "done")
    await db.set_document_status(doc_id, "done")
    await queue.put({
        "type": "done",
        "articles_created": created,
        "articles_updated": updated,
        "stubs_created": stubs,
        "message": msg,
    })
    return {}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_after_extract(state: WikiState) -> str:
    return "finalize" if not state.get("concepts") else "write_articles"


def route_after_write(state: WikiState) -> str:
    return END if state.get("paused") else "create_stubs"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(WikiState)

    g.add_node("extract_concepts", node_extract_concepts)
    g.add_node("write_articles", node_write_articles)
    g.add_node("create_stubs", node_create_stubs)
    g.add_node("rebuild_graph", node_rebuild_graph)
    g.add_node("finalize", node_finalize)

    g.set_entry_point("extract_concepts")

    g.add_conditional_edges("extract_concepts", route_after_extract, {
        "write_articles": "write_articles",
        "finalize": "finalize",
    })
    g.add_conditional_edges("write_articles", route_after_write, {
        "create_stubs": "create_stubs",
        END: END,
    })
    g.add_edge("create_stubs", "rebuild_graph")
    g.add_edge("rebuild_graph", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


_graph = _build_graph()


# ---------------------------------------------------------------------------
# Public interface (mirrors pipeline.py)
# ---------------------------------------------------------------------------

async def generate_wiki(wiki_id: int, job_id: int, doc_id: int, content: str, queue: Queue, parallel_writes: int = 1):
    try:
        await db.update_job_status(job_id, "running")
        await _graph.ainvoke({
            "wiki_id": wiki_id,
            "job_id": job_id,
            "doc_id": doc_id,
            "content": content,
            "queue": queue,
            "parallel_writes": parallel_writes,
            "concepts": [],
            "created": 0,
            "updated": 0,
            "stubs": 0,
            "paused": False,
            "error": None,
        })
    except Exception as exc:
        err = str(exc)
        await db.update_job_status(job_id, "error", error=err)
        await db.set_document_status(doc_id, "error")
        await queue.put({"type": "error", "message": err})
