"""create conversations and messages

Revision ID: f23b8929664c
Revises: 86e0744f5356
Create Date: 2026-09-28 19:30:06.329169

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f23b8929664c"
down_revision: str | Sequence[str] | None = "86e0744f5356"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create conversations and messages: the copilot chat history."""
    op.create_table(
        "conversations",
        # The API's own conversation id, a client-chosen string such as "demo1".
        sa.Column("id", sa.String(), primary_key=True),
        # Nullable: a chat can start before the customer is known.
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        # Free text ("user" or "assistant"), like orders.status: no DB-level enum.
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # JSON lists: tool names in call order, and the file#heading sources the
        # reply cites. User messages simply keep the empty default.
        sa.Column(
            "tools_called",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "sources", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
        sa.Column("escalated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Serves "the last N messages of one conversation, in order".
    op.create_index(
        "ix_messages_conversation_id_id", "messages", ["conversation_id", "id"]
    )


def downgrade() -> None:
    """Drop messages, then conversations (the reverse dependency order)."""
    op.drop_table("messages")
    op.drop_table("conversations")
