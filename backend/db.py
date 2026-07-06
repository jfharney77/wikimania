import asyncpg
import json
import os
import re

_pool: asyncpg.Pool | None = None


async def init_pool(database_url: str):
    global _pool
    is_local = any(h in database_url for h in ("localhost", "127.0.0.1"))
    ssl = None if is_local else "require"
    _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10, ssl=ssl)
    async with _pool.acquire() as conn:
        # Core tables
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS wikis (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS source_documents (
                id          SERIAL PRIMARY KEY,
                wiki_id     INT REFERENCES wikis(id) ON DELETE CASCADE,
                filename    TEXT NOT NULL,
                content     TEXT NOT NULL,
                uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status      TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS wiki_articles (
                id         SERIAL PRIMARY KEY,
                wiki_id    INT REFERENCES wikis(id) ON DELETE CASCADE,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS article_links (
                from_id  INT REFERENCES wiki_articles(id) ON DELETE CASCADE,
                to_title TEXT NOT NULL,
                PRIMARY KEY (from_id, to_title)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS generation_jobs (
                id           SERIAL PRIMARY KEY,
                wiki_id      INT REFERENCES wikis(id) ON DELETE CASCADE,
                doc_id       INT REFERENCES source_documents(id),
                status       TEXT NOT NULL DEFAULT 'pending',
                progress     TEXT NOT NULL DEFAULT '',
                error        TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_snapshots (
                id         SERIAL PRIMARY KEY,
                wiki_id    INT REFERENCES wikis(id) ON DELETE CASCADE,
                graph_json TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS job_events (
                id         BIGSERIAL PRIMARY KEY,
                job_id     INT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                seq        INT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS job_events_job_id_seq_idx ON job_events (job_id, seq)"
        )

        # Migration: add paused_state to generation_jobs
        await conn.execute("""
            ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS paused_state TEXT
        """)

        # Users (auth)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # Migration: add wiki_id to tables created before this feature
        for table in ('source_documents', 'wiki_articles', 'generation_jobs', 'graph_snapshots'):
            await conn.execute(f"""
                ALTER TABLE {table} ADD COLUMN IF NOT EXISTS
                wiki_id INT REFERENCES wikis(id) ON DELETE CASCADE
            """)

        # Migrate unique constraint on wiki_articles to (wiki_id, title)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE wiki_articles DROP CONSTRAINT IF EXISTS wiki_articles_title_key;
            EXCEPTION WHEN OTHERS THEN NULL; END $$;
        """)
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'wiki_articles_wiki_id_title_key'
                ) THEN
                    ALTER TABLE wiki_articles
                    ADD CONSTRAINT wiki_articles_wiki_id_title_key UNIQUE (wiki_id, title);
                END IF;
            END $$;
        """)

        # ── IngestItem contract (Phase 0) ─────────────────────────────────────
        # Every ingested item carries (source, source_id) so re-ingesting is an
        # update, never a duplicate. Plain uploads use source='upload'.
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'")
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS source_id TEXT")
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS author TEXT")
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS doc_date TIMESTAMPTZ")
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS metadata TEXT NOT NULL DEFAULT '{}'")
        await conn.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS parent_doc_id INT REFERENCES source_documents(id) ON DELETE CASCADE")
        # Backfill identity for rows that predate the contract so re-ingesting
        # them updates instead of duplicating. If historical duplicates exist
        # for a (wiki_id, filename), only the newest row gets the identity key
        # (the unique index below would otherwise reject the backfill).
        await conn.execute("""
            UPDATE source_documents s SET source_id = s.filename
            WHERE s.source_id IS NULL AND s.source = 'upload' AND s.id = (
                SELECT max(s2.id) FROM source_documents s2
                WHERE s2.wiki_id IS NOT DISTINCT FROM s.wiki_id
                  AND s2.filename = s.filename AND s2.source_id IS NULL AND s2.source = 'upload'
            ) AND NOT EXISTS (
                SELECT 1 FROM source_documents s3
                WHERE s3.wiki_id IS NOT DISTINCT FROM s.wiki_id
                  AND s3.source = 'upload' AND s3.source_id = s.filename
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS source_documents_source_key
            ON source_documents (wiki_id, source, source_id) WHERE source_id IS NOT NULL
        """)

        # Article kinds distinguish LLM topic articles from deterministic
        # email/person/newsletter pages.
        await conn.execute("ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'article'")
        await conn.execute("ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS source_doc_id INT REFERENCES source_documents(id) ON DELETE SET NULL")

        # ── Gmail ingestion (Phases 1-5) ──────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gmail_accounts (
                id            SERIAL PRIMARY KEY,
                wiki_id       INT UNIQUE REFERENCES wikis(id) ON DELETE CASCADE,
                email         TEXT NOT NULL,
                access_token  TEXT NOT NULL,
                refresh_token TEXT,
                token_expiry  TIMESTAMPTZ,
                label_name    TEXT NOT NULL DEFAULT 'wiki',
                sync_enabled  BOOLEAN NOT NULL DEFAULT true,
                history_id    TEXT,
                last_sync_at  TIMESTAMPTZ,
                last_error    TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_threads (
                id              SERIAL PRIMARY KEY,
                wiki_id         INT REFERENCES wikis(id) ON DELETE CASCADE,
                thread_id       TEXT NOT NULL,
                subject         TEXT,
                sender          TEXT,
                participants    TEXT NOT NULL DEFAULT '[]',
                doc_id          INT REFERENCES source_documents(id) ON DELETE SET NULL,
                article_id      INT REFERENCES wiki_articles(id) ON DELETE SET NULL,
                message_ids     TEXT NOT NULL DEFAULT '[]',
                is_newsletter   BOOLEAN NOT NULL DEFAULT false,
                has_attachments BOOLEAN NOT NULL DEFAULT false,
                gmail_link      TEXT,
                last_message_at TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (wiki_id, thread_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_sync_log (
                id         SERIAL PRIMARY KEY,
                wiki_id    INT REFERENCES wikis(id) ON DELETE CASCADE,
                thread_id  TEXT,
                action     TEXT NOT NULL,
                detail     TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id         SERIAL PRIMARY KEY,
                wiki_id    INT REFERENCES wikis(id) ON DELETE CASCADE,
                kind       TEXT NOT NULL,
                name       TEXT NOT NULL,
                email      TEXT,
                article_id INT REFERENCES wiki_articles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (wiki_id, kind, name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_mentions (
                entity_id INT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                doc_id    INT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
                PRIMARY KEY (entity_id, doc_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_denylist (
                id         SERIAL PRIMARY KEY,
                wiki_id    INT REFERENCES wikis(id) ON DELETE CASCADE,
                pattern    TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (wiki_id, pattern)
            )
        """)


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialised"
    return _pool


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(username: str, password_hash: str, role: str = "user") -> dict:
    async with get_pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO users (username, password_hash, role) VALUES ($1, $2, $3) "
                "RETURNING id, username, role, created_at",
                username, password_hash, role,
            )
            return dict(row)
        except asyncpg.UniqueViolationError:
            raise ValueError(f"Username '{username}' is already taken.")


async def get_user_by_username(username: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username=$1",
            username,
        )
        return dict(row) if row else None


async def list_users() -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, role, created_at FROM users ORDER BY created_at"
        )
        return [dict(r) for r in rows]


async def update_user_role(user_id: int, role: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET role=$1 WHERE id=$2 RETURNING id, username, role, created_at",
            role, user_id,
        )
        return dict(row) if row else None


async def delete_user(user_id: int) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id=$1", user_id)
        return result == "DELETE 1"


async def count_users() -> int:
    async with get_pool().acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM users")


# ---------------------------------------------------------------------------
# Wikis
# ---------------------------------------------------------------------------

async def create_wiki(name: str) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO wikis (name) VALUES ($1) RETURNING id, name, created_at", name
        )
        return dict(row)


async def list_wikis() -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, created_at FROM wikis ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]


async def get_wiki(wiki_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, created_at FROM wikis WHERE id=$1", wiki_id
        )
        return dict(row) if row else None


async def delete_wiki(wiki_id: int) -> int:
    """Delete a wiki and all its content. Returns count of articles deleted."""
    async with get_pool().acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM wiki_articles WHERE wiki_id=$1", wiki_id
        )
        await conn.execute("DELETE FROM wikis WHERE id=$1", wiki_id)  # cascades
        return count


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

async def upsert_source_document(
    wiki_id: int,
    source: str,
    source_id: str,
    filename: str,
    content: str,
    author: str | None = None,
    doc_date=None,
    metadata: dict | None = None,
    parent_doc_id: int | None = None,
) -> tuple[int, str]:
    """Idempotent ingest: (wiki_id, source, source_id) is the identity key.

    Returns (doc_id, action) where action is 'created', 'updated' or 'unchanged'.
    """
    meta_json = json.dumps(metadata or {}, default=str)
    async with get_pool().acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, content, status FROM source_documents WHERE wiki_id=$1 AND source=$2 AND source_id=$3",
            wiki_id, source, source_id,
        )
        if existing:
            # Archived (wiki was reset) or errored docs need reprocessing even
            # when the content is byte-identical.
            if existing["content"] == content and existing["status"] not in ("archived", "error"):
                await conn.execute(
                    "UPDATE source_documents SET metadata=$1 WHERE id=$2",
                    meta_json, existing["id"],
                )
                return existing["id"], "unchanged"
            await conn.execute(
                """UPDATE source_documents
                   SET filename=$1, content=$2, author=$3, doc_date=$4, metadata=$5,
                       status='pending', uploaded_at=now()
                   WHERE id=$6""",
                filename, content, author, doc_date, meta_json, existing["id"],
            )
            return existing["id"], "updated"
        row = await conn.fetchrow(
            """INSERT INTO source_documents
               (wiki_id, source, source_id, filename, content, author, doc_date, metadata, parent_doc_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
            wiki_id, source, source_id, filename, content, author, doc_date, meta_json, parent_doc_id,
        )
        return row["id"], "created"


async def set_document_status(doc_id: int, status: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE source_documents SET status=$1 WHERE id=$2", status, doc_id
        )


async def get_document(doc_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, filename, content FROM source_documents WHERE id=$1", doc_id
        )
        return dict(row) if row else None


async def list_documents(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, filename, source, source_id, author, uploaded_at, status FROM source_documents WHERE wiki_id=$1 ORDER BY uploaded_at DESC",
            wiki_id,
        )
        return [dict(r) for r in rows]


async def list_child_documents(parent_doc_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, filename, source, source_id FROM source_documents WHERE parent_doc_id=$1",
            parent_doc_id,
        )
        return [dict(r) for r in rows]


async def delete_document(doc_id: int):
    """Delete a source document (children cascade via parent_doc_id FK)."""
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM source_documents WHERE id=$1", doc_id)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

async def create_job(wiki_id: int, doc_id: int | None = None) -> int:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO generation_jobs (wiki_id, doc_id) VALUES ($1, $2) RETURNING id",
            wiki_id, doc_id,
        )
        return row["id"]


async def save_paused_state(job_id: int, state_json: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE generation_jobs SET status='paused', paused_state=$1 WHERE id=$2",
            state_json, job_id,
        )


async def update_job_status(job_id: int, status: str, error: str | None = None):
    async with get_pool().acquire() as conn:
        if status in ("done", "error"):
            await conn.execute(
                "UPDATE generation_jobs SET status=$1, error=$2, completed_at=now() WHERE id=$3",
                status, error, job_id,
            )
        else:
            await conn.execute(
                "UPDATE generation_jobs SET status=$1, error=$2 WHERE id=$3",
                status, error, job_id,
            )


async def get_job(job_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM generation_jobs WHERE id=$1", job_id)
        return dict(row) if row else None


async def mark_orphaned_jobs() -> int:
    """Fail jobs left 'running'/'pending' by a prior server crash or restart.

    Paused jobs are intentionally left alone — they are resumable by the user.
    The 1-hour threshold avoids racing jobs that are legitimately still starting.
    Returns the number of jobs marked as errored.
    """
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            """UPDATE generation_jobs
               SET status='error',
                   error='Server restarted while job was running',
                   completed_at=now()
               WHERE status IN ('running', 'pending')
                 AND created_at < now() - interval '1 hour'"""
        )
        # result looks like "UPDATE <n>"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0


# ---------------------------------------------------------------------------
# Job events (durable SSE backing store — survives restarts, multi-worker safe)
# ---------------------------------------------------------------------------

async def append_job_event(job_id: int, event: dict) -> int:
    """Append an event to a job's durable event log. Returns the assigned seq.

    The seq is computed atomically per job inside the INSERT so concurrent
    producers (or workers) never collide.
    """
    event_json = json.dumps(event, default=str)
    async with get_pool().acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO job_events (job_id, seq, event_json)
               SELECT $1, COALESCE(MAX(seq), 0) + 1, $2
               FROM job_events WHERE job_id=$1
               RETURNING seq""",
            job_id, event_json,
        )


async def get_job_events_after(job_id: int, after_seq: int) -> list[dict]:
    """Return events for a job with seq > after_seq, in order."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT seq, event_json FROM job_events WHERE job_id=$1 AND seq>$2 ORDER BY seq",
            job_id, after_seq,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Wiki articles
# ---------------------------------------------------------------------------

async def upsert_article(
    wiki_id: int,
    title: str,
    content: str,
    kind: str = "article",
    source_doc_id: int | None = None,
) -> tuple[int, bool]:
    async with get_pool().acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM wiki_articles WHERE wiki_id=$1 AND title=$2", wiki_id, title
        )
        if existing:
            await conn.execute(
                "UPDATE wiki_articles SET content=$1, updated_at=now() WHERE id=$2",
                content, existing["id"],
            )
            return existing["id"], False
        else:
            row = await conn.fetchrow(
                "INSERT INTO wiki_articles (wiki_id, title, content, kind, source_doc_id) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                wiki_id, title, content, kind, source_doc_id,
            )
            return row["id"], True


async def update_article_content(article_id: int, content: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE wiki_articles SET content=$1, updated_at=now() WHERE id=$2",
            content, article_id,
        )


async def get_article_by_title(wiki_id: int, title: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, content, kind, source_doc_id FROM wiki_articles WHERE wiki_id=$1 AND title=$2",
            wiki_id, title,
        )
        return dict(row) if row else None


async def get_article_by_source_doc(wiki_id: int, doc_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, content, kind, source_doc_id FROM wiki_articles WHERE wiki_id=$1 AND source_doc_id=$2",
            wiki_id, doc_id,
        )
        return dict(row) if row else None


async def delete_article_by_id(article_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM wiki_articles WHERE id=$1", article_id)


async def get_article_content(wiki_id: int, title: str) -> str | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM wiki_articles WHERE wiki_id=$1 AND title=$2", wiki_id, title
        )
        return row["content"] if row else None


async def list_articles(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, kind, created_at, updated_at FROM wiki_articles WHERE wiki_id=$1 ORDER BY title",
            wiki_id,
        )
        return [dict(r) for r in rows]


async def get_article_by_id(article_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, wiki_id, title, content, kind, source_doc_id, created_at, updated_at FROM wiki_articles WHERE id=$1",
            article_id,
        )
        return dict(row) if row else None


async def delete_article_by_title(wiki_id: int, title: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM wiki_articles WHERE wiki_id=$1 AND title=$2", wiki_id, title
        )


async def get_all_articles(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content FROM wiki_articles WHERE wiki_id=$1", wiki_id
        )
        return [dict(r) for r in rows]


async def list_article_titles(wiki_id: int) -> list[str]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT title FROM wiki_articles WHERE wiki_id=$1 ORDER BY title", wiki_id
        )
        return [r["title"] for r in rows]


_STOP_WORDS = frozenset({
    'a', 'an', 'the', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
    'is', 'it', 'me', 'my', 'by', 'do', 'one', 'give', 'tell', 'please',
    'can', 'could', 'would', 'should', 'what', 'how', 'why', 'when',
    'where', 'who', 'summarize', 'explain', 'describe', 'sentence', 'about',
    'with', 'that', 'this', 'are', 'was', 'were', 'has', 'have', 'had',
    'summary', 'brief', 'short', 'long', 'simple', 'detail',
})


async def search_articles(wiki_id: int, query: str, limit: int = 8) -> list[dict]:
    words = [w for w in re.split(r'\W+', query.lower()) if len(w) > 2 and w not in _STOP_WORDS]
    if not words:
        words = [query.lower()]

    async with get_pool().acquire() as conn:
        seen: dict[int, dict] = {}
        for word in words:
            pattern = f'%{word}%'
            rows = await conn.fetch(
                """SELECT id, title, content FROM wiki_articles
                   WHERE wiki_id=$1 AND (title ILIKE $2 OR content ILIKE $2)""",
                wiki_id, pattern,
            )
            for row in rows:
                d = dict(row)
                aid = d['id']
                if aid not in seen:
                    seen[aid] = {'data': d, 'score': 0}
                seen[aid]['score'] += 2 if word in d['title'].lower() else 1

        ranked = sorted(seen.values(), key=lambda x: -x['score'])
        return [item['data'] for item in ranked[:limit]]


# ---------------------------------------------------------------------------
# Article links
# ---------------------------------------------------------------------------

async def replace_article_links(article_id: int, to_titles: list[str]):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM article_links WHERE from_id=$1", article_id)
        if to_titles:
            await conn.executemany(
                "INSERT INTO article_links (from_id, to_title) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                [(article_id, t) for t in to_titles],
            )


async def get_all_article_links(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT al.from_id, al.to_title
               FROM article_links al
               JOIN wiki_articles wa ON wa.id = al.from_id
               WHERE wa.wiki_id=$1""",
            wiki_id,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Graph snapshots
# ---------------------------------------------------------------------------

async def save_graph_snapshot(wiki_id: int, graph_json: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO graph_snapshots (wiki_id, graph_json) VALUES ($1, $2)",
            wiki_id, graph_json,
        )


async def get_latest_graph(wiki_id: int) -> str | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT graph_json FROM graph_snapshots WHERE wiki_id=$1 ORDER BY created_at DESC LIMIT 1",
            wiki_id,
        )
        return row["graph_json"] if row else None


# ---------------------------------------------------------------------------
# Gmail accounts (OAuth tokens + sync settings)
# ---------------------------------------------------------------------------

async def upsert_gmail_account(
    wiki_id: int,
    email: str,
    access_token: str,
    refresh_token: str | None,
    token_expiry,
) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO gmail_accounts (wiki_id, email, access_token, refresh_token, token_expiry)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (wiki_id) DO UPDATE SET
                   email=EXCLUDED.email,
                   access_token=EXCLUDED.access_token,
                   refresh_token=COALESCE(EXCLUDED.refresh_token, gmail_accounts.refresh_token),
                   token_expiry=EXCLUDED.token_expiry,
                   last_error=NULL
               RETURNING *""",
            wiki_id, email, access_token, refresh_token, token_expiry,
        )
        return dict(row)


async def get_gmail_account(wiki_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM gmail_accounts WHERE wiki_id=$1", wiki_id)
        return dict(row) if row else None


async def list_gmail_accounts(sync_enabled_only: bool = False) -> list[dict]:
    async with get_pool().acquire() as conn:
        q = "SELECT * FROM gmail_accounts"
        if sync_enabled_only:
            q += " WHERE sync_enabled"
        rows = await conn.fetch(q + " ORDER BY id")
        return [dict(r) for r in rows]


async def update_gmail_tokens(account_id: int, access_token: str, token_expiry):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE gmail_accounts SET access_token=$1, token_expiry=$2 WHERE id=$3",
            access_token, token_expiry, account_id,
        )


async def update_gmail_settings(wiki_id: int, label_name: str | None, sync_enabled: bool | None) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE gmail_accounts
               SET label_name=COALESCE($1, label_name),
                   sync_enabled=COALESCE($2, sync_enabled)
               WHERE wiki_id=$3 RETURNING *""",
            label_name, sync_enabled, wiki_id,
        )
        return dict(row) if row else None


async def update_gmail_sync_state(account_id: int, history_id: str | None = None, error: str | None = None):
    async with get_pool().acquire() as conn:
        await conn.execute(
            """UPDATE gmail_accounts
               SET history_id=COALESCE($1, history_id), last_sync_at=now(), last_error=$2
               WHERE id=$3""",
            history_id, error, account_id,
        )


async def delete_gmail_account(wiki_id: int) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM gmail_accounts WHERE wiki_id=$1", wiki_id)
        return result == "DELETE 1"


# ---------------------------------------------------------------------------
# Email threads (thread ↔ wiki page mapping; persisted sync state)
# ---------------------------------------------------------------------------

async def upsert_email_thread(
    wiki_id: int,
    thread_id: str,
    subject: str,
    sender: str | None,
    participants: list[str],
    doc_id: int | None,
    article_id: int | None,
    message_ids: list[str],
    is_newsletter: bool,
    has_attachments: bool,
    gmail_link: str | None,
    last_message_at,
) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO email_threads
               (wiki_id, thread_id, subject, sender, participants, doc_id, article_id,
                message_ids, is_newsletter, has_attachments, gmail_link, last_message_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
               ON CONFLICT (wiki_id, thread_id) DO UPDATE SET
                   subject=EXCLUDED.subject,
                   sender=EXCLUDED.sender,
                   participants=EXCLUDED.participants,
                   doc_id=EXCLUDED.doc_id,
                   article_id=EXCLUDED.article_id,
                   message_ids=EXCLUDED.message_ids,
                   is_newsletter=EXCLUDED.is_newsletter,
                   has_attachments=EXCLUDED.has_attachments,
                   gmail_link=EXCLUDED.gmail_link,
                   last_message_at=EXCLUDED.last_message_at,
                   updated_at=now()
               RETURNING *""",
            wiki_id, thread_id, subject, sender, json.dumps(participants),
            doc_id, article_id, json.dumps(message_ids), is_newsletter,
            has_attachments, gmail_link, last_message_at,
        )
        return dict(row)


async def get_email_thread(wiki_id: int, thread_id: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM email_threads WHERE wiki_id=$1 AND thread_id=$2", wiki_id, thread_id
        )
        return dict(row) if row else None


async def list_email_threads(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_threads WHERE wiki_id=$1 ORDER BY last_message_at DESC NULLS LAST",
            wiki_id,
        )
        return [dict(r) for r in rows]


async def list_email_threads_by_sender(wiki_id: int, sender: str) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_threads WHERE wiki_id=$1 AND lower(sender)=lower($2)",
            wiki_id, sender,
        )
        return [dict(r) for r in rows]


async def delete_email_thread(thread_row_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM email_threads WHERE id=$1", thread_row_id)


async def get_email_thread_by_article(article_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM email_threads WHERE article_id=$1", article_id)
        return dict(row) if row else None


async def get_email_links_for_articles(article_ids: list[int]) -> dict[int, str]:
    """Map article_id → Gmail deep link, for query-answer citations."""
    if not article_ids:
        return {}
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT article_id, gmail_link FROM email_threads WHERE article_id = ANY($1::int[])",
            article_ids,
        )
        return {r["article_id"]: r["gmail_link"] for r in rows if r["gmail_link"]}


async def search_email_threads(
    wiki_id: int,
    q: str | None = None,
    sender: str | None = None,
    after: str | None = None,
    before: str | None = None,
    has_attachment: bool | None = None,
    topic: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Filtered search across email-derived pages (Phase 4)."""
    conds = ["t.wiki_id = $1"]
    params: list = [wiki_id]

    def _add(cond_tpl: str, value):
        params.append(value)
        conds.append(cond_tpl.format(n=len(params)))

    if sender:
        _add("(t.sender ILIKE ${n} OR t.participants ILIKE ${n})", f"%{sender}%")
    if after:
        _add("t.last_message_at >= ${n}::timestamptz", after)
    if before:
        _add("t.last_message_at <= ${n}::timestamptz", before)
    if has_attachment is not None:
        _add("t.has_attachments = ${n}", has_attachment)
    if q:
        _add("(t.subject ILIKE ${n} OR a.content ILIKE ${n})", f"%{q}%")
    if topic:
        _add(
            "(EXISTS (SELECT 1 FROM article_links al WHERE al.from_id = t.article_id AND al.to_title ILIKE ${n}) "
            "OR a.content ILIKE ${n})",
            f"%{topic}%",
        )

    query = f"""
        SELECT t.id, t.thread_id, t.subject, t.sender, t.participants, t.article_id,
               t.is_newsletter, t.has_attachments, t.gmail_link, t.last_message_at,
               a.title AS article_title
        FROM email_threads t
        LEFT JOIN wiki_articles a ON a.id = t.article_id
        WHERE {' AND '.join(conds)}
        ORDER BY t.last_message_at DESC NULLS LAST
        LIMIT {int(limit)}
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Email sync log + metrics
# ---------------------------------------------------------------------------

async def add_sync_log(wiki_id: int, thread_id: str | None, action: str, detail: str = ""):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO email_sync_log (wiki_id, thread_id, action, detail) VALUES ($1, $2, $3, $4)",
            wiki_id, thread_id, action, detail,
        )


async def get_sync_log(wiki_id: int, limit: int = 50) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, thread_id, action, detail, created_at FROM email_sync_log "
            "WHERE wiki_id=$1 ORDER BY created_at DESC LIMIT $2",
            wiki_id, limit,
        )
        return [dict(r) for r in rows]


async def get_email_metrics(wiki_id: int, days: int = 7) -> dict:
    """Items ingested/day, dedupe hits, errors — Phase 5 metrics."""
    async with get_pool().acquire() as conn:
        per_day = await conn.fetch(
            """SELECT date_trunc('day', created_at)::date AS day, action, COUNT(*) AS count
               FROM email_sync_log
               WHERE wiki_id=$1 AND created_at > now() - ($2 || ' days')::interval
               GROUP BY 1, 2 ORDER BY 1 DESC""",
            wiki_id, str(days),
        )
        totals = await conn.fetch(
            "SELECT action, COUNT(*) AS count FROM email_sync_log WHERE wiki_id=$1 GROUP BY action",
            wiki_id,
        )
        threads = await conn.fetchval("SELECT COUNT(*) FROM email_threads WHERE wiki_id=$1", wiki_id)
        return {
            "per_day": [dict(r) for r in per_day],
            "totals": {r["action"]: r["count"] for r in totals},
            "threads_tracked": threads,
        }


# ---------------------------------------------------------------------------
# Entities (people / organizations / projects) — Phase 3
# ---------------------------------------------------------------------------

async def upsert_entity(wiki_id: int, kind: str, name: str, email: str | None = None) -> dict:
    async with get_pool().acquire() as conn:
        # Merge by email first (a person renamed in a signature is still the same person).
        if email:
            existing = await conn.fetchrow(
                "SELECT * FROM entities WHERE wiki_id=$1 AND kind=$2 AND lower(email)=lower($3)",
                wiki_id, kind, email,
            )
            if existing:
                return dict(existing)
        row = await conn.fetchrow(
            """INSERT INTO entities (wiki_id, kind, name, email) VALUES ($1, $2, $3, $4)
               ON CONFLICT (wiki_id, kind, name) DO UPDATE SET
                   email=COALESCE(entities.email, EXCLUDED.email)
               RETURNING *""",
            wiki_id, kind, name, email,
        )
        return dict(row)


async def set_entity_article(entity_id: int, article_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE entities SET article_id=$1 WHERE id=$2", article_id, entity_id)


async def add_entity_mention(entity_id: int, doc_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_mentions (entity_id, doc_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            entity_id, doc_id,
        )


async def get_entity(entity_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM entities WHERE id=$1", entity_id)
        return dict(row) if row else None


async def list_entities(wiki_id: int, kind: str | None = None) -> list[dict]:
    async with get_pool().acquire() as conn:
        if kind:
            rows = await conn.fetch(
                "SELECT * FROM entities WHERE wiki_id=$1 AND kind=$2 ORDER BY name", wiki_id, kind
            )
        else:
            rows = await conn.fetch("SELECT * FROM entities WHERE wiki_id=$1 ORDER BY kind, name", wiki_id)
        return [dict(r) for r in rows]


async def get_entity_threads(entity_id: int) -> list[dict]:
    """Email threads an entity appears in (via entity_mentions → doc → thread)."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT t.subject, t.gmail_link, t.last_message_at, a.title AS article_title
               FROM entity_mentions em
               JOIN email_threads t ON t.doc_id = em.doc_id
               LEFT JOIN wiki_articles a ON a.id = t.article_id
               WHERE em.entity_id = $1
               ORDER BY t.last_message_at DESC NULLS LAST""",
            entity_id,
        )
        return [dict(r) for r in rows]


async def get_entities_for_doc(doc_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.* FROM entities e
               JOIN entity_mentions em ON em.entity_id = e.id
               WHERE em.doc_id = $1""",
            doc_id,
        )
        return [dict(r) for r in rows]


async def remove_mentions_for_doc(doc_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM entity_mentions WHERE doc_id=$1", doc_id)


async def delete_orphan_entities(wiki_id: int) -> list[dict]:
    """Delete entities with no remaining mentions. Returns the deleted rows
    (so callers can also remove their person/org pages)."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """DELETE FROM entities e
               WHERE e.wiki_id=$1
                 AND NOT EXISTS (SELECT 1 FROM entity_mentions em WHERE em.entity_id = e.id)
               RETURNING e.id, e.kind, e.name, e.article_id""",
            wiki_id,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sender denylist — Phase 5
# ---------------------------------------------------------------------------

async def list_denylist(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, pattern, created_at FROM email_denylist WHERE wiki_id=$1 ORDER BY pattern",
            wiki_id,
        )
        return [dict(r) for r in rows]


async def add_denylist(wiki_id: int, pattern: str) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO email_denylist (wiki_id, pattern) VALUES ($1, $2)
               ON CONFLICT (wiki_id, pattern) DO UPDATE SET pattern=EXCLUDED.pattern
               RETURNING id, pattern, created_at""",
            wiki_id, pattern.strip().lower(),
        )
        return dict(row)


async def remove_denylist(wiki_id: int, entry_id: int) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM email_denylist WHERE wiki_id=$1 AND id=$2", wiki_id, entry_id
        )
        return result == "DELETE 1"


# ---------------------------------------------------------------------------
# Reset (content only — keeps the wiki record)
# ---------------------------------------------------------------------------

async def reset_wiki_content(wiki_id: int) -> dict:
    async with get_pool().acquire() as conn:
        articles = await conn.fetchval(
            "SELECT COUNT(*) FROM wiki_articles WHERE wiki_id=$1", wiki_id
        )
        await conn.execute("DELETE FROM wiki_articles WHERE wiki_id=$1", wiki_id)
        await conn.execute("DELETE FROM graph_snapshots WHERE wiki_id=$1", wiki_id)
        await conn.execute("DELETE FROM email_threads WHERE wiki_id=$1", wiki_id)
        await conn.execute("DELETE FROM entities WHERE wiki_id=$1", wiki_id)
        await conn.execute(
            "UPDATE source_documents SET status='archived' WHERE wiki_id=$1", wiki_id
        )
        return {"articles_deleted": articles}
