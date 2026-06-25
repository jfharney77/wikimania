# Wikimania Critique & Spec

## Criticism

**The pipeline silently discards the majority of every document beyond ~6–8 KB.**

In `pipeline.py`, concept extraction receives `content[:8000]` (line 203) and every article-writing LLM call receives `content[:6000]` (lines 110, 113). `pipeline_lg.py` imports the same `_call_one` and adds its own `content[:8000]` cap (line 66). The full document is stored intact in `source_documents.content`, so the data is available — but the pipeline never uses it past those byte offsets.

The sample documents happen to fall near but under these limits (6 904 B and 7 702 B), masking the problem during development. Any realistic upload — a research paper, a meeting transcript, a technical spec, a book chapter — routinely runs 20–200 KB. For a 50 KB document, ~94 % of the content is silently ignored: concepts from later sections are never extracted, and every article is written from the same truncated preamble regardless of which concept it covers. There is no warning, no truncation marker, and no partial-result signal to the user.

This is the most significant weakness because it degrades the core capability for all real-world input without any visible indication of failure.

---

## Spec

### Goal

Process documents of arbitrary length by splitting them into chunks, running concept extraction over all chunks, and supplying each article-writing call with only the chunks most relevant to that article's concept. The user experience and API surface stay the same; the quality of results improves proportionally with document length.

### Approach

**Phase 1 — chunked concept extraction**

1. Add a `chunk_document(content: str, chunk_size: int = 6000, overlap: int = 500) -> list[str]` utility that splits the document into overlapping windows (character-based, split on word boundary).
2. In `generate_wiki` (and the LangGraph equivalent), loop over chunks and call `llm.call_fast` once per chunk with the existing `EXTRACT_CONCEPTS_PROMPT`. Deduplicate the union of all returned concept lists before proceeding. The existing de-duplication against `existing_titles` from the DB still applies.
3. Emit a `phase` event that reports how many chunks were extracted (e.g. `"Extracting concepts from 4 chunks…"`).

**Phase 2 — per-concept source retrieval**

1. Add a `find_relevant_chunks(concept: str, chunks: list[str], top_k: int = 2) -> str` utility that scores each chunk by case-insensitive keyword overlap with the concept title and returns the top-k chunks joined as the source text.
2. Replace `content[:6000]` in `_call_one` with the result of `find_relevant_chunks(title, chunks)`. The returned text will be roughly `top_k × chunk_size` characters, which for `top_k=2, chunk_size=6000` stays well within context limits of all supported providers.
3. Pass the pre-chunked `list[str]` through `_write_articles` / `node_write_articles` instead of the raw `content` string.

**Specific file changes**

| File | Change |
|------|--------|
| `backend/pipeline.py` | Add `chunk_document` and `find_relevant_chunks`; update `generate_wiki`, `resume_wiki`, `_write_articles`, `_call_one` signatures and calls |
| `backend/pipeline_lg.py` | Update `WikiState` to carry `chunks: list[str]`; update `node_extract_concepts` and `node_write_articles`; import `_call_one` update is automatic |

No database schema changes are required. No API changes are required. The `PARALLEL_WRITES` and `BATCH_DELAY` knobs continue to work unchanged.

### Acceptance criteria

1. Uploading `reinforcement_learning.md` (7 702 B) produces the same number of concepts as before (regression check).
2. Uploading a synthetic document of 40 000 characters containing 5 distinct topic sections produces concepts from all 5 sections (not only the first).
3. An article whose concept title appears only in section 4 of a long document includes content from that section, not only from the document's opening paragraphs.
4. The SSE event stream for a multi-chunk document emits `phase` messages indicating chunk count; no new event types are required.
5. No change to API request/response shapes or the database schema.
