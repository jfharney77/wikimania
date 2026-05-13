import asyncpg
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

async def save_document(wiki_id: int, filename: str, content: str) -> int:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO source_documents (wiki_id, filename, content) VALUES ($1, $2, $3) RETURNING id",
            wiki_id, filename, content,
        )
        return row["id"]


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
            "SELECT id, filename, uploaded_at, status FROM source_documents WHERE wiki_id=$1 ORDER BY uploaded_at DESC",
            wiki_id,
        )
        return [dict(r) for r in rows]


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


# ---------------------------------------------------------------------------
# Wiki articles
# ---------------------------------------------------------------------------

async def upsert_article(wiki_id: int, title: str, content: str) -> tuple[int, bool]:
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
                "INSERT INTO wiki_articles (wiki_id, title, content) VALUES ($1, $2, $3) RETURNING id",
                wiki_id, title, content,
            )
            return row["id"], True


async def get_article_content(wiki_id: int, title: str) -> str | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM wiki_articles WHERE wiki_id=$1 AND title=$2", wiki_id, title
        )
        return row["content"] if row else None


async def list_articles(wiki_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, created_at, updated_at FROM wiki_articles WHERE wiki_id=$1 ORDER BY title",
            wiki_id,
        )
        return [dict(r) for r in rows]


async def get_article_by_id(article_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, content, created_at, updated_at FROM wiki_articles WHERE id=$1",
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
# Reset (content only — keeps the wiki record)
# ---------------------------------------------------------------------------

async def reset_wiki_content(wiki_id: int) -> dict:
    async with get_pool().acquire() as conn:
        articles = await conn.fetchval(
            "SELECT COUNT(*) FROM wiki_articles WHERE wiki_id=$1", wiki_id
        )
        await conn.execute("DELETE FROM wiki_articles WHERE wiki_id=$1", wiki_id)
        await conn.execute("DELETE FROM graph_snapshots WHERE wiki_id=$1", wiki_id)
        await conn.execute(
            "UPDATE source_documents SET status='archived' WHERE wiki_id=$1", wiki_id
        )
        return {"articles_deleted": articles}
