"""
graph_webhook_service.py — Microsoft Graph API integration

Handles:
  - Acquiring an app-only access token via client-credentials flow
  - Registering/renewing a change-notification subscription on the shared mailbox
  - Fetching a full email message by its Graph message ID

Docs:
  https://learn.microsoft.com/en-us/graph/api/resources/change-notifications-api-overview
  https://learn.microsoft.com/en-us/graph/auth-v2-service

Required Azure permissions (Application):
  Mail.Read  — to read messages from the target mailbox
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# ── Graph endpoints ────────────────────────────────────────────────────────────
_AUTHORITY = "https://login.microsoftonline.com"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# ── In-memory token cache (single-instance; fine for one worker) ──────────────
_token_cache: dict = {"access_token": None, "expires_at": None}


# ══════════════════════════════════════════════════════════════════════════════
# Token management
# ══════════════════════════════════════════════════════════════════════════════

async def _get_access_token() -> str:
    """
    Return a valid app-only access token for Microsoft Graph.
    Re-uses a cached token until it expires (with a 60-second buffer).
    """
    now = datetime.now(tz=timezone.utc)
    if (
        _token_cache["access_token"]
        and _token_cache["expires_at"]
        and _token_cache["expires_at"] > now + timedelta(seconds=60)
    ):
        return _token_cache["access_token"]

    tenant_id = settings.GRAPH_TENANT_ID
    if not tenant_id or not settings.GRAPH_CLIENT_ID or not settings.GRAPH_CLIENT_SECRET:
        raise RuntimeError(
            "GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET must be set in .env"
        )

    url = f"{_AUTHORITY}/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "client_id": settings.GRAPH_CLIENT_ID,
        "client_secret": settings.GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=payload)

    if resp.status_code != 200:
        log.error("[Graph] Token request failed %s: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Graph token request failed: {resp.status_code}")

    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + timedelta(seconds=int(data.get("expires_in", 3600)))
    log.info("[Graph] New access token acquired (expires in %ss)", data.get("expires_in"))
    return _token_cache["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ══════════════════════════════════════════════════════════════════════════════
# Subscription management
# ══════════════════════════════════════════════════════════════════════════════

async def register_webhook_subscription() -> dict:
    """
    Register a Graph change-notification subscription on the target mailbox
    inbox. Returns the subscription object from Graph.

    Call once on application startup (or whenever the subscription expires).
    Subscriptions last at most 4230 minutes (~3 days) for mail resources;
    you should schedule renewal before that point.
    """
    token = await _get_access_token()
    mailbox = settings.GRAPH_MAILBOX or settings.EMAIL_USER
    if not mailbox:
        raise RuntimeError("GRAPH_MAILBOX (or EMAIL_USER) must be set in .env")

    # Expire in 3 days (just under the Graph maximum)
    expiry = (datetime.now(tz=timezone.utc) + timedelta(minutes=4200)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    body = {
        "changeType": "created",
        "notificationUrl": settings.GRAPH_WEBHOOK_URL,
        "resource": f"/users/{mailbox}/mailFolders/inbox/messages",
        "expirationDateTime": expiry,
        "clientState": settings.GRAPH_WEBHOOK_SECRET,
        "latestSupportedTlsVersion": "v1_2",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/subscriptions",
            headers=_auth_headers(token),
            content=json.dumps(body),
        )

    if resp.status_code not in (200, 201):
        log.error("[Graph] Subscription registration failed %s: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Graph subscription registration failed: {resp.status_code}")

    sub = resp.json()
    log.info("[Graph] Subscription registered — id=%s expires=%s", sub.get("id"), sub.get("expirationDateTime"))
    return sub


async def renew_webhook_subscription(subscription_id: str) -> dict:
    """Extend an existing subscription's expiry by 3 days."""
    token = await _get_access_token()
    expiry = (datetime.now(tz=timezone.utc) + timedelta(minutes=4200)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            f"{_GRAPH_BASE}/subscriptions/{subscription_id}",
            headers=_auth_headers(token),
            content=json.dumps({"expirationDateTime": expiry}),
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Graph subscription renewal failed: {resp.status_code}")
    return resp.json()


async def list_webhook_subscriptions() -> list[dict]:
    """List all active webhook subscriptions for this app."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/subscriptions",
            headers=_auth_headers(token),
        )
    if resp.status_code != 200:
        log.error("Failed to list subscriptions: %s", resp.text)
        return []
    return resp.json().get("value", [])


async def delete_webhook_subscription(subscription_id: str) -> bool:
    """Delete a subscription by ID."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"{_GRAPH_BASE}/subscriptions/{subscription_id}",
            headers=_auth_headers(token),
        )
    return resp.status_code == 204


async def maintain_subscription() -> None:
    """
    Self-healing function to run daily.
    Finds existing subscriptions pointing to our URL.
    If none exist, creates one.
    If they exist but expire soon (< 24h), renews them.
    """
    if not settings.GRAPH_TENANT_ID or not settings.GRAPH_CLIENT_ID:
        log.warning("[Graph] Missing credentials, skipping subscription maintenance.")
        return

    log.info("[Graph] Running subscription maintenance...")
    subs = await list_webhook_subscriptions()
    our_url = settings.GRAPH_WEBHOOK_URL
    
    our_subs = [s for s in subs if s.get("notificationUrl") == our_url]
    
    if not our_subs:
        log.info("[Graph] No active subscription found for our URL. Creating one...")
        await register_webhook_subscription()
        return

    now = datetime.now(tz=timezone.utc)
    for sub in our_subs:
        exp_str = sub.get("expirationDateTime")
        if exp_str:
            # Parse Graph's ISO format
            exp = datetime.strptime(exp_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            time_left = exp - now
            
            if time_left < timedelta(hours=24):
                log.info("[Graph] Subscription %s expires in <24h. Renewing...", sub["id"])
                try:
                    await renew_webhook_subscription(sub["id"])
                    log.info("[Graph] Renewed %s successfully.", sub["id"])
                except Exception as e:
                    log.error("[Graph] Failed to renew %s: %s. Will recreate.", sub["id"], e)
                    await delete_webhook_subscription(sub["id"])
                    await register_webhook_subscription()
            else:
                log.info("[Graph] Subscription %s is healthy (expires %s).", sub["id"], exp_str)


# ══════════════════════════════════════════════════════════════════════════════
# Message fetching
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_message(message_id: str) -> Optional[dict]:
    """
    Fetch a full email message object from Graph by its message ID.

    Returns a dict with the shape:
    {
        "id": str,
        "conversationId": str,
        "subject": str,
        "bodyPreview": str,
        "body": {"contentType": "html"|"text", "content": str},
        "sender": {"emailAddress": {"name": str, "address": str}},
        "toRecipients": [{"emailAddress": {"name": str, "address": str}}],
        "ccRecipients": [...],
        "hasAttachments": bool,
    }
    Returns None on 404 (e.g. message deleted before we processed it).
    """
    token = await _get_access_token()
    mailbox = settings.GRAPH_MAILBOX or settings.EMAIL_USER
    # Select only the fields we need to minimise payload
    select_fields = (
        "id,conversationId,subject,bodyPreview,body,"
        "sender,toRecipients,ccRecipients,hasAttachments,receivedDateTime"
    )
    url = f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}?$select={select_fields}"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=_auth_headers(token))

    if resp.status_code == 404:
        log.warning("[Graph] Message %s not found (404)", message_id)
        return None
    if resp.status_code != 200:
        log.error("[Graph] Fetch message failed %s: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Graph fetch message failed: {resp.status_code}")

    return resp.json()
