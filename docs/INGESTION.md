# Wiki Ingestion

This documents how the wiki ingests documents (spec-growing-wiki.md, Phase 0)
and how the email source plugs into the same path (Phases 1–5).

## How ingestion worked before the IngestItem contract

- **Input format:** a single `.md` file uploaded via `POST /api/wikis/{id}/documents/upload`.
- **Storage:** the raw markdown was inserted into `source_documents` (one new row per upload — re-uploading the same file duplicated it).
- **Page creation:** an async pipeline job (`pipeline.generate_wiki`) ran two LLM phases:
  1. *Concept extraction* (`MODEL_FAST`) — the document text plus the list of existing article titles produce a JSON array of concept titles not already covered.
  2. *Article writing* (`MODEL_REASONING`) — each concept becomes a new `wiki_articles` row, or expands an existing article with the new source material.
- **Topics/links:** articles are markdown with `[[wikilink]]` syntax. Links are parsed into `article_links`; dangling links become stub articles; graphify rebuilds the knowledge graph from articles + links after every job.

## The IngestItem contract (Phase 0)

All ingestion now goes through `backend/ingest.py`:

```
IngestItem {
  source        # 'upload' | 'gmail' | 'gmail:attachment' | ...
  source_id     # stable id within the source (filename, Gmail thread id, ...)
  title
  body_text
  attachments[] # {filename, mime_type, text}
  author
  date
  metadata{}
}
```

`ingest_item(wiki_id, item)`:

1. Upserts `source_documents` keyed on **(wiki_id, source, source_id)** — the
   idempotency rule. Same key + same content → `unchanged` (no-op); same key +
   new content → `updated` (the existing row is updated, never duplicated).
   Enforced by a unique index (`source_documents_source_key`).
2. Stores each attachment as a child `source_document`
   (`source='{source}:attachment'`, `source_id='{source_id}/{filename}'`,
   `parent_doc_id` set).
3. Runs the existing LLM topic pipeline over `body_text` (unless suppressed,
   e.g. for newsletter issues), which writes/expands topic articles exactly as
   before — email content enriches the same topics ordinary documents do.

The file-upload endpoint is routed through this contract with
`source='upload'`, `source_id=filename`.

## Email ingestion (Phases 1–5)

- `gmail_client.py` — OAuth (`gmail.readonly`) + REST wrappers with token
  refresh and exponential backoff.
- `email_clean.py` — pure heuristics: HTML→text (tracking images dropped),
  quoted-reply/signature/disclaimer stripping, redaction patterns, forwarded-
  message parsing, subject/address parsing.
- `email_ingest.py` — thread → `IngestItem` mapping and orchestration:
  - subject → title (Re:/Fwd: stripped); concatenated cleaned messages → body;
    participants → authors; `.pdf/.docx/.txt/.md` attachments → child pages.
  - A deterministic wiki page (kind `email`) renders the thread with a
    Gmail deep link, participants (wikilinked to person pages), attachments,
    per-message sections, and a changelog that records later thread growth.
  - Entities (people from headers + LLM body mentions, organizations,
    projects) get deterministic pages regenerated from the DB every sync.
  - Newsletters (`List-Unsubscribe` header) roll up under a
    `Newsletter: <sender>` page and skip the topic pipeline.
  - Mail delivered to the `+wiki` sub-address is ingested regardless of label;
    the forwarded content is the document, the forwarder's note an annotation.
- `gmail_sync.py` — background poller (label + alias queries). Per-thread
  message ids persist in `email_threads`, so sync is incremental, idempotent,
  and resumes after restarts. Results land in `email_sync_log`
  (status view + metrics).
- Hygiene: redaction before storage, per-sender denylist, purge-sender and
  page deletion that also remove orphaned entities.

## Article kinds

| kind | created by |
|------|------------|
| `article` | LLM topic pipeline |
| `email` / `newsletter_issue` | deterministic email page render |
| `newsletter` | per-newsletter rollup |
| `person` / `organization` / `project` | entity pages |
| `attachment` | extracted attachment text |
