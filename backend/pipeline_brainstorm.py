"""
Brainstorm agent — a read-only LangGraph agent that suggests ideas for a wiki.

Three modes:
  * stability  — actions to improve the wiki's structural health / coverage balance
  * conflicts  — proposals to resolve overlapping or contradictory pages
  * ideas      — net-new ideas implied by the wiki (esp. a project under construction)

Unlike pipeline_critic, this NEVER writes to wiki_articles — it only streams
suggestions as `idea` events. It runs on an open-source model via llm.call_brainstorm
(Ollama by default), independent of the app's PROVIDER.

Progress is persisted through db.append_job_event, so the SSE stream survives
restarts and works across worker processes (see CRITIQUE_SPEC_2.md).
"""

from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

import db
import llm
from pipeline import STUB_CONTENT
from pipeline_critic import _parse_json_list

MODES = ("stability", "conflicts", "ideas")

# Keep the prompt payload bounded so a small local model stays responsive.
MAX_CONTEXT_CHARS = 12000
CONFLICT_BODY_CHARS = 700

_STUB_MARKER = STUB_CONTENT.strip()[:40]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class BrainstormState(TypedDict):
    wiki_id: int
    job_id: int
    mode: str
    articles: list
    inventory: str          # compact, mode-agnostic description of the wiki
    ideas: list
    error: Optional[str]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_OUTPUT_CONTRACT = """\
Return ONLY a JSON array of objects, each shaped exactly like:
{"title": "Short suggestion headline", "rationale": "1-2 sentences on why", "related_articles": ["Article A", "Article B"], "priority": "high" | "medium" | "low"}
Return [] if you have no good suggestions.
No explanation, no markdown fences, no <think> blocks."""

STABILITY_PROMPT = """\
You are a wiki maintainer. Below is an inventory of a wiki's articles with their
sizes, link counts, and flags for stubs and orphans (articles with no links in or out).

{inventory}

Suggest concrete, actionable work items to improve the wiki's STRUCTURAL HEALTH and
coverage balance: expand thin stubs, connect orphans, fill obvious gaps, even out
uneven depth, and add missing cross-links. Do NOT rewrite articles — propose work items.

""" + _OUTPUT_CONTRACT

CONFLICTS_PROMPT = """\
You are a wiki editor. Below are articles from a wiki (titles and excerpts).

{inventory}

Identify pages that CONFLICT, overlap, compete, or contradict each other. For each,
propose how to resolve it (merge, split, disambiguate, or reconcile the facts). Only
flag genuine conflicts or real overlap — ignore articles that are merely related.
Put the affected article titles in "related_articles".

""" + _OUTPUT_CONTRACT

IDEAS_PROMPT = """\
This wiki documents a project that is being built. Below is its current content.

{inventory}

Brainstorm NEW ideas suggested by the wiki: missing features, logical next steps,
components that are implied but not yet documented, and open questions worth resolving.
Be specific and grounded in what the wiki already covers.

""" + _OUTPUT_CONTRACT

_MODE_PROMPTS = {
    "stability": STABILITY_PROMPT,
    "conflicts": CONFLICTS_PROMPT,
    "ideas": IDEAS_PROMPT,
}

_MODE_SYSTEM = {
    "stability": "You are a meticulous wiki maintainer proposing structural improvements.",
    "conflicts": "You are a wiki editor finding and resolving conflicting pages.",
    "ideas": "You are a creative product strategist brainstorming from a project wiki.",
}


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _is_stub(content: str) -> bool:
    return content.strip().startswith(_STUB_MARKER)


def _build_inventory(mode: str, articles: list, links: list) -> str:
    """Compact description of the wiki, tailored to the mode, capped at MAX_CONTEXT_CHARS."""
    out_counts: dict[int, int] = {}
    in_counts: dict[str, int] = {}
    for link in links:
        out_counts[link["from_id"]] = out_counts.get(link["from_id"], 0) + 1
        in_counts[link["to_title"]] = in_counts.get(link["to_title"], 0) + 1

    lines: list[str] = []
    for a in articles:
        outgoing = out_counts.get(a["id"], 0)
        incoming = in_counts.get(a["title"], 0)
        if mode == "conflicts":
            body = a["content"].strip().replace("\n", " ")[:CONFLICT_BODY_CHARS]
            lines.append(f"## {a['title']}\n{body}")
        else:
            flags = []
            if _is_stub(a["content"]):
                flags.append("STUB")
            if outgoing == 0 and incoming == 0:
                flags.append("ORPHAN")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"- {a['title']} ({len(a['content'])} chars, "
                f"{outgoing} links out, {incoming} links in){flag_str}"
            )

    sep = "\n\n---\n\n" if mode == "conflicts" else "\n"
    inventory = sep.join(lines)
    if len(inventory) > MAX_CONTEXT_CHARS:
        inventory = inventory[:MAX_CONTEXT_CHARS] + "\n…(truncated)"
    return inventory


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def node_load_context(state: BrainstormState) -> dict:
    wiki_id = state["wiki_id"]
    job_id = state["job_id"]
    mode = state["mode"]

    await db.append_job_event(job_id, {"type": "phase", "message": "Reading the wiki..."})

    articles = await db.get_all_articles(wiki_id)
    links = await db.get_all_article_links(wiki_id)

    if not articles:
        return {"articles": [], "inventory": ""}

    inventory = _build_inventory(mode, articles, links)
    await db.append_job_event(
        job_id,
        {"type": "phase", "message": f"Brainstorming over {len(articles)} article(s)..."},
    )
    return {"articles": articles, "inventory": inventory}


async def node_brainstorm(state: BrainstormState) -> dict:
    job_id = state["job_id"]
    mode = state["mode"]

    if not state["articles"]:
        return {"ideas": []}

    prompt = _MODE_PROMPTS[mode].replace("{inventory}", state["inventory"])
    raw = await llm.call_brainstorm(_MODE_SYSTEM[mode], prompt)
    suggestions = _parse_json_list(raw)

    ideas: list = []
    for s in suggestions:
        if not isinstance(s, dict) or not s.get("title"):
            continue
        idea = {
            "title": str(s.get("title", "")).strip(),
            "rationale": str(s.get("rationale", "")).strip(),
            "related_articles": s.get("related_articles") or [],
            "priority": str(s.get("priority", "medium")).strip().lower(),
        }
        if idea["priority"] not in ("high", "medium", "low"):
            idea["priority"] = "medium"
        ideas.append(idea)
        await db.append_job_event(job_id, {"type": "idea", **idea})

    return {"ideas": ideas}


async def node_finalize(state: BrainstormState) -> dict:
    job_id = state["job_id"]
    mode = state["mode"]
    count = len(state.get("ideas", []))

    if not state["articles"]:
        msg = "Nothing to brainstorm yet — this wiki has no articles."
    elif count == 0:
        msg = "No suggestions this time."
    else:
        msg = f"Brainstormed {count} idea(s)."

    await db.update_job_status(job_id, "done")
    await db.append_job_event(job_id, {"type": "done", "mode": mode, "count": count, "message": msg})
    return {}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(BrainstormState)
    g.add_node("load_context", node_load_context)
    g.add_node("brainstorm", node_brainstorm)
    g.add_node("finalize", node_finalize)

    g.set_entry_point("load_context")
    g.add_edge("load_context", "brainstorm")
    g.add_edge("brainstorm", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_graph = _build_graph()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def run_brainstorm(wiki_id: int, job_id: int, mode: str):
    if mode not in MODES:
        raise ValueError(f"Invalid brainstorm mode: {mode}")
    try:
        await db.update_job_status(job_id, "running")
        await _graph.ainvoke({
            "wiki_id": wiki_id,
            "job_id": job_id,
            "mode": mode,
            "articles": [],
            "inventory": "",
            "ideas": [],
            "error": None,
        })
    except Exception as exc:
        err = str(exc)
        await db.update_job_status(job_id, "error", error=err)
        await db.append_job_event(job_id, {"type": "error", "message": err})
