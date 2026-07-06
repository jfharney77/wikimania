"""Phase 0 — the pluggable ingestion contract.

Every document source (file upload, Gmail thread, email attachment, forward)
is normalised into an ``IngestItem`` and passed through ``ingest_item``.
Identity is ``(source, source_id)``: re-ingesting the same item updates the
existing source document instead of creating a duplicate.

See docs/INGESTION.md for how ingestion flows into wiki pages and topics.
"""

import asyncio
import os
from datetime import datetime

from pydantic import BaseModel, Field

import db

_use_langgraph = os.getenv("USE_LANGGRAPH", "false").lower() == "true"
if _use_langgraph:
    import pipeline_lg as wiki_pipeline
else:
    import pipeline as wiki_pipeline


class IngestAttachment(BaseModel):
    filename: str
    mime_type: str = "application/octet-stream"
    text: str = ""  # extracted text ("" if extraction failed/unsupported)
    source_key: str | None = None  # identity within the parent item (defaults to filename)


class IngestItem(BaseModel):
    source: str                     # 'upload' | 'gmail' | 'gmail_attachment' | ...
    source_id: str                  # stable id within the source (filename, thread id, ...)
    title: str
    body_text: str
    attachments: list[IngestAttachment] = Field(default_factory=list)
    author: str | None = None
    date: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class IngestResult(BaseModel):
    doc_id: int
    action: str                     # 'created' | 'updated' | 'unchanged'
    job_id: int | None = None       # topic-generation job (None when skipped)
    attachment_doc_ids: list[int] = Field(default_factory=list)


async def ingest_item(
    wiki_id: int,
    item: IngestItem,
    parallel_writes: int = 1,
    run_topic_pipeline: bool = True,
) -> IngestResult:
    """Store an IngestItem idempotently and (optionally) run topic generation.

    - The source document is upserted on (wiki_id, source, source_id).
    - If the content is unchanged, nothing else runs (dedupe no-op).
    - Attachments are stored as child source documents with their own identity
      (``{source}:attachment`` / ``{source_id}/{filename}``).
    - When ``run_topic_pipeline`` is true, the existing LLM pipeline extracts
      concepts and writes/expands topic articles from the body text.
    """
    doc_id, action = await db.upsert_source_document(
        wiki_id=wiki_id,
        source=item.source,
        source_id=item.source_id,
        filename=item.title,
        content=item.body_text,
        author=item.author,
        doc_date=item.date,
        metadata=item.metadata,
    )

    attachment_doc_ids: list[int] = []
    for att in item.attachments:
        att_doc_id, _ = await db.upsert_source_document(
            wiki_id=wiki_id,
            source=f"{item.source}:attachment",
            source_id=f"{item.source_id}/{att.source_key or att.filename}",
            filename=att.filename,
            content=att.text or f"(no text could be extracted from {att.filename})",
            author=item.author,
            doc_date=item.date,
            metadata={"mime_type": att.mime_type, "parent_source_id": item.source_id},
            parent_doc_id=doc_id,
        )
        attachment_doc_ids.append(att_doc_id)

    if action == "unchanged":
        return IngestResult(doc_id=doc_id, action=action, attachment_doc_ids=attachment_doc_ids)

    job_id = None
    if run_topic_pipeline and item.body_text.strip():
        job_id = await db.create_job(wiki_id=wiki_id, doc_id=doc_id)
        asyncio.create_task(
            wiki_pipeline.generate_wiki(
                wiki_id, job_id, doc_id, item.body_text, parallel_writes=parallel_writes
            )
        )
    else:
        await db.set_document_status(doc_id, "done")

    return IngestResult(doc_id=doc_id, action=action, job_id=job_id, attachment_doc_ids=attachment_doc_ids)
