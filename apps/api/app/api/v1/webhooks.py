"""
webhooks.py — Microsoft Graph change-notification endpoint

Two request types handled at POST /api/v1/webhooks/graph:

1. Validation handshake (GET or POST with ?validationToken=...)
   Microsoft sends this when you first register the subscription.
   We must echo back the token as plain text with 200.

2. Change notification (POST with JSON body)
   Microsoft sends this when a new email arrives.
   We verify clientState, fetch the full message, run ingestion.

Security:
  - clientState verified on every notification (GRAPH_WEBHOOK_SECRET)
  - No auth token required — this endpoint is public (Microsoft calls it)
  - Heavy work is done inside the session; any exception rolls back cleanly

DO NOT:
  - send emails (no outbound mail here)
  - assign tickets (ingestion only)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.services import graph_webhook_service as graph_svc
from app.services.ticket_ingestion_service import (
    validate_email_payload,
    resolve_participants,
    ingest_email,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/webhooks/graph  — validation handshake
# ══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def graph_webhook_validation(
    validationToken: Optional[str] = Query(default=None),
):
    """
    Microsoft Graph sends a GET with ?validationToken=<token> when a
    subscription is created. We must return the token as plain text.
    """
    if not validationToken:
        raise HTTPException(status_code=400, detail="Missing validationToken")
    log.info("[Webhook] Validation handshake received")
    return Response(content=validationToken, media_type="text/plain", status_code=200)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/webhooks/graph  — notification handler
# ══════════════════════════════════════════════════════════════════════════════

@router.post("")
async def graph_webhook_notification(
    request: Request,
    validationToken: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """
    Handles both:
      - POST validation handshake (validationToken in query)
      - Real change notifications (JSON body)

    Graph requires a 202 Accepted response within 10 seconds.
    We acknowledge immediately and do not block on slow DB calls
    beyond what's needed — all logic is async.
    """
    # ── Validation handshake via POST ────────────────────────────────────────
    if validationToken:
        log.info("[Webhook] Validation handshake (POST) received")
        return Response(content=validationToken, media_type="text/plain", status_code=200)

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        log.warning("[Webhook] Failed to parse JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    values: list[dict] = body.get("value", [])
    if not values:
        # Graph sometimes sends lifecycle notifications (subscription expiry).
        # Acknowledge and ignore.
        log.debug("[Webhook] Empty notification batch — acknowledged")
        return Response(status_code=202)

    # ── Process each notification in the batch ────────────────────────────────
    for notification in values:
        await _handle_single_notification(notification, session)

    return Response(status_code=202)


# ══════════════════════════════════════════════════════════════════════════════
# Per-notification handler
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_single_notification(notification: dict, session: AsyncSession) -> None:
    """
    Process one Graph change notification.

    Pipeline:
      1. Verify clientState (GRAPH_WEBHOOK_SECRET)
      2. Extract message ID from resourceData
      3. Fetch full message from Graph
      4. Validate payload ([TICKET] tag + known user)
      5. Resolve participants (DB lookup)
      6. Route to existing thread or create new ticket
    """
    # ── 1. Verify clientState ─────────────────────────────────────────────────
    client_state = notification.get("clientState", "")
    if client_state != settings.GRAPH_WEBHOOK_SECRET:
        log.warning(
            "[Webhook] clientState mismatch — expected %r got %r. Dropping.",
            settings.GRAPH_WEBHOOK_SECRET,
            client_state,
        )
        return

    # ── 2. Extract message ID ─────────────────────────────────────────────────
    resource_data = notification.get("resourceData", {})
    message_id = resource_data.get("id") or _extract_id_from_resource(notification.get("resource", ""))
    if not message_id:
        log.warning("[Webhook] No message ID in notification — skipping: %s", notification)
        return

    # ── 3. Fetch full message from Graph ──────────────────────────────────────
    try:
        email_payload = await graph_svc.fetch_message(message_id)
    except Exception as exc:
        log.error("[Webhook] Failed to fetch message %s: %s", message_id, exc)
        return

    if email_payload is None:
        log.info("[Webhook] Message %s not found (deleted?) — skipping", message_id)
        return

    log.info(
        "[Webhook] Processing email — subject=%r from=%s",
        email_payload.get("subject"),
        email_payload.get("sender", {}).get("emailAddress", {}).get("address"),
    )

    # ── 4. Validate ───────────────────────────────────────────────────────────
    ok, reason = validate_email_payload(email_payload)
    if not ok:
        log.info("[Webhook] Email dropped: %s", reason)
        return

    # ── 5. Resolve participants ───────────────────────────────────────────────
    try:
        participants = await resolve_participants(session, email_payload)
    except Exception as exc:
        log.error("[Webhook] resolve_participants failed: %s", exc)
        return

    if not participants["any_known"]:
        log.info("[Webhook] No known users found in email — dropping")
        return

    # ── 6. Ingest ─────────────────────────────────────────────────────────────
    try:
        ticket = await ingest_email(session, email_payload, participants)
        if ticket:
            log.info("[Webhook] ✓ Ticket %s processed successfully", ticket.id)
    except Exception as exc:
        await session.rollback()
        log.error("[Webhook] ingest_email failed for message %s: %s", message_id, exc, exc_info=True)


def _extract_id_from_resource(resource: str) -> Optional[str]:
    """
    Fallback: parse the message ID from a resource path like
    "/users/.../messages/<id>".
    """
    if not resource:
        return None
    parts = resource.rstrip("/").split("/")
    return parts[-1] if parts else None
