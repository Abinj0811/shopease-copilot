"""create escalations

Revision ID: 86e0744f5356
Revises: 58b04a2f2c67
Create Date: 2026-09-28 13:09:17.064070

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "86e0744f5356"
down_revision: str | Sequence[str] | None = "58b04a2f2c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create escalations: handoff requests from the copilot to a human agent."""
    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Nullable: an escalation must be recordable even when the caller does
        # not know which customer is chatting.
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True
        ),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_escalations_customer_id", "escalations", ["customer_id"])


def downgrade() -> None:
    """Drop escalations."""
    op.drop_table("escalations")
