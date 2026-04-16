"""
Audit log routes
"""
from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.audit import AuditLog, AuditLogRead

router = APIRouter()


@router.get("/", response_model=List[AuditLogRead])
async def get_audit_logs(
    resource_type: str = Query(None),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get audit logs for current user. Admins can see all."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

    if current_user.role not in ["ops_admin", "super_admin"]:
        query = query.where(AuditLog.user_id == current_user.id)

    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)

    result = await session.exec(query)
    return result.all()
