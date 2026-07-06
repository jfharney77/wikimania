"""Phase 2 + 4 — hands-free Gmail sync.

A background task polls each connected account every GMAIL_SYNC_INTERVAL
seconds for (a) threads carrying the configured label (default 'wiki') and
(b) mail delivered to the +wiki sub-address alias. Threads are ingested
through the Phase 1 mapping; per-thread message ids are persisted in
email_threads, so sync is incremental, idempotent, and survives restarts.
"""

import asyncio
import os
import traceback

import db
import email_ingest
import gmail_client

SYNC_INTERVAL = int(os.getenv("GMAIL_SYNC_INTERVAL", "120"))
SYNC_PAGE_SIZE = int(os.getenv("GMAIL_SYNC_PAGE_SIZE", "50"))
SYNC_MAX_PAGES = int(os.getenv("GMAIL_SYNC_MAX_PAGES", "10"))

_task: asyncio.Task | None = None


async def _collect_thread_ids(account: dict, seen: set[str], out: list[str],
                              q: str = "", label_ids: list[str] | None = None):
    """Follow nextPageToken so a backlog bigger than one page still syncs."""
    page_token = None
    for _ in range(SYNC_MAX_PAGES):
        data = await gmail_client.list_threads(
            account, q=q, label_ids=label_ids,
            max_results=SYNC_PAGE_SIZE, page_token=page_token,
        )
        for t in data.get("threads", []):
            if t["id"] not in seen:
                seen.add(t["id"])
                out.append(t["id"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break


async def sync_account(account: dict) -> dict:
    """Run one sync cycle for one connected account. Returns cycle stats."""
    wiki_id = account["wiki_id"]
    stats = {"checked": 0, "ingested": 0, "updated": 0, "skipped": 0, "denied": 0, "errors": 0}

    thread_ids: list[str] = []
    seen: set[str] = set()

    # (a) labeled threads
    label_id = await gmail_client.resolve_label_id(account, account["label_name"])
    if label_id:
        await _collect_thread_ids(account, seen, thread_ids, label_ids=[label_id])

    # (b) +wiki sub-address forwards (Phase 4) — ingested regardless of label
    local, _, domain = account["email"].partition("@")
    if local and domain:
        alias = f"{local}{email_ingest.INGEST_SUFFIX}@{domain}"
        await _collect_thread_ids(account, seen, thread_ids, q=f"to:{alias}")

    for tid in thread_ids:
        stats["checked"] += 1
        try:
            result = await email_ingest.ingest_thread(wiki_id, account, tid, trigger="sync")
            stats[result["action"] if result["action"] in stats else "skipped"] += 1
        except Exception as e:
            stats["errors"] += 1
            await db.add_sync_log(wiki_id, tid, "error", str(e)[:500])

    # Persist the mailbox historyId so restarts can prove where they resumed.
    history_id = None
    try:
        profile = await gmail_client.get_profile(account)
        history_id = str(profile.get("historyId", "")) or None
    except Exception:
        pass
    await db.update_gmail_sync_state(account["id"], history_id=history_id, error=None)
    return stats


async def sync_once() -> list[dict]:
    """One cycle across all sync-enabled accounts (used by the loop and the
    manual 'Sync now' endpoint)."""
    results = []
    for account in await db.list_gmail_accounts(sync_enabled_only=True):
        try:
            stats = await sync_account(account)
            results.append({"wiki_id": account["wiki_id"], "email": account["email"], **stats})
        except Exception as e:
            await db.update_gmail_sync_state(account["id"], error=str(e)[:500])
            await db.add_sync_log(account["wiki_id"], None, "error", f"Sync cycle failed: {e}")
            results.append({"wiki_id": account["wiki_id"], "email": account["email"], "error": str(e)})
    return results


async def _sync_loop():
    # Small initial delay so startup (migrations, first admin) settles first.
    await asyncio.sleep(5)
    while True:
        try:
            await sync_once()
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(SYNC_INTERVAL)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_sync_loop())


async def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
