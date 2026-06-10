from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, SQLModel
from sqlalchemy import func, delete

from app.core.database import get_session
from app.core.auth import get_current_user, hash_password
from app.models.user import User, UserCreate, UserRead, UserBase

router = APIRouter()

class UserUpdate(SQLModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None
    organization: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

@router.get("/", response_model=List[UserRead])
async def get_users(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["ops_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to view users")
    result = await session.execute(select(User))
    users = result.scalars().all()
    return users

@router.post("/", response_model=UserRead)
async def create_user(
    user_in: UserCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["ops_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to create users")
    # Normalize email to lowercase always
    normalized_email = user_in.email.strip().lower()
    result = await session.execute(select(User).where(func.lower(User.email) == normalized_email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User.model_validate(user_in, update={
        "email": normalized_email,
        "hashed_password": hash_password(user_in.password),
    })
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    user_in: UserUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["ops_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to update users")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = user_in.model_dump(exclude_unset=True)
    # If email is being updated, normalize it to lowercase
    if "email" in update_data and update_data["email"]:
        update_data["email"] = update_data["email"].strip().lower()
    user.sqlmodel_update(update_data)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["ops_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete users")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent a user from deleting themselves if needed, but not strictly required if admin.
    if current_user.id == user_id:
         raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    
    try:
        from app.models.folder import Folder
        from app.models.document import Document
        from app.models.chat import ChatSession
        from app.models.draft import ContractDraft
        from app.models.translation import TranslationJob
        from app.models.audit import AuditLog
        
        # We need to manually delete associated records due to foreign keys.
        
        # 1. Documents 
        # Properly delete documents to clean up MinIO and Vector Stores, and avoid FK constraints on Folders.
        docs = await session.execute(select(Document).where(Document.owner_id == user_id))
        doc_list = docs.scalars().all()
        
        # In a real heavy-duty production system, we might queue this deletion asynchronously. 
        # Here we perform them sequentially using the logic adapted from documents.py
        from app.core.storage import delete_file
        from app.core.vector_store import delete_document_clauses
        from app.core.elasticsearch import delete_document_clauses_es
        from app.core.graph_db import delete_document_graph
        from app.models.clause import Clause
        from app.models.document import DocumentChunk, DocumentTree
        
        for doc in doc_list:
            try: await delete_file(doc.storage_key)
            except: pass
            try: await delete_document_clauses(doc.id)
            except: pass
            try: await delete_document_clauses_es(doc.id)
            except: pass
            try: delete_document_graph(doc.id)
            except: pass
            
            # Sub-document DB cleanups
            await session.execute(delete(TranslationJob).where(TranslationJob.document_id == doc.id))
            await session.execute(delete(Clause).where(Clause.doc_id == doc.id))
            await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            await session.execute(delete(DocumentTree).where(DocumentTree.document_id == doc.id))
            await session.delete(doc)
            
        # 2. Translations (not tied to specific documents might still exist)
        await session.execute(delete(TranslationJob).where(TranslationJob.user_id == user_id))

        # 3. Chat Sessions Details
        from app.models.chat import ChatMessage
        chats = await session.execute(select(ChatSession).where(ChatSession.user_id == user_id))
        chat_list = chats.scalars().all()
        for chat in chat_list:
             await session.execute(delete(ChatMessage).where(ChatMessage.session_id == chat.id))
             await session.delete(chat)
        
        # 4. Drafts
        await session.execute(delete(ContractDraft).where(ContractDraft.user_id == user_id))
        # Note: Setting approved_by = None might be safer than deleting a draft approved by the user 
        # if the draft was created by someone else, but for simplicity we drop or nullify.
        # Let's just nullify the approved_by field instead of deleting someone else's draft.
        await session.execute(
            ContractDraft.__table__.update()
            .where(ContractDraft.approved_by == user_id)
            .values(approved_by=None)
        )
        
        # 5. Folders
        await session.execute(delete(Folder).where(Folder.owner_id == user_id))
        
        # 6. Audit Logs
        await session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))

        # 7. Web Search — citations first (FK → sessions), then sessions (FK → users)
        from app.models.web_search import WebSearchSession, WebSearchCitation
        ws_sessions = await session.execute(
            select(WebSearchSession).where(WebSearchSession.user_id == user_id)
        )
        for ws in ws_sessions.scalars().all():
            await session.execute(
                delete(WebSearchCitation).where(WebSearchCitation.session_id == ws.id)
            )
            await session.delete(ws)

        # 8. Ticket participations (TicketUser rows where this user is assignee/watcher)
        from app.models.ticket import TicketUser
        await session.execute(delete(TicketUser).where(TicketUser.user_id == user_id))

        # 9. Finally, delete the user
        await session.delete(user)
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {str(e)}")
    
    return None
