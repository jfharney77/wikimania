import asyncio
import re
import tempfile
import os
from asyncio import Queue

import db
import llm


def _fill(template: str, **kwargs) -> str:
    """String-substitute without .format() so { } in user content don't break things."""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result


EXTRACT_CONCEPTS_PROMPT = """\
You are analyzing a document to identify key concepts for a wiki knowledge base.

Read the following document and list all concepts, topics, entities, processes, or ideas that deserve their own wiki article.

Existing wiki articles (avoid duplicates — only return concepts NOT already well-covered):
{existing_titles}

Document:
{content}

Return ONLY a JSON array of concept titles. Each title should be:
- In title case (e.g., "Machine Learning", "Gradient Descent")
- Concise (typically 1-4 words)
- Specific enough to be a meaningful standalone article

Do NOT include:
- Overly generic words (e.g., "Engineering", "Science", "Type")
- Media format labels (e.g., "Type Video", "Type Audio", "PDF")
- Document structure artifacts (section headers, UI labels, nav items)
- Concepts already covered by the existing articles listed above

Return ONLY valid JSON — no explanation, no markdown fences:
["Concept One", "Concept Two"]"""

WRITE_ARTICLE_PROMPT = """\
You are building a wiki knowledge base in the style of Wikipedia. Write an encyclopedic article for the concept below.

Use [[Article Title]] wikilink syntax to cross-reference related concepts. Only link to concepts from the provided list.

Related wiki concepts (link to these where relevant):
{related_titles}

Source material:
{source_content}

Write the wiki article for: {title}

Requirements:
- Encyclopedic, factual tone
- Use ## headings to organize sections where appropriate
- Cross-reference related concepts with [[wikilinks]]
- Start directly with article content — no preamble"""

EXPAND_ARTICLE_PROMPT = """\
You are updating an existing wiki article with new information from a source document.

Related wiki concepts (use [[wikilinks]] where relevant):
{related_titles}

New source material:
{source_content}

Existing article for "{title}":
{existing_content}

Rewrite the complete updated article, incorporating new information while preserving existing content and structure. Add new [[wikilinks]] as appropriate. Start directly with article content."""

QUERY_PROMPT = """\
You are a wiki assistant. Answer the user's question using only the wiki articles provided below.
Cite the articles you draw from using [[Article Title]] notation.
If the wiki does not contain enough information, say so clearly.

Wiki articles:
{articles}

Question: {question}"""

STUB_CONTENT = """\
*This article was automatically created as a stub because other wiki articles link here. Upload more documents to expand it.*
"""


def _parse_wikilinks(content: str) -> list[str]:
    return list(set(re.findall(r"\[\[([^\]]+)\]\]", content)))


async def generate_wiki(wiki_id: int, job_id: int, doc_id: int, content: str, queue: Queue):
    try:
        await db.update_job_status(job_id, "running")

        # Phase 1 — concept extraction
        await queue.put({"type": "phase", "phase": 1, "message": "Extracting concepts from document..."})
        existing_titles = await db.list_article_titles(wiki_id)
        titles_preview = ", ".join(existing_titles[:50]) or "none yet"
        prompt = _fill(
            EXTRACT_CONCEPTS_PROMPT,
            existing_titles=titles_preview,
            content=content[:8000],
        )
        raw = await llm.call_fast(prompt)
        concepts = llm.parse_json_array(raw)

        if not concepts:
            await queue.put({"type": "warning", "message": "No new concepts found in this document."})
            await queue.put({"type": "done", "articles_created": 0, "articles_updated": 0, "stubs_created": 0, "message": "No new concepts found."})
            await db.update_job_status(job_id, "done")
            await db.set_document_status(doc_id, "done")
            return

        await queue.put({"type": "concepts", "titles": concepts, "count": len(concepts)})

        # Phase 2 — write / expand articles
        await queue.put({"type": "phase", "phase": 2, "message": f"Writing {len(concepts)} wiki articles..."})
        all_titles = await db.list_article_titles(wiki_id)
        related = ", ".join(all_titles[:80])

        created = 0
        updated = 0

        for i, title in enumerate(concepts):
            existing = await db.get_article_content(wiki_id, title)

            if existing:
                system = "You are a wiki author expanding an existing article."
                user = _fill(
                    EXPAND_ARTICLE_PROMPT,
                    related_titles=related,
                    source_content=content[:6000],
                    title=title,
                    existing_content=existing,
                )
            else:
                system = "You are a wiki author writing a new encyclopedic article."
                user = _fill(
                    WRITE_ARTICLE_PROMPT,
                    related_titles=related,
                    source_content=content[:6000],
                    title=title,
                )

            article_content = await llm.call_reasoning(system, user)
            article_content = re.sub(r"<think>.*?</think>", "", article_content, flags=re.DOTALL).strip()

            article_id, was_created = await db.upsert_article(wiki_id, title, article_content)

            wikilinks = _parse_wikilinks(article_content)
            await db.replace_article_links(article_id, wikilinks)

            if was_created:
                created += 1
            else:
                updated += 1

            await queue.put({
                "type": "article",
                "title": title,
                "status": "created" if was_created else "updated",
                "n": i + 1,
                "total": len(concepts),
            })

        # Phase 2b — stubs for dangling wikilinks
        stubs = await _create_stubs(wiki_id, queue)

        # Phase 3 — rebuild knowledge graph
        await queue.put({"type": "phase", "phase": 3, "message": "Rebuilding knowledge graph..."})
        await _rebuild_graph(wiki_id)
        await queue.put({"type": "graph_done", "message": "Knowledge graph updated."})

        await db.update_job_status(job_id, "done")
        await db.set_document_status(doc_id, "done")
        await queue.put({
            "type": "done",
            "articles_created": created,
            "articles_updated": updated,
            "stubs_created": stubs,
            "message": f"Wiki updated — {created} new, {updated} expanded, {stubs} stubs.",
        })

    except Exception as exc:
        err = str(exc)
        await db.update_job_status(job_id, "error", error=err)
        await db.set_document_status(doc_id, "error")
        await queue.put({"type": "error", "message": err})


async def _create_stubs(wiki_id: int, queue: Queue) -> int:
    all_links = await db.get_all_article_links(wiki_id)
    existing_titles = set(await db.list_article_titles(wiki_id))

    dangling = {link["to_title"] for link in all_links} - existing_titles
    if not dangling:
        return 0

    count = 0
    for title in sorted(dangling):
        await db.upsert_article(wiki_id, title, STUB_CONTENT)
        count += 1
        await queue.put({"type": "stub", "title": title})

    return count


async def _rebuild_graph(wiki_id: int):
    articles = await db.get_all_articles(wiki_id)
    links = await db.get_all_article_links(wiki_id)

    article_index = {a["title"]: a["id"] for a in articles}

    nodes = [
        {
            "id": f"article_{a['id']}",
            "label": a["title"],
            "file_type": "document",
            "source_file": f"{a['title']}.md",
            "source_url": None,
            "captured_at": None,
            "author": None,
            "contributor": None,
        }
        for a in articles
    ]

    edges = []
    for link in links:
        target_id = article_index.get(link["to_title"])
        if target_id:
            edges.append({
                "source": f"article_{link['from_id']}",
                "target": f"article_{target_id}",
                "relation": "references",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": None,
                "source_location": None,
                "weight": 1.0,
            })

    extraction = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}

    graph_json = await asyncio.get_event_loop().run_in_executor(
        None, _graphify_build, extraction
    )
    await db.save_graph_snapshot(wiki_id, graph_json)


def _graphify_build(extraction: dict) -> str:
    from graphify.build import build_from_json
    from graphify.cluster import cluster
    from graphify.export import to_json

    G = build_from_json(extraction)
    communities = cluster(G)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        to_json(G, communities, tmp_path)
        with open(tmp_path) as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


async def answer_query(wiki_id: int, question: str) -> dict:
    results = await db.search_articles(wiki_id, question)
    if not results:
        return {"answer": "No relevant wiki articles found for your question.", "sources": []}

    articles_text = "\n\n---\n\n".join(
        f"## {r['title']}\n{r['content'][:1500]}" for r in results
    )
    answer = await llm.call_reasoning(
        "You are a wiki assistant. Answer questions using only the provided wiki articles.",
        _fill(QUERY_PROMPT, articles=articles_text, question=question),
    )
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

    return {
        "answer": answer,
        "sources": [{"id": r["id"], "title": r["title"]} for r in results],
    }
