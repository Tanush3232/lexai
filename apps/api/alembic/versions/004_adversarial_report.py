"""
004_adversarial_report.py
=========================
Adds adversarial_report_json column to contract_drafts table.
Phase 6.5: stores the red-team findings JSON from the adversarial Claude Sonnet agent.
Zero risk — nullable TEXT column, backward compatible with all existing rows.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contract_drafts",
        sa.Column("adversarial_report_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contract_drafts", "adversarial_report_json")
