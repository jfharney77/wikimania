"""Phase 3 — entity extraction and person/organization/project pages.

People come from email headers (deterministic) and body mentions (LLM,
best-effort). Each entity gets a deterministic wiki page (kind 'person',
'organization' or 'project') listing the threads it appears in. Pages are
regenerated from the database on every sync so they stay correct after
purges and deletions.
"""

import json
import re

import db
import llm

ENTITY_PROMPT = """\
Extract named entities from this email text. Return ONLY valid JSON, no explanation:
{"people": ["Full Name", ...], "organizations": ["Org Name", ...], "projects": ["Project Name", ...]}

Rules:
- people: real humans mentioned by name in the text (not email addresses)
- organizations: companies, institutions, teams
- projects: named projects, products, initiatives
- Omit generic words. Empty arrays are fine.

Email text:
"""

_KIND_LABEL = {"person": "Person", "organization": "Organization", "project": "Project"}


async def extract_body_entities(text: str) -> dict:
    """LLM extraction of people/orgs/projects from body text. Fails soft to {}."""
    try:
        raw = await llm.call_fast(ENTITY_PROMPT + text[:4000])
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {}
        data = json.loads(m.group())
        return {
            k: [str(v).strip() for v in data.get(k, []) if str(v).strip()][:20]
            for k in ("people", "organizations", "projects")
        }
    except Exception:
        return {}


def _entity_page_title(kind: str, name: str) -> str:
    # Bracket-free so the title is a valid [[wikilink]] target.
    return name.replace("[", "(").replace("]", ")").strip()


async def _resolve_page_title(wiki_id: int, kind: str, name: str) -> str:
    """Avoid clobbering an ordinary topic article that shares the entity's name."""
    title = _entity_page_title(kind, name)
    existing = await db.get_article_by_title(wiki_id, title)
    if existing and existing["kind"] not in (kind, "stub"):
        title = f"{title} ({_KIND_LABEL.get(kind, kind.title())})"
    return title


def render_entity_page(entity: dict, threads: list[dict]) -> str:
    kind = entity["kind"]
    lines = [f"*{_KIND_LABEL.get(kind, kind.title())} page generated automatically from email ingestion.*", ""]
    if entity.get("email"):
        lines.append(f"**Email:** {entity['email']}")
        lines.append("")
    if threads:
        lines.append("## Email Threads")
        for t in threads:
            when = t["last_message_at"].date().isoformat() if t.get("last_message_at") else ""
            link = f" ([Gmail]({t['gmail_link']}))" if t.get("gmail_link") else ""
            title = t.get("article_title") or t.get("subject") or "Untitled"
            lines.append(f"- [[{title}]] {when}{link}".rstrip())
    else:
        lines.append("*No email threads currently reference this entity.*")
    return "\n".join(lines) + "\n"


async def refresh_entity_page(wiki_id: int, entity: dict) -> str:
    """(Re)build an entity's wiki page from current mentions. Returns page title."""
    threads = await db.get_entity_threads(entity["id"])
    title = await _resolve_page_title(wiki_id, entity["kind"], entity["name"])
    content = render_entity_page(entity, threads)
    article_id, _ = await db.upsert_article(wiki_id, title, content, kind=entity["kind"])
    await db.set_entity_article(entity["id"], article_id)
    await db.replace_article_links(article_id, re.findall(r"\[\[([^\]]+)\]\]", content))
    return title


async def sync_entities(
    wiki_id: int,
    doc_id: int,
    header_people: list[tuple[str, str]],
    body_text: str,
    use_llm: bool = True,
) -> dict[str, list[str]]:
    """Upsert entities for one ingested email and refresh their pages.

    ``header_people`` is [(name, email), ...] from From/To/Cc headers.
    Returns {"person": [page titles], "organization": [...], "project": [...]}
    so the email page can wikilink to them.
    """
    page_titles: dict[str, list[str]] = {"person": [], "organization": [], "project": []}
    seen: set[tuple[str, str]] = set()

    async def _record(kind: str, name: str, email: str | None = None):
        key = (kind, (email or name).lower())
        if key in seen or not name:
            return
        seen.add(key)
        entity = await db.upsert_entity(wiki_id, kind, name, email)
        await db.add_entity_mention(entity["id"], doc_id)
        title = await refresh_entity_page(wiki_id, entity)
        page_titles[kind].append(title)

    for name, email in header_people:
        display = name.strip() or email.split("@")[0]
        await _record("person", display, email)

    if use_llm and body_text.strip():
        extracted = await extract_body_entities(body_text)
        for name in extracted.get("people", []):
            await _record("person", name)
        for name in extracted.get("organizations", []):
            await _record("organization", name)
        for name in extracted.get("projects", []):
            await _record("project", name)

    return page_titles


async def cleanup_orphans(wiki_id: int) -> int:
    """Delete entities with no remaining mentions and their pages (Phase 5)."""
    orphans = await db.delete_orphan_entities(wiki_id)
    for orphan in orphans:
        if orphan.get("article_id"):
            await db.delete_article_by_id(orphan["article_id"])
    return len(orphans)


def link_mentions(body: str, entity_titles: list[str]) -> str:
    """Wikilink the first plain-text occurrence of each entity name in a body."""
    for title in sorted(set(entity_titles), key=len, reverse=True):
        base = re.sub(r"\s*\((Person|Organization|Project)\)$", "", title)
        if len(base) < 3:
            continue
        pattern = re.compile(r"(?<!\[)\b" + re.escape(base) + r"\b(?!\])")
        body, _ = pattern.subn(f"[[{title}|{base}]]" if title != base else f"[[{base}]]", body, count=1)
    return body
