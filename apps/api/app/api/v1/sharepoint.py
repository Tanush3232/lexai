"""
sharepoint.py — SharePoint + Power Automate Webhooks

Provides HTTP endpoints to ingest legal requests, events (messages), and attachments.
These endpoints are secured by SHAREPOINT_WEBHOOK_SECRET.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import get_session
from app.services.sharepoint_ingestion_service import (
    process_request,
    process_event,
    process_attachment,
)

log = logging.getLogger(__name__)
router = APIRouter()

# Simple dependency to check API key
def verify_sharepoint_secret(x_sharepoint_token: Optional[str] = Header(None)):
    # Use dedicated secret; fall back to GRAPH_WEBHOOK_SECRET for backward-compat
    expected_token = settings.SHAREPOINT_WEBHOOK_SECRET or settings.GRAPH_WEBHOOK_SECRET

    if not expected_token:
        log.warning("[SharePoint] No webhook secret configured — rejecting all requests for safety.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook secret not configured.")

    if not x_sharepoint_token or x_sharepoint_token != expected_token:
        log.warning("[SharePoint] Unauthorized webhook attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-SharePoint-Token header",
        )


class RequestPayload(BaseModel):
    requestId: str
    subject: str
    conversationId: Optional[str] = None
    entity: Optional[str] = None

class EventPayload(BaseModel):
    requestId: str
    sender: str
    timestamp: str
    body: str

class AttachmentPayload(BaseModel):
    requestId: str
    fileName: str
    fileUrl: str
    entity: Optional[str] = None


@router.post("/request", dependencies=[Depends(verify_sharepoint_secret)])
async def create_or_update_request(
    payload: RequestPayload,
    session: AsyncSession = Depends(get_session)
):
    """
    Called when a new Request is created in SharePoint.
    Upserts the Ticket.
    """
    try:
        ticket = await process_request(session, payload.model_dump())
        return {"status": "success", "ticket_id": ticket.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"[SharePoint] Failed to process request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/event", dependencies=[Depends(verify_sharepoint_secret)])
async def append_event(
    payload: EventPayload,
    session: AsyncSession = Depends(get_session)
):
    """
    Called when a new Event (conversation message) is added in SharePoint.
    Appends a Message to the Ticket.
    """
    try:
        msg = await process_event(session, payload.model_dump())
        return {"status": "success", "message_id": msg.id if msg else None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"[SharePoint] Failed to process event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/attachment", dependencies=[Depends(verify_sharepoint_secret)])
async def append_attachment(
    payload: AttachmentPayload,
    session: AsyncSession = Depends(get_session)
):
    """
    Called when an Attachment is uploaded to SharePoint.
    Links the Attachment to the Ticket.
    """
    try:
        att = await process_attachment(session, payload.model_dump())
        return {"status": "success", "attachment_id": att.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"[SharePoint] Failed to process attachment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
