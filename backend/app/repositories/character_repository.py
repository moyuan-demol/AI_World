from sqlalchemy import func, select

from app.models.character import Character
from app.repositories.base import BaseRepository


class CharacterRepository(BaseRepository[Character]):
    model = Character

    async def list_by_user(self, user_id: int) -> list[Character]:
        result = await self.session.execute(
            select(Character).where(Character.user_id == user_id).order_by(Character.id.desc())
        )
        return list(result.scalars().all())

    async def get_for_user(self, character_id: int, user_id: int) -> Character | None:
        """Ownership aware lookup: a user can only read their own characters."""
        result = await self.session.execute(
            select(Character).where(Character.id == character_id, Character.user_id == user_id)
        )
        return result.scalars().first()

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Character.id)).where(Character.user_id == user_id)
        )
        return int(result.scalar_one())
