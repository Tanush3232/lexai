"""
sharepoint.py — SharePoint + Power Automate Webhook Endpoints

Provides three HTTP POST endpoints for Power Automate to call when SharePoint
list items or library documents change:

  POST /api/v1/sharepoint/request      ← Requests List
  POST /api/v1/sharepoint/event        ← Events List
  POST /api/v1/sharepoint/attachment   ← LegalAttachments Library

Security: Every request must carry the header:
  X-SharePoint-Token: <SHAREPOINT_WEBHOOK_SECRET | GRAPH_WEBHOOK_SECRET>

Payload field names mirror the SharePoint column internal names exactly to
minimise transformation effort in Power Automate flows.
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.database import get_session
from app.services.sharepoint_ingestion_service import (
    process_request,
    process_event,
    process_attachment,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ─── Security ─────────────────────────────────────────────────────────────────

def verify_sharepoint_secret(x_sharepoint_token: Optional[str] = Header(None)) -> None:
    """
    Shared-secret guard applied to every endpoint in this router.
    Uses SHAREPOINT_WEBHOOK_SECRET if set; falls back to GRAPH_WEBHOOK_SECRET
    for backward compatibility with existing env files.
    Raises 503 if no secret is configured (fail-safe — never open by default).
    """
    expected = getattr(settings, "SHAREPOINT_WEBHOOK_SECRET", None) or settings.GRAPH_WEBHOOK_SECRET

    if not expected:
        log.critical("[SharePoint] No webhook secret configured — endpoint disabled for safety.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret not configured on this server.",
        )

    if not x_sharepoint_token or x_sharepoint_token != expected:
        log.warning("[SharePoint] Rejected unauthorised webhook call.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-SharePoint-Token header.",
        )


# ─── Request Payload (Requests List) ──────────────────────────────────────────
# SharePoint columns: RequestID, ConversationID, Entity, Subject

class RequestPayload(BaseModel):
    """
    Mirrors the SharePoint Requests List columns exactly.
    Power Automate should map SP column values to these fields with no renaming.
    """
    requestId: str = Field(..., description="SharePoint Requests List → RequestID")
    subject: str = Field(..., description="SharePoint Requests List → Subject")
    conversationId: Optional[str] = Field(None, description="SharePoint Requests List → ConversationID")
    entity: Optional[str] = Field(None, description="SharePoint Requests List → Entity")

    @field_validator("requestId", "subject", mode="before")
    @classmethod
    def must_not_be_blank(cls, v: str, info) -> str:  # noqa: N805
        if not str(v).strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return str(v).strip()


# ─── Event Payload (Events List) ──────────────────────────────────────────────
# SharePoint columns: RequestID, EventID, MessageID, Timestamp, Sender, Body,
#                     Direction, HasAttachment, AttachmentNames, AttachmentLinks,
#                     ToEmails, CCEmails, BccEmails

class EventPayload(BaseModel):
    """
    Mirrors the SharePoint Events List columns exactly.
    """
    requestId: str = Field(..., description="SharePoint Events List → RequestID")
    eventId: Optional[str] = Field(None, description="SharePoint Events List → EventID")
    messageId: Optional[str] = Field(None, description="SharePoint Events List → MessageID")
    timestamp: str = Field(..., description="SharePoint Events List → Timestamp (ISO-8601)")
    sender: str = Field(..., description="SharePoint Events List → Sender (email address)")
    body: str = Field(..., description="SharePoint Events List → Body")
    direction: Optional[str] = Field(
        None, description="SharePoint Events List → Direction ('inbound' | 'outbound')"
    )
    hasAttachment: Optional[bool] = Field(None, description="SharePoint Events List → HasAttachment")
    attachmentNames: Optional[str] = Field(
        None, description="SharePoint Events List → AttachmentNames (pipe-separated)"
    )
    attachmentLinks: Optional[str] = Field(
        None, description="SharePoint Events List → AttachmentLinks (pipe-separated URLs)"
    )
    toEmails: Optional[str] = Field(
        None, description="SharePoint Events List → ToEmails (pipe-separated)"
    )
    ccEmails: Optional[str] = Field(
        None, description="SharePoint Events List → CCEmails (pipe-separated)"
    )
    bccEmails: Optional[str] = Field(
        None, description="SharePoint Events List → BccEmails (pipe-separated)"
    )

    @field_validator("requestId", "sender", "timestamp", mode="before")
    @classmethod
    def must_not_be_blank(cls, v: str, info) -> str:  # noqa: N805
        if not str(v).strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return str(v).strip()


# ─── Attachment Payload (LegalAttachments Library) ────────────────────────────
# SharePoint columns: Name, Modified, Modified By, RequestID, EventID,
#                     DocumentID, Entity

class AttachmentPayload(BaseModel):
    """
    Mirrors the SharePoint LegalAttachments document library columns exactly.
    """
    requestId: str = Field(..., description="SharePoint LegalAttachments → RequestID")
    fileName: str = Field(..., description="SharePoint LegalAttachments → Name (filename with ext)")
    fileUrl: str = Field(..., description="Direct SharePoint link to the document")
    eventId: Optional[str] = Field(None, description="SharePoint LegalAttachments → EventID")
    documentId: Optional[str] = Field(None, description="SharePoint LegalAttachments → DocumentID")
    entity: Optional[str] = Field(None, description="SharePoint LegalAttachments → Entity")
    modifiedBy: Optional[str] = Field(None, description="SharePoint LegalAttachments → Modified By")
    spModifiedAt: Optional[str] = Field(
        None, description="SharePoint LegalAttachments → Modified (ISO-8601 timestamp)"
    )

    @field_validator("requestId", "fileName", "fileUrl", mode="before")
    @classmethod
    def must_not_be_blank(cls, v: str, info) -> str:  # noqa: N805
        if not str(v).strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return str(v).strip()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/request",
    summary="Upsert a legal request from SharePoint",
    dependencies=[Depends(verify_sharepoint_secret)],
    status_code=200,
)
async def create_or_update_request(
    payload: RequestPayload,
    session: AsyncSession = Depends(get_session),
):
    """
    Called by Power Automate when an item is created or modified
    in the SharePoint **Requests List**.

    Performs an UPSERT keyed on `requestId`:
    - First call → creates a new Ticket
    - Subsequent calls with same requestId → updates the existing Ticket
    """
    try:
        ticket = await process_request(session, payload.model_dump())
        return {"status": "success", "ticket_id": ticket.id, "request_id": ticket.request_id}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.error("[SharePoint] /request failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post(
    "/event",
    summary="Append a conversation event from SharePoint Events List",
    dependencies=[Depends(verify_sharepoint_secret)],
    status_code=200,
)
async def append_event(
    payload: EventPayload,
    session: AsyncSession = Depends(get_session),
):
    """
    Called by Power Automate when a new item is added to the
    SharePoint **Events List**.

    Appends a `Message` to the associated Ticket. Idempotent:
    duplicate events (same EventID or same content+timestamp) are silently dropped.
    """
    try:
        msg = await process_event(session, payload.model_dump())
        return {
            "status": "success",
            "message_id": msg.id if msg else None,
            "event_id": msg.event_id if msg else None,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.error("[SharePoint] /event failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post(
    "/attachment",
    summary="Link a document from SharePoint LegalAttachments Library",
    dependencies=[Depends(verify_sharepoint_secret)],
    status_code=200,
)
async def append_attachment(
    payload: AttachmentPayload,
    session: AsyncSession = Depends(get_session),
):
    """
    Called by Power Automate when a document is uploaded to the
    SharePoint **LegalAttachments** document library.

    Associates the file with the correct Ticket. Idempotent:
    duplicate file URLs are silently dropped.
    """
    try:
        att = await process_attachment(session, payload.model_dump())
        return {
            "status": "success",
            "attachment_id": att.id,
            "document_id": att.document_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.error("[SharePoint] /attachment failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
