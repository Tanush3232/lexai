"""Add is_global and parent_id for Global Vault and nested folders

Revision ID: 007
Revises: 006
Create Date: 2026-09-11 14:00:00.000000

Adds:
  - folders.is_global (BOOLEAN, default FALSE)
  - folders.parent_id (VARCHAR, foreign key to folders.id, nullable, indexed)
  - documents.is_global (BOOLEAN, default FALSE)
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── folders: add is_global and parent_id ──────────────────────────────────
    op.add_column(
        'folders',
        sa.Column('is_global', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column(
        'folders',
        sa.Column('parent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_foreign_key(
        'fk_folders_parent_id', 'folders', 'folders', ['parent_id'], ['id']
    )
    op.create_index(
        op.f('ix_folders_parent_id'), 'folders', ['parent_id'], unique=False
    )

    # ── documents: add is_global ──────────────────────────────────────────────
    op.add_column(
        'documents',
        sa.Column('is_global', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('documents', 'is_global')
    op.drop_index(op.f('ix_folders_parent_id'), table_name='folders')
    op.drop_constraint('fk_folders_parent_id', 'folders', type_='foreignkey')
    op.drop_column('folders', 'parent_id')
    op.drop_column('folders', 'is_global')
