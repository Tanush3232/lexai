"""Add full SharePoint column set to tickets, messages, and attachments

Revision ID: 006
Revises: 005
Create Date: 2026-06-09 00:00:00.000000

Adds every column that maps to a SharePoint list/library field so that
Power Automate payloads can be stored verbatim without data loss.

Tickets table (Requests List):
  + conversation_id  VARCHAR  nullable  indexed
  + entity           VARCHAR  nullable

Messages table (Events List):
  + event_id          VARCHAR  nullable  indexed
  + message_id        VARCHAR  nullable  indexed
  + sender_email      VARCHAR  nullable
  + direction         VARCHAR  nullable
  + has_attachment    BOOLEAN  nullable
  + attachment_names  TEXT     nullable
  + attachment_links  TEXT     nullable
  + to_emails         TEXT     nullable
  + cc_emails         TEXT     nullable
  + bcc_emails        TEXT     nullable

Attachments table (LegalAttachments Library):
  + request_id      VARCHAR   nullable  indexed
  + event_id        VARCHAR   nullable  indexed
  + document_id     VARCHAR   nullable
  + modified_by     VARCHAR   nullable
  + sp_modified_at  DATETIME  nullable
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tickets: add conversation_id and entity ───────────────────────────────
    op.add_column(
        'tickets',
        sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_tickets_conversation_id'), 'tickets', ['conversation_id'], unique=False
    )
    op.add_column(
        'tickets',
        sa.Column('entity', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    # ── messages: add all Events List columns ─────────────────────────────────
    op.add_column(
        'messages',
        sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_messages_event_id'), 'messages', ['event_id'], unique=False
    )
    op.add_column(
        'messages',
        sa.Column('message_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_messages_message_id'), 'messages', ['message_id'], unique=False
    )
    op.add_column(
        'messages',
        sa.Column('sender_email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('direction', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('has_attachment', sa.Boolean(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('attachment_names', sa.Text(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('attachment_links', sa.Text(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('to_emails', sa.Text(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('cc_emails', sa.Text(), nullable=True),
    )
    op.add_column(
        'messages',
        sa.Column('bcc_emails', sa.Text(), nullable=True),
    )

    # ── attachments: add all LegalAttachments Library columns ─────────────────
    op.add_column(
        'attachments',
        sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_attachments_request_id'), 'attachments', ['request_id'], unique=False
    )
    op.add_column(
        'attachments',
        sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_attachments_event_id'), 'attachments', ['event_id'], unique=False
    )
    op.add_column(
        'attachments',
        sa.Column('document_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'attachments',
        sa.Column('modified_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'attachments',
        sa.Column('sp_modified_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # ── attachments ───────────────────────────────────────────────────────────
    op.drop_column('attachments', 'sp_modified_at')
    op.drop_column('attachments', 'modified_by')
    op.drop_column('attachments', 'document_id')
    op.drop_index(op.f('ix_attachments_event_id'), table_name='attachments')
    op.drop_column('attachments', 'event_id')
    op.drop_index(op.f('ix_attachments_request_id'), table_name='attachments')
    op.drop_column('attachments', 'request_id')

    # ── messages ─────────────────────────────────────────────────────────────
    op.drop_column('messages', 'bcc_emails')
    op.drop_column('messages', 'cc_emails')
    op.drop_column('messages', 'to_emails')
    op.drop_column('messages', 'attachment_links')
    op.drop_column('messages', 'attachment_names')
    op.drop_column('messages', 'has_attachment')
    op.drop_column('messages', 'direction')
    op.drop_column('messages', 'sender_email')
    op.drop_index(op.f('ix_messages_message_id'), table_name='messages')
    op.drop_column('messages', 'message_id')
    op.drop_index(op.f('ix_messages_event_id'), table_name='messages')
    op.drop_column('messages', 'event_id')

    # ── tickets ───────────────────────────────────────────────────────────────
    op.drop_column('tickets', 'entity')
    op.drop_index(op.f('ix_tickets_conversation_id'), table_name='tickets')
    op.drop_column('tickets', 'conversation_id')
