"""
enterprise_contract_drafting_v2

Adds:
  - draft_sessions table (orchestration state per drafting flow)
  - contract_drafts.session_id (link back to session)
  - contract_drafts.blocks_json (block-based content)
  - contract_drafts.sources_json (approved research sources)
  - contract_drafts.export_path (MinIO .docx path)

Revision ID: 003
Revises: 002_web_search
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002_web_search"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. New draft_sessions table ──────────────────────────────────────────
    op.create_table(
        "draft_sessions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("original_prompt", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False, server_default="analyzing_intent"),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("redaction_map_json", sa.Text(), nullable=True),
        sa.Column("intent_json", sa.Text(), nullable=True),
        sa.Column("sources_json", sa.Text(), nullable=True),
        sa.Column("approved_source_ids_json", sa.Text(), nullable=True),
        sa.Column("questions_json", sa.Text(), nullable=True),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ── 2. Add new columns to contract_drafts ────────────────────────────────
    op.add_column("contract_drafts", sa.Column("session_id", sa.String(), nullable=True))
    op.add_column("contract_drafts", sa.Column("blocks_json", sa.Text(), nullable=True))
    op.add_column("contract_drafts", sa.Column("sources_json", sa.Text(), nullable=True))
    op.add_column("contract_drafts", sa.Column("export_path", sa.String(), nullable=True))


def downgrade():
    op.drop_column("contract_drafts", "export_path")
    op.drop_column("contract_drafts", "sources_json")
    op.drop_column("contract_drafts", "blocks_json")
    op.drop_column("contract_drafts", "session_id")
    op.drop_table("draft_sessions")
