from sqlalchemy import func, select

from app.models.knowledge import KnowledgeBase
from app.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KnowledgeBase]):
    model = KnowledgeBase

    async def list_by_user(self, user_id: int) -> list[KnowledgeBase]:
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id)
            .order_by(KnowledgeBase.id.desc())
        )
        return list(result.scalars().all())

    async def get_for_user(self, knowledge_id: int, user_id: int) -> KnowledgeBase | None:
        result = await self.session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_id, KnowledgeBase.user_id == user_id
            )
        )
        return result.scalars().first()

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(KnowledgeBase.id)).where(KnowledgeBase.user_id == user_id)
        )
        return int(result.scalar_one())
