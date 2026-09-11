from sqlalchemy import select

from app.models.memory import Memory
from app.repositories.base import BaseRepository


class MemoryRepository(BaseRepository[Memory]):
    model = Memory

    async def list_by_user(self, user_id: int, character_id: int | None = None) -> list[Memory]:
        statement = select(Memory).where(Memory.user_id == user_id)
        if character_id is not None:
            statement = statement.where(Memory.character_id == character_id)
        statement = statement.order_by(Memory.id.desc())
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_for_user(self, memory_id: int, user_id: int) -> Memory | None:
        result = await self.session.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )
        return result.scalars().first()
