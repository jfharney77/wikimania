# Spec Progression: Growing Wiki with Gmail Ingestion

Goal: Extend the existing document-fed wiki so email becomes a first-class document source — threads, newsletters, and attachments flow in, get organized, and enrich the knowledge graph.

Each phase should be fully working and testable before moving on.

---

## Phase 0 — Baseline & Ingestion Contract
**Objective:** Make the wiki's ingestion pluggable before touching Gmail.

- Document how the wiki currently ingests a document: input format(s), how pages are created, how topics/links are derived.
- Define an `IngestItem` contract: `{ source, source_id, title, body_text, attachments[], author, date, metadata{} }`. All existing ingestion goes through this contract.
- Idempotency rule: ingesting the same `source + source_id` twice is a no-op or an update, never a duplicate page.

**Acceptance:** Existing document upload still works, now routed through `IngestItem`; re-ingesting a document updates rather than duplicates.

---

## Phase 1 — Manual Email Import (read-only Gmail)
**Objective:** Prove the email→wiki mapping on hand-picked messages.

- OAuth with `gmail.readonly`.
- UI: search/browse recent inbox threads inside the app; user selects a thread and clicks "Import to wiki."
- Thread→IngestItem mapping: subject → title candidate; concatenated messages (quoted-reply noise stripped) → body; participants → authors; attachments extracted (start with .pdf, .docx, .txt, .md) and ingested as linked child documents.
- HTML email handling: convert to clean text/markdown; strip signatures, disclaimers, tracking images (best-effort heuristics, keep it simple).

**Acceptance:** Importing a real multi-message thread produces one coherent wiki page with readable body, correct participants, and its attachments as linked pages. Importing it again does not duplicate.

---

## Phase 2 — Automatic Ingestion via Label
**Objective:** Hands-free flow: label an email → it appears in the wiki.

- Poll (or use Gmail history API) for messages carrying a configurable label, default `wiki`.
- Auto-ingest labeled threads through the Phase 1 mapping; track processed history IDs for incremental, idempotent sync.
- A thread that grows after import (new replies) updates its existing wiki page, appending the new messages with a changelog note.
- Status view: recently ingested items, skipped items, errors.

**Acceptance:** Labeling a thread in Gmail makes it appear in the wiki within one sync cycle; replying to that thread later updates the same page.

---

## Phase 3 — Topic Clustering & Entity Linking
**Objective:** Email content enriches the wiki's structure, not just its page count.

- Topic assignment: ingested emails are clustered/assigned to existing wiki topics; new topics proposed when nothing fits (respect however the wiki currently does this — extend, don't fork).
- Entity extraction: people (from headers *and* body mentions), organizations, projects. Each person gets/updates a person page: name, email address, threads they appear in.
- Cross-linking: wiki pages mention-link to person pages; person pages list their threads; topic pages list their emails alongside ordinary documents.
- Newsletter detection: messages with `List-Unsubscribe` headers get grouped under a per-newsletter page (issues as children) instead of one page per issue polluting topics.

**Acceptance:** After ingesting ~20 varied threads, the wiki shows sensible topic groupings, person pages with correct thread lists, and newsletters rolled up under their own pages.

---

## Phase 4 — Forward-to-Wiki & Query
**Objective:** Lower friction to zero and make the corpus useful.

- Dedicated ingestion address pattern: mail forwarded to the connected account with `+wiki` sub-address (e.g. `me+wiki@gmail.com`) is auto-ingested regardless of label. Parse the *forwarded* content as the document, the forwarder's note as an annotation.
- Search across email-derived pages with filters: sender, date range, has-attachment, topic.
- "Ask the wiki" (if the wiki already has or wants a Q&A layer): answers cite the source email/thread with a deep link back to Gmail (`https://mail.google.com/mail/u/0/#all/<message-id>` style).

**Acceptance:** Forwarding an article email to the `+wiki` address creates a page within one cycle; searching by the article's topic finds it; the page links back to the original Gmail message.

---

## Phase 5 — Hygiene, Privacy & Hardening
- Redaction rules: configurable patterns (phone numbers, one-time codes, addresses) scrubbed before storage; per-sender "never ingest" denylist.
- Retention/undo: deleting a wiki page derived from email also removes extracted entities that have no other references; a full "purge this sender" action.
- Rate limiting, token refresh, backoff; sync survives restarts (resume from persisted history ID).
- Metrics: items ingested/day, dedupe hits, cluster quality spot-check list.

**Acceptance:** A week of labeled/forwarded email ingests unattended; purging a sender verifiably removes their content and orphaned entities.
