"""create policy_chunks

Revision ID: 58b04a2f2c67
Revises: a8f1db791405
Create Date: 2026-09-28 11:25:40.480804

The embedding column's width is read from embed_dim in config/rag.yaml so the
config stays the single source of truth. To switch embed models: change
embed_dim, `alembic downgrade -1`, `alembic upgrade head`, then re-run
`python -m copilot.rag.index`.
"""

from collections.abc import Sequence
from pathlib import Path

import yaml
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58b04a2f2c67"
down_revision: str | Sequence[str] | None = "a8f1db791405"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RAG_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "rag.yaml"


def embed_dim() -> int:
    with RAG_CONFIG_PATH.open(encoding="utf-8") as f:
        dim = yaml.safe_load(f)["embed_dim"]
    if not isinstance(dim, int) or dim < 1:
        raise ValueError(f"embed_dim in {RAG_CONFIG_PATH} must be a positive integer")
    return dim


def upgrade() -> None:
    """Create policy_chunks (one row per heading chunk of a policy doc)."""
    # Idempotent: scripts/check_infra.py may already have created it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # No ANN index: a few dozen rows are faster and more exact with a plain scan.
    op.execute(
        f"""
        CREATE TABLE policy_chunks (
            id           SERIAL PRIMARY KEY,
            file         TEXT NOT NULL,
            doc_title    TEXT NOT NULL,
            category     TEXT NOT NULL,
            version      TEXT NOT NULL,
            last_updated DATE NOT NULL,
            heading      TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            chunk_index  INTEGER NOT NULL,
            content      TEXT NOT NULL,
            embedding    vector({embed_dim()}) NOT NULL,
            CONSTRAINT uq_policy_chunks_file_chunk UNIQUE (file, chunk_index)
        )
        """
    )


def downgrade() -> None:
    """Drop policy_chunks; the vector extension is left in place."""
    op.execute("DROP TABLE policy_chunks")
