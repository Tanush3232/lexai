"""Add SharePoint Ticketing models

Revision ID: 005
Revises: 004
Create Date: 2026-06-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add request_id to tickets
    op.add_column('tickets', sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_tickets_request_id'), 'tickets', ['request_id'], unique=True)

    # 2. Create attachments table
    op.create_table('attachments',
    sa.Column('ticket_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('file_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('file_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('entity', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_attachments_ticket_id'), 'attachments', ['ticket_id'], unique=False)


def downgrade() -> None:
    # 1. Drop attachments table
    op.drop_index(op.f('ix_attachments_ticket_id'), table_name='attachments')
    op.drop_table('attachments')

    # 2. Drop request_id from tickets
    op.drop_index(op.f('ix_tickets_request_id'), table_name='tickets')
    op.drop_column('tickets', 'request_id')
