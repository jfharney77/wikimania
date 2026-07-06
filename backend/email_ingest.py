"""Phases 1-4 — Gmail thread → wiki mapping.

A Gmail thread is parsed into the IngestItem contract (Phase 0), stored
idempotently, rendered as a deterministic wiki page (kind 'email'), and fed
through the existing LLM topic pipeline so email content enriches the same
topic articles ordinary documents do. Attachments become linked child pages,
participants become person pages, and newsletters roll up under a
per-newsletter page.
"""

import base64
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from html import unescape

import db
import email_clean
import entities
import gmail_client
import ingest
import pipeline

ATTACHMENT_EXTS = {".pdf", ".docx", ".txt", ".md"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
INGEST_SUFFIX = os.getenv("GMAIL_INGEST_SUFFIX", "+wiki")
USE_ENTITY_LLM = os.getenv("EMAIL_ENTITY_LLM", "true").lower() == "true"


def gmail_thread_link(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


# ---------------------------------------------------------------------------
# Thread parsing (pure — takes Gmail API JSON, no network)
# ---------------------------------------------------------------------------

def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h.get("value", "") for h in msg.get("payload", {}).get("headers", [])}


def _walk_parts(payload: dict, out: list[dict]):
    if not payload:
        return
    parts = payload.get("parts")
    if parts:
        for p in parts:
            _walk_parts(p, out)
    else:
        out.append(payload)


def _decode_part(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except Exception:
        return ""


def parse_message(msg: dict) -> dict:
    """Parse one Gmail message into headers, raw/cleaned body, attachment refs."""
    headers = _headers(msg)
    from_name, from_email = email_clean.parse_address(headers.get("from", ""))

    parts: list[dict] = []
    _walk_parts(msg.get("payload", {}), parts)

    plain, html = "", ""
    attachments: list[dict] = []
    for part in parts:
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")
        if filename and part.get("body", {}).get("attachmentId"):
            ext = os.path.splitext(filename)[1].lower()
            if ext in ATTACHMENT_EXTS and part["body"].get("size", 0) <= MAX_ATTACHMENT_BYTES:
                attachments.append({
                    "message_id": msg["id"],
                    "attachment_id": part["body"]["attachmentId"],
                    "filename": filename,
                    "mime_type": mime,
                })
        elif mime == "text/plain" and not plain:
            plain = _decode_part(part)
        elif mime == "text/html" and not html:
            html = _decode_part(part)

    raw_text = plain or (email_clean.html_to_text(html) if html else "")

    try:
        date = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        date = None

    recipients = (
        email_clean.split_address_list(headers.get("to", ""))
        + email_clean.split_address_list(headers.get("cc", ""))
    )

    return {
        "id": msg["id"],
        "from_name": from_name,
        "from_email": from_email,
        "recipients": recipients,
        "subject": headers.get("subject", ""),
        "date": date,
        "raw_text": raw_text,
        "body": email_clean.clean_message_body(raw_text),
        "list_unsubscribe": headers.get("list-unsubscribe", ""),
        "delivered_to": headers.get("delivered-to", "") or headers.get("x-original-to", ""),
        "attachments": attachments,
    }


def parse_thread(thread: dict, account_email: str = "") -> dict:
    """Parse a full Gmail thread (Phase 1 thread→IngestItem mapping input)."""
    messages = [parse_message(m) for m in thread.get("messages", [])]
    messages.sort(key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc))

    participants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in messages:
        for name, email in [(m["from_name"], m["from_email"])] + m["recipients"]:
            if email and email not in seen and not email_clean.is_plus_wiki_address(email, INGEST_SUFFIX):
                seen.add(email)
                participants.append((name, email))

    sender = ""
    for m in messages:
        if m["from_email"] and m["from_email"] != (account_email or "").lower():
            sender = m["from_email"]
            break
    if not sender and messages:
        sender = messages[0]["from_email"]

    plus_wiki = any(
        email_clean.is_plus_wiki_address(addr, INGEST_SUFFIX)
        for m in messages
        for addr in [m["delivered_to"]] + [e for _, e in m["recipients"]]
    )

    subject = next((m["subject"] for m in messages if m["subject"]), "")
    dates = [m["date"] for m in messages if m["date"]]

    return {
        "thread_id": thread.get("id", ""),
        "subject": subject,
        "messages": messages,
        "participants": participants,
        "sender": sender,
        "is_newsletter": any(m["list_unsubscribe"] for m in messages),
        "has_attachments": any(m["attachments"] for m in messages),
        "plus_wiki": plus_wiki,
        "gmail_link": gmail_thread_link(thread.get("id", "")),
        "last_message_at": max(dates) if dates else None,
    }


# ---------------------------------------------------------------------------
# IngestItem construction
# ---------------------------------------------------------------------------

def build_ingest_item(parsed: dict, attachments: list[ingest.IngestAttachment],
                      redaction=None) -> tuple[ingest.IngestItem, dict]:
    """Map a parsed thread to the IngestItem contract.

    Returns (item, extra) where extra carries render-time info:
    {"annotation": forwarder's note or "", "sections": [per-message dicts]}.
    """
    messages = parsed["messages"]
    annotation = ""
    title = email_clean.clean_subject(parsed["subject"])
    author = ", ".join(
        (name or email) for name, email in parsed["participants"][:6]
    ) or None
    sections: list[dict] = []

    forwarded = email_clean.parse_forwarded(messages[0]["raw_text"]) if len(messages) == 1 else None
    if forwarded and (parsed["plus_wiki"] or forwarded["headers"]):
        # Phase 4: the forwarded content is the document; the forwarder's note
        # is an annotation.
        annotation = email_clean.redact(forwarded["note"], redaction)
        fwd_subject = forwarded["headers"].get("subject")
        if fwd_subject:
            title = email_clean.clean_subject(fwd_subject)
        fwd_from = forwarded["headers"].get("from", "")
        name, email = email_clean.parse_address(fwd_from)
        body = email_clean.strip_disclaimers(email_clean.strip_signature(forwarded["body"]))
        body = email_clean.redact(body, redaction)
        sections.append({
            "author": name or email or messages[0]["from_name"] or messages[0]["from_email"],
            "email": email,
            "date": forwarded["headers"].get("date", ""),
            "body": body,
        })
        if name or email:
            author = name or email
    else:
        for m in messages:
            sections.append({
                "author": m["from_name"] or m["from_email"],
                "email": m["from_email"],
                "date": m["date"].strftime("%Y-%m-%d %H:%M UTC") if m["date"] else "",
                "body": email_clean.redact(m["body"], redaction),
            })

    body_text = "\n\n".join(
        f"### {s['author']} — {s['date']}\n\n{s['body']}".rstrip()
        for s in sections
    )

    item = ingest.IngestItem(
        source="gmail",
        source_id=parsed["thread_id"],
        title=title,
        body_text=body_text,
        attachments=attachments,
        author=author,
        date=parsed["last_message_at"],
        metadata={
            "gmail_link": parsed["gmail_link"],
            "thread_id": parsed["thread_id"],
            "message_ids": [m["id"] for m in messages],
            "participants": [list(p) for p in parsed["participants"]],
            "is_newsletter": parsed["is_newsletter"],
            "annotation": annotation,
        },
    )
    return item, {"annotation": annotation, "sections": sections}


# ---------------------------------------------------------------------------
# Attachment text extraction
# ---------------------------------------------------------------------------

def extract_attachment_text(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md"):
        return data.decode("utf-8", "replace").strip()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception:
            return ""
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", "replace")
            xml = xml.replace("</w:p>", "\n")
            return unescape(re.sub(r"<[^>]+>", "", xml)).strip()
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------------------
# Page rendering (deterministic — no LLM)
# ---------------------------------------------------------------------------

def _changelog_from_article(content: str | None) -> list[str]:
    if not content:
        return []
    m = re.search(r"## Changelog\n(.*?)(?:\n## |\Z)", content, re.DOTALL)
    if not m:
        return []
    return [line for line in m.group(1).strip().splitlines() if line.startswith("- ")]


def render_email_page(
    parsed: dict,
    sections: list[dict],
    annotation: str,
    attachment_titles: list[str],
    person_titles: list[str],
    changelog: list[str],
    person_by_email: dict[str, str] | None = None,
) -> str:
    person_by_email = person_by_email or {}
    lines = [f"*Email thread imported from Gmail — [open in Gmail]({parsed['gmail_link']}).*", ""]

    part_bits = []
    for name, email in parsed["participants"]:
        page = person_by_email.get(email)
        display = name or email
        part_bits.append(f"[[{page}|{display}]]" if page and page != display else (f"[[{display}]]" if page else display))
    if part_bits:
        lines.append(f"**Participants:** {', '.join(part_bits)}")
    if parsed["last_message_at"]:
        lines.append(f"**Last message:** {parsed['last_message_at'].strftime('%Y-%m-%d %H:%M UTC')}")
    if attachment_titles:
        lines.append("**Attachments:** " + ", ".join(f"[[{t}]]" for t in attachment_titles))
    lines.append("")

    if annotation:
        lines.append(f"> **Forwarder's note:** {annotation}")
        lines.append("")

    lines.append("## Messages")
    lines.append("")
    participant_pages = {
        person_by_email.get(email) or (name or email)
        for name, email in parsed["participants"]
    }
    other_titles = [t for t in person_titles if t not in participant_pages]
    for s in sections:
        header = f"### {s['author']} — {s['date']}".rstrip(" —")
        body = entities.link_mentions(s["body"], other_titles)
        lines.append(header)
        lines.append("")
        lines.append(body)
        lines.append("")

    if changelog:
        lines.append("## Changelog")
        lines.extend(changelog)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_newsletter_page(name: str, issues: list[dict]) -> str:
    lines = [
        f"*Newsletter page generated automatically. Issues are grouped here instead of polluting topic pages.*",
        "",
        "## Issues",
    ]
    for issue in issues:
        when = issue["last_message_at"].date().isoformat() if issue.get("last_message_at") else ""
        title = issue.get("article_title") or issue.get("subject") or "Untitled issue"
        lines.append(f"- [[{title}]] {when}".rstrip())
    return "\n".join(lines) + "\n"


def render_attachment_page(filename: str, parent_title: str, gmail_link: str, text: str) -> str:
    body = text.strip() or "*No text could be extracted from this attachment.*"
    return (
        f"*Attachment `{filename}` from email thread [[{parent_title}]] — "
        f"[open thread in Gmail]({gmail_link}).*\n\n{body}\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def _resolve_email_page_title(wiki_id: int, title: str, existing_thread: dict | None,
                                    doc_id: int) -> str:
    if existing_thread and existing_thread.get("article_id"):
        current = await db.get_article_by_id(existing_thread["article_id"])
        if current:
            return current["title"]
    existing = await db.get_article_by_title(wiki_id, title)
    if existing and existing["kind"] not in ("email", "newsletter_issue") and existing.get("source_doc_id") != doc_id:
        return f"{title} (Email)"
    return title


async def _is_denied(wiki_id: int, sender: str) -> bool:
    sender = (sender or "").lower()
    for entry in await db.list_denylist(wiki_id):
        if entry["pattern"] in sender:
            return True
    return False


async def ingest_thread(
    wiki_id: int,
    account: dict,
    thread_id: str,
    trigger: str = "manual",
    parallel_writes: int = 1,
    use_entity_llm: bool = True,
) -> dict:
    """Fetch a Gmail thread and ingest it into the wiki. Idempotent.

    Returns {"action", "title", "article_id", "doc_id", "job_id"}.
    action ∈ ingested | updated | skipped | denied.
    """
    thread = await gmail_client.get_thread(account, thread_id)
    parsed = parse_thread(thread, account.get("email", ""))

    if await _is_denied(wiki_id, parsed["sender"]):
        await db.add_sync_log(wiki_id, thread_id, "denied", f"Sender {parsed['sender']} is on the denylist.")
        return {"action": "denied", "title": parsed["subject"]}

    existing_thread = await db.get_email_thread(wiki_id, thread_id)
    msg_ids = [m["id"] for m in parsed["messages"]]
    known_ids = set()
    if existing_thread:
        import json as _json
        known_ids = set(_json.loads(existing_thread["message_ids"] or "[]"))
        new_ids = [i for i in msg_ids if i not in known_ids]
        if not new_ids:
            await db.add_sync_log(wiki_id, thread_id, "dedupe", "No new messages — already ingested.")
            return {
                "action": "skipped",
                "title": parsed["subject"],
                "article_id": existing_thread.get("article_id"),
                "doc_id": existing_thread.get("doc_id"),
            }

    # Fetch + extract attachments (Phase 1: .pdf .docx .txt .md)
    redaction = email_clean.load_redaction_patterns()
    attachments: list[ingest.IngestAttachment] = []
    for m in parsed["messages"]:
        for att in m["attachments"]:
            try:
                data = await gmail_client.get_attachment(account, att["message_id"], att["attachment_id"])
                text = email_clean.redact(extract_attachment_text(att["filename"], data), redaction)
            except Exception as e:
                text = f"(attachment could not be fetched: {e})"
            attachments.append(ingest.IngestAttachment(
                filename=att["filename"], mime_type=att["mime_type"], text=text,
                # Same filename can appear in several messages of one thread —
                # key attachment identity on (message_id, filename).
                source_key=f"{att['message_id']}/{att['filename']}",
            ))

    item, extra = build_ingest_item(parsed, attachments, redaction)

    # Newsletters skip the topic pipeline (Phase 3 — no per-issue topic pollution).
    result = await ingest.ingest_item(
        wiki_id, item,
        parallel_writes=parallel_writes,
        run_topic_pipeline=not parsed["is_newsletter"],
    )

    # Entities: header people + LLM body mentions → person/org/project pages.
    entity_pages = await entities.sync_entities(
        wiki_id, result.doc_id, parsed["participants"], item.body_text,
        use_llm=use_entity_llm and USE_ENTITY_LLM and not parsed["is_newsletter"],
    )
    all_entity_titles = entity_pages["person"] + entity_pages["organization"] + entity_pages["project"]
    person_by_email = {}
    for ent in await db.get_entities_for_doc(result.doc_id):
        if ent["kind"] == "person" and ent.get("email") and ent.get("article_id"):
            article = await db.get_article_by_id(ent["article_id"])
            if article:
                person_by_email[ent["email"].lower()] = article["title"]

    # Email page (deterministic) with changelog.
    page_title = await _resolve_email_page_title(wiki_id, item.title, existing_thread, result.doc_id)
    old_content = None
    if existing_thread and existing_thread.get("article_id"):
        old_article = await db.get_article_by_id(existing_thread["article_id"])
        old_content = old_article["content"] if old_article else None
    changelog = _changelog_from_article(old_content)
    today = datetime.now(timezone.utc).date().isoformat()
    if existing_thread:
        new_count = len([i for i in msg_ids if i not in known_ids])
        changelog.append(f"- {today}: appended {new_count} new message(s) from thread sync.")
    else:
        changelog.append(f"- {today}: imported thread with {len(msg_ids)} message(s) ({trigger}).")

    # Attachment child pages first, so the email page can link to them.
    attachment_titles: list[str] = []
    for att, att_doc_id in zip(item.attachments, result.attachment_doc_ids):
        att_title = att.filename.replace("[", "(").replace("]", ")")
        existing_att = await db.get_article_by_title(wiki_id, att_title)
        if existing_att and existing_att.get("source_doc_id") not in (None, att_doc_id):
            att_title = f"{att_title} ({thread_id[:6]})"
        att_content = render_attachment_page(att.filename, page_title, parsed["gmail_link"], att.text)
        att_article_id, _ = await db.upsert_article(
            wiki_id, att_title, att_content, kind="attachment", source_doc_id=att_doc_id
        )
        await db.replace_article_links(att_article_id, re.findall(r"\[\[([^\]|]+)", att_content))
        attachment_titles.append(att_title)

    content = render_email_page(
        parsed, extra["sections"], extra["annotation"],
        attachment_titles, all_entity_titles, changelog, person_by_email,
    )
    article_id, _ = await db.upsert_article(
        wiki_id, page_title, content,
        kind="newsletter_issue" if parsed["is_newsletter"] else "email",
        source_doc_id=result.doc_id,
    )
    await db.replace_article_links(article_id, re.findall(r"\[\[([^\]|]+)", content))

    await db.upsert_email_thread(
        wiki_id=wiki_id,
        thread_id=thread_id,
        subject=parsed["subject"],
        sender=parsed["sender"],
        participants=[e for _, e in parsed["participants"]],
        doc_id=result.doc_id,
        article_id=article_id,
        message_ids=msg_ids,
        is_newsletter=parsed["is_newsletter"],
        has_attachments=parsed["has_attachments"],
        gmail_link=parsed["gmail_link"],
        last_message_at=parsed["last_message_at"],
    )

    # Refresh entity pages now that the thread row exists, so their
    # "Email Threads" sections include this thread.
    for ent in await db.get_entities_for_doc(result.doc_id):
        await entities.refresh_entity_page(wiki_id, ent)

    # Newsletter rollup page (issues as children).
    if parsed["is_newsletter"]:
        await _update_newsletter_page(wiki_id, parsed)

    action = "updated" if existing_thread else "ingested"
    await db.add_sync_log(wiki_id, thread_id, action, f"{page_title} ({trigger})")

    # The topic pipeline rebuilds the graph when it finishes; for
    # deterministic-only ingests (newsletters) rebuild here.
    if result.job_id is None:
        try:
            await pipeline._rebuild_graph(wiki_id)
        except Exception:
            pass

    return {
        "action": action,
        "title": page_title,
        "article_id": article_id,
        "doc_id": result.doc_id,
        "job_id": result.job_id,
    }


def _newsletter_name(parsed: dict) -> str:
    for name, email in parsed["participants"]:
        if email == parsed["sender"]:
            if name:
                return name
            break
    if parsed["sender"] and "@" in parsed["sender"]:
        return parsed["sender"].split("@")[0]
    return parsed["sender"] or "Unknown"


async def _update_newsletter_page(wiki_id: int, parsed: dict):
    name = _newsletter_name(parsed)
    title = f"Newsletter: {name}".replace("[", "(").replace("]", ")")
    issues = await db.list_email_threads_by_sender(wiki_id, parsed["sender"])
    issue_rows = []
    for t in [t for t in issues if t["is_newsletter"]]:
        article_title = None
        if t.get("article_id"):
            a = await db.get_article_by_id(t["article_id"])
            article_title = a["title"] if a else None
        issue_rows.append({
            "article_title": article_title,
            "subject": t["subject"],
            "last_message_at": t["last_message_at"],
        })
    content = render_newsletter_page(name, issue_rows)
    article_id, _ = await db.upsert_article(wiki_id, title, content, kind="newsletter")
    await db.replace_article_links(article_id, re.findall(r"\[\[([^\]|]+)", content))


# ---------------------------------------------------------------------------
# Retention / purge (Phase 5)
# ---------------------------------------------------------------------------

async def _remove_newsletter_issue(wiki_id: int, issue_title: str) -> int:
    """Drop a deleted issue from its newsletter rollup page; delete the rollup
    entirely when no issues remain. Returns extra pages deleted (0 or 1)."""
    for a in await db.list_articles(wiki_id):
        if a["kind"] != "newsletter":
            continue
        page = await db.get_article_by_id(a["id"])
        if not page or f"[[{issue_title}]]" not in page["content"]:
            continue
        lines = [l for l in page["content"].splitlines() if f"[[{issue_title}]]" not in l]
        if not any(l.startswith("- ") for l in lines):
            await db.delete_article_by_id(a["id"])
            return 1
        content = "\n".join(lines)
        await db.update_article_content(a["id"], content)
        await db.replace_article_links(a["id"], re.findall(r"\[\[([^\]|]+)", content))
        return 0
    return 0


async def delete_email_page(wiki_id: int, article_id: int) -> dict:
    """Delete an email-derived page, its documents, its attachment pages, and
    any extracted entities that have no other references."""
    thread = await db.get_email_thread_by_article(article_id)
    article = await db.get_article_by_id(article_id)
    deleted_pages = 0

    doc_id = (thread or {}).get("doc_id") or (article or {}).get("source_doc_id")
    if doc_id:
        await db.remove_mentions_for_doc(doc_id)

    if article:
        await db.delete_article_by_id(article_id)
        deleted_pages += 1
        if thread and thread["is_newsletter"]:
            deleted_pages += await _remove_newsletter_issue(wiki_id, article["title"])

    if doc_id:
        # Delete attachment pages tied to child docs, then the doc tree.
        for child in await db.list_child_documents(doc_id):
            att_article = await db.get_article_by_source_doc(wiki_id, child["id"])
            if att_article:
                await db.delete_article_by_id(att_article["id"])
                deleted_pages += 1
        await db.delete_document(doc_id)

    if thread:
        await db.delete_email_thread(thread["id"])

    orphans = await entities.cleanup_orphans(wiki_id)

    # Refresh remaining entity pages so they stop listing the deleted thread.
    for ent in await db.list_entities(wiki_id):
        await entities.refresh_entity_page(wiki_id, ent)

    try:
        await pipeline._rebuild_graph(wiki_id)
    except Exception:
        pass

    return {"pages_deleted": deleted_pages, "entities_removed": orphans}


async def purge_sender(wiki_id: int, sender: str, add_to_denylist: bool = True) -> dict:
    """Remove every thread/page from a sender and orphaned entities (Phase 5)."""
    threads = await db.list_email_threads_by_sender(wiki_id, sender)
    pages_deleted = 0
    entities_removed = 0
    for t in threads:
        if t.get("article_id"):
            res = await delete_email_page(wiki_id, t["article_id"])
            pages_deleted += res["pages_deleted"]
            entities_removed += res["entities_removed"]
        else:
            if t.get("doc_id"):
                await db.remove_mentions_for_doc(t["doc_id"])
                await db.delete_document(t["doc_id"])
            await db.delete_email_thread(t["id"])
    entities_removed += await entities.cleanup_orphans(wiki_id)
    if add_to_denylist:
        await db.add_denylist(wiki_id, sender)
    await db.add_sync_log(wiki_id, None, "purged", f"Purged sender {sender}: {len(threads)} thread(s).")
    return {
        "threads_purged": len(threads),
        "pages_deleted": pages_deleted,
        "entities_removed": entities_removed,
        "denylisted": add_to_denylist,
    }
