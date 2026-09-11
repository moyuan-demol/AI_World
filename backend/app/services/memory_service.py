"""Long term memory service (phase 2 schema, API live from phase 1)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.memory import Memory
from app.repositories.memory_repository import MemoryRepository

MEMORY_TYPES = {"fact", "preference", "project", "history"}


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.memories = MemoryRepository(session)

    async def create(
        self,
        user_id: int,
        *,
        content: str,
        memory_type: str = "fact",
        character_id: int | None = None,
    ) -> Memory:
        memory = await self.memories.create(
            user_id=user_id,
            character_id=character_id,
            memory_type=memory_type if memory_type in MEMORY_TYPES else "fact",
            content=content,
        )
        await self.session.commit()
        return memory

    async def list(self, user_id: int, character_id: int | None = None) -> list[Memory]:
        return await self.memories.list_by_user(user_id, character_id=character_id)

    async def delete(self, user_id: int, memory_id: int) -> None:
        memory = await self.memories.get_for_user(memory_id, user_id)
        if memory is None:
            raise NotFoundError("记忆不存在或无权访问")
        await self.memories.delete(memory)
        await self.session.commit()

    async def recent_context(self, user_id: int, character_id: int | None, limit: int = 5) -> str:
        """Small helper used to inject memories into a chat prompt later."""
        memories = await self.memories.list_by_user(user_id, character_id=character_id)
        if not memories:
            return ""
        lines = ["以下是关于用户的长期记忆："]
        for memory in memories[:limit]:
            lines.append("- [" + memory.memory_type + "] " + memory.content)
        return "\n".join(lines)
