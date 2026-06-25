# Wikimania Critique & Spec — #3

## Criticism

**Authentication is present but authorization is absent: any logged-in user can read, modify, and destroy every other user's wikis.**

The application has a complete auth stack — JWT tokens, bcrypt-hashed passwords, role-based admin management, protected endpoints — but no per-resource ownership model. The `wikis` table has no `owner` column; `list_wikis()` returns every wiki unconditionally; no endpoint checks whether the requesting user owns the wiki it is operating on.

Concrete attack paths, each requiring only a valid JWT:

| Request | Effect |
|---------|--------|
| `GET /api/wikis` | Returns every wiki from every user with its integer ID |
| `DELETE /api/wikis/3` | Permanently deletes user B's wiki including all articles, documents, jobs, and graph snapshots — no ownership check |
| `POST /api/wikis/3/documents/upload` | Injects a document into another user's wiki, triggering a generation job at their rate-limit quota |
| `DELETE /api/wikis/3/content` | Wipes all articles in any wiki |
| `POST /api/wikis/3/critic` | Starts a long-running background job against any wiki |

Because IDs are sequential integers and `GET /api/wikis` already enumerates all of them, there is no obscurity defence even in a single-user scenario where a second account is later added.

Evidence in code:

- `db.py:195–208` — `create_wiki(name)` takes no user argument; `list_wikis()` has no `WHERE` filter
- `db.py:14–22` — `wikis` DDL has no `created_by` column
- `main.py:226–228` — `list_wikis` passes `get_current_user` result straight through without filtering by caller
- `main.py:238–244` — `delete_wiki` fetches the wiki by ID and deletes it; no ownership assertion
- `main.py:251–275` — `upload_document` validates `wiki_id` existence only
- Every wiki-scoped endpoint (`list_documents`, `list_articles`, `get_article`, `query_wiki`, `get_graph`, `export_vault`, `start_critic`, `reset_wiki_content`) is identical: authenticate caller, look up resource by ID, act — ownership never checked

This means adding a second user to any deployment immediately grants that user full destructive access to everyone else's data. The auth machinery (bcrypt, JWT, admin roles) provides no actual data isolation.

---

## Spec

### Goal

Scope every wiki to its creator. A regular user can only see and act on wikis they own. Admin users retain full visibility. No API shape changes — only HTTP status codes change for unauthorized access.

### Approach

**Phase 1 — Add ownership to the data model**

1. Add a `created_by` column to `wikis`:
   ```sql
   ALTER TABLE wikis ADD COLUMN IF NOT EXISTS
       created_by INT REFERENCES users(id) ON DELETE SET NULL;
   ```
   Placed in `init_pool` alongside the existing migration block so it applies automatically on next startup. Existing wikis get `NULL` (treated as admin-only — see acceptance criterion 4).

2. Update `db.create_wiki(name: str, user_id: int) -> dict` to insert `created_by = user_id`.

3. Update `db.list_wikis(user_id: int | None = None, is_admin: bool = False) -> list[dict]`:
   - Admin (`is_admin=True`): no filter (current behaviour).
   - Regular user: `WHERE created_by = $1`.

4. Add `db.get_wiki_owner(wiki_id: int) -> int | None` returning `created_by`.

**Phase 2 — Enforce ownership in the API layer**

Add a helper in `main.py`:

```python
async def _require_wiki_access(wiki_id: int, user: dict) -> dict:
    """Raises 404 if the wiki doesn't exist, 403 if the caller doesn't own it."""
    wiki = await db.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found.")
    if user.get("role") != "admin" and wiki.get("created_by") != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return wiki
```

Apply it by replacing the `await db.get_wiki(wiki_id)` + bare existence check in every wiki-scoped endpoint:

| Endpoint | Change |
|----------|--------|
| `DELETE /api/wikis/{wiki_id}` | Replace `db.get_wiki` call with `_require_wiki_access` |
| `POST /api/wikis/{wiki_id}/documents/upload` | Same |
| `GET /api/wikis/{wiki_id}/documents` | Same |
| `GET /api/wikis/{wiki_id}/articles` | Same |
| `GET /api/wikis/{wiki_id}/articles/{article_id}` | Same |
| `POST /api/wikis/{wiki_id}/query` | Same |
| `GET /api/wikis/{wiki_id}/graph` | Same |
| `GET /api/wikis/{wiki_id}/export` | Same |
| `POST /api/wikis/{wiki_id}/critic` | Same |
| `DELETE /api/wikis/{wiki_id}/content` | Same |

**Phase 3 — Thread user identity through `create_wiki`**

In `main.py:create_wiki`, pass `user["sub"]` as `user_id` to `db.create_wiki`. In `main.py:list_wikis`, pass `user["sub"]` and `user["role"] == "admin"` to `db.list_wikis`.

### Specific file changes

| File | Change |
|------|--------|
| `backend/db.py` | Migration: `ADD COLUMN IF NOT EXISTS created_by INT REFERENCES users(id) ON DELETE SET NULL`; update `create_wiki` signature; update `list_wikis` to accept and apply `user_id`/`is_admin` filter; add `get_wiki_owner` |
| `backend/main.py` | Add `_require_wiki_access` helper; update all 10 wiki-scoped endpoints to call it; pass user context to `db.create_wiki` and `db.list_wikis` |

No new tables, no API shape changes, no frontend changes required.

### Acceptance criteria

1. User A creates wiki W1. `GET /api/wikis` as User B (non-admin) returns an empty list (W1 is not visible).
2. User B sending `DELETE /api/wikis/{W1.id}` receives `403 Not authorized`, not `200`.
3. User B sending `POST /api/wikis/{W1.id}/documents/upload` receives `403 Not authorized`.
4. An admin user can list, delete, and upload to all wikis including those owned by others and those with `created_by = NULL` (legacy rows).
5. User A can still perform all operations on their own wiki W1 without any change in UX.
6. Existing wikis with `created_by = NULL` are returned only in admin-scoped list calls, not in regular-user list calls — preventing data leakage from pre-migration rows.
