"""
Long-term memory: persists conversation history in Postgres so it
survives server restarts. Same interface as ShortTermMemory, so the
API layer can swap between them without other code changes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.models import ConversationMessage


class LongTermMemory:
    async def get_history(self, session_id: str, session: AsyncSession) -> list[dict]:
        """Returns all messages for a session, oldest first."""
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.created_at)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [{"role": r.role, "content": r.content} for r in rows]

    async def extend(self, session_id: str, messages: list[dict], session: AsyncSession) -> None:
        """Adds multiple messages at once."""
        for m in messages:
            row = ConversationMessage(
                session_id=session_id,
                role=m.get("role", "unknown"),
                content=m.get("content"),
            )
            session.add(row)
        await session.commit()

    async def clear(self, session_id: str, session: AsyncSession) -> None:
        """Deletes all messages for a session."""
        stmt = select(ConversationMessage).where(ConversationMessage.session_id == session_id)
        result = await session.execute(stmt)
        for row in result.scalars().all():
            await session.delete(row)
        await session.commit()


memory = LongTermMemory()