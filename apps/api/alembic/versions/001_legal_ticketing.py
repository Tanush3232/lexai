"""add_legal_ticketing_system

Revision ID: 001_legal_ticketing
Revises: 
Create Date: 2026-04-23

Creates the five tables for the Legal Ticketing System:
  - tickets
  - ticket_users  (with unique constraint on ticket_id + user_id)
  - email_threads (with unique constraint on conversation_id)
  - email_logs
  - messages
"""
from alembic import op
import sqlalchemy as sa


# ── Revision identifiers ─────────────────────────────────────────────────────
revision = "001_legal_ticketing"
down_revision = None          # first migration in this project
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tickets ──────────────────────────────────────────────────────────────
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_tickets_created_by_users"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_priority", "tickets", ["priority"])
    op.create_index("ix_tickets_created_by", "tickets", ["created_by"])

    # ── ticket_users ──────────────────────────────────────────────────────────
    op.create_table(
        "ticket_users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_ticket_users_ticket"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ticket_users_user"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_id", "user_id", name="uq_ticket_users_ticket_user"),
    )
    op.create_index("ix_ticket_users_ticket_id", "ticket_users", ["ticket_id"])
    op.create_index("ix_ticket_users_user_id", "ticket_users", ["user_id"])

    # ── email_threads ─────────────────────────────────────────────────────────
    op.create_table(
        "email_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_email_threads_ticket"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_email_threads_conversation_id"),
    )
    op.create_index("ix_email_threads_conversation_id", "email_threads", ["conversation_id"])
    op.create_index("ix_email_threads_ticket_id", "email_threads", ["ticket_id"])

    # ── email_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "email_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("email_metadata", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_email_logs_ticket"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_email_logs_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_logs_ticket_id", "email_logs", ["ticket_id"])
    op.create_index("ix_email_logs_user_id", "email_logs", ["user_id"])

    # ── messages ──────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("sender_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="app"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("email_thread_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_messages_ticket"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], name="fk_messages_sender"),
        sa.ForeignKeyConstraint(["email_thread_id"], ["email_threads.id"], name="fk_messages_email_thread"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_ticket_id", "messages", ["ticket_id"])
    op.create_index("ix_messages_sender_id", "messages", ["sender_id"])
    op.create_index("ix_messages_email_thread_id", "messages", ["email_thread_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("messages")
    op.drop_table("email_logs")
    op.drop_table("email_threads")
    op.drop_table("ticket_users")
    op.drop_table("tickets")
