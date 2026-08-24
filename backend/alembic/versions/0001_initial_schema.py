"""Initial schema: document_chunks, conversation_messages, pgvector HNSW index

This migration reflects the actual current production schema as of
Aug 24, 2026 — collapsing what was originally two separate real
events (table creation, then the HNSW index added later via
scripts/add_vector_index.py) into one migration for simplicity, since
this is meant as Alembic's starting baseline, not a literal replay of
history.

Revision ID: 0001
Revises:
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536)),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversation_messages_session_id",
        "conversation_messages",
        ["session_id"],
    )

    # Matches scripts/add_vector_index.py exactly — see that script's
    # docstring for why HNSW (vs. IVFFlat) was chosen.
    op.execute(
        "CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("document_chunks_embedding_hnsw_idx", table_name="document_chunks")
    op.drop_index("ix_conversation_messages_session_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_table("document_chunks")
