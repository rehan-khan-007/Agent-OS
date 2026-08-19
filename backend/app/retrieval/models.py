"""
Database model for storing document chunks and their embeddings.
Uses pgvector's Vector type for similarity search in PostgreSQL.
"""

from sqlalchemy import Column, Integer, String, Text
from pgvector.sqlalchemy import Vector

from app.database import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(Vector(1536))