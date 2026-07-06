"""Gmail OAuth + REST client (read-only scope).

Implemented directly over httpx (already a project dependency) instead of the
Google SDK. Handles token refresh on 401/expiry and exponential backoff on
429/5xx (Phase 5 hardening).
"""

import asyncio
import base64
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx

import db

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8001/api/gmail/oauth/callback"
)

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

MAX_RETRIES = 5


class GmailError(Exception):
    pass


class GmailAuthError(GmailError):
    pass


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def auth_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",       # get a refresh token
        "prompt": "consent",            # force refresh token on re-connect
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange an auth code for tokens. Returns token payload with expiry datetime."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_ENDPOINT, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
        })
    if resp.status_code != 200:
        raise GmailAuthError(f"Token exchange failed: {resp.text}")
    data = resp.json()
    data["expiry"] = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
    return data


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_ENDPOINT, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        raise GmailAuthError(f"Token refresh failed: {resp.text}")
    data = resp.json()
    data["expiry"] = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
    return data


async def _get_valid_token(account: dict) -> str:
    """Return a usable access token for the account, refreshing if expired."""
    expiry = account.get("token_expiry")
    if expiry and expiry > datetime.now(timezone.utc):
        return account["access_token"]
    if not account.get("refresh_token"):
        raise GmailAuthError("Access token expired and no refresh token available — reconnect Gmail.")
    data = await refresh_access_token(account["refresh_token"])
    await db.update_gmail_tokens(account["id"], data["access_token"], data["expiry"])
    account["access_token"] = data["access_token"]
    account["token_expiry"] = data["expiry"]
    return data["access_token"]


# ---------------------------------------------------------------------------
# REST wrapper with backoff
# ---------------------------------------------------------------------------

async def _api_get(account: dict, path: str, params: dict | None = None) -> dict:
    token = await _get_valid_token(account)
    backoff = 2.0
    for attempt in range(MAX_RETRIES):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 401 and attempt == 0 and account.get("refresh_token"):
            data = await refresh_access_token(account["refresh_token"])
            await db.update_gmail_tokens(account["id"], data["access_token"], data["expiry"])
            account["access_token"] = data["access_token"]
            account["token_expiry"] = data["expiry"]
            token = data["access_token"]
            continue
        if resp.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        if resp.status_code != 200:
            raise GmailError(f"Gmail API {path} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()
    raise GmailError(f"Gmail API {path} failed after {MAX_RETRIES} retries.")


# ---------------------------------------------------------------------------
# Gmail endpoints
# ---------------------------------------------------------------------------

async def get_profile(account: dict) -> dict:
    return await _api_get(account, "/profile")


async def get_profile_with_token(access_token: str) -> dict:
    """Profile lookup right after OAuth, before an account row exists."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/profile", headers={"Authorization": f"Bearer {access_token}"}
        )
    if resp.status_code != 200:
        raise GmailError(f"Profile lookup failed: {resp.text[:300]}")
    return resp.json()


async def list_labels(account: dict) -> list[dict]:
    data = await _api_get(account, "/labels")
    return data.get("labels", [])


async def list_threads(account: dict, q: str = "", label_ids: list[str] | None = None,
                       max_results: int = 25, page_token: str | None = None) -> dict:
    params: dict = {"maxResults": max_results}
    if q:
        params["q"] = q
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token
    return await _api_get(account, "/threads", params)


async def get_thread(account: dict, thread_id: str, fmt: str = "full") -> dict:
    return await _api_get(account, f"/threads/{thread_id}", {"format": fmt})


async def get_attachment(account: dict, message_id: str, attachment_id: str) -> bytes:
    data = await _api_get(account, f"/messages/{message_id}/attachments/{attachment_id}")
    return base64.urlsafe_b64decode(data.get("data", "") + "===")


async def resolve_label_id(account: dict, label_name: str) -> str | None:
    for label in await list_labels(account):
        if label.get("name", "").lower() == label_name.lower():
            return label["id"]
    return None
