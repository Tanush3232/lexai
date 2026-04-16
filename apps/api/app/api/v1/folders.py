"""
Folder management routes
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.folder import Folder, FolderCreate, FolderRead
from app.models.document import Document
from app.services.audit_service import log_action

router = APIRouter()


@router.get("/", response_model=List[FolderRead])
async def list_folders(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(Folder).where(Folder.owner_id == current_user.id))
    folders = result.all()

    # Attach document counts
    folder_reads = []
    for f in folders:
        count_result = await session.exec(
            select(func.count(Document.id)).where(Document.folder_id == f.id)
        )
        count = count_result.first() or 0
        folder_reads.append(FolderRead(**f.dict(), document_count=count))
    return folder_reads


@router.post("/", response_model=FolderRead, status_code=201)
async def create_folder(
    body: FolderCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    folder = Folder(name=body.name, description=body.description, owner_id=current_user.id)
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    await log_action(session, current_user.id, "create", "folder", folder.id)
    return FolderRead(**folder.dict(), document_count=0)


@router.get("/{folder_id}", response_model=FolderRead)
async def get_folder(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(Folder).where(Folder.id == folder_id))
    folder = result.first()
    if not folder or folder.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Folder not found")
    count_result = await session.exec(
        select(func.count(Document.id)).where(Document.folder_id == folder_id)
    )
    count = count_result.first() or 0
    return FolderRead(**folder.dict(), document_count=count)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(Folder).where(Folder.id == folder_id))
    folder = result.first()
    if not folder or folder.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Folder not found")
    await session.delete(folder)
    await session.commit()
    await log_action(session, current_user.id, "delete", "folder", folder_id)
