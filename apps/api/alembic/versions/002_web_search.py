"""add_web_search_feature

Revision ID: 002_web_search
Revises: 001_legal_ticketing
Create Date: 2026-05-13

Creates two tables for the Web Search feature:
  - web_search_sessions   (session metadata, multi-turn query storage, reasoning steps)
  - web_search_citations  (per-source citations with turn_index for multi-turn association)
"""
from alembic import op
import sqlalchemy as sa


# ── Revision identifiers ─────────────────────────────────────────────────────
revision = "002_web_search"
down_revision = "001_legal_ticketing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── web_search_sessions ───────────────────────────────────────────────────
    op.create_table(
        "web_search_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="fast"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("answer_summary", sa.Text(), nullable=True),
        sa.Column("full_answer", sa.Text(), nullable=True),
        sa.Column("reasoning_steps", sa.Text(), nullable=True),   # JSON array
        sa.Column("search_plan", sa.Text(), nullable=True),       # JSON
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_web_search_sessions_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_search_sessions_user_id", "web_search_sessions", ["user_id"])

    # ── web_search_citations ──────────────────────────────────────────────────
    op.create_table(
        "web_search_citations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(), nullable=False, server_default=""),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("jurisdiction", sa.String(), nullable=True),
        sa.Column("citation_type", sa.String(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["web_search_sessions.id"], name="fk_web_search_citations_session"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_search_citations_session_id", "web_search_citations", ["session_id"])


def downgrade() -> None:
    op.drop_table("web_search_citations")
    op.drop_table("web_search_sessions")
