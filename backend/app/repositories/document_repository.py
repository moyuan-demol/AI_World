from sqlalchemy import delete, func, select

from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    async def list_by_knowledge(self, knowledge_id: int) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.knowledge_id == knowledge_id)
            .order_by(Document.chunk_index.asc())
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: int) -> list[Document]:
        result = await self.session.execute(
            select(Document).where(Document.user_id == user_id).order_by(Document.id.desc())
        )
        return list(result.scalars().all())

    async def list_by_knowledge_ids(self, knowledge_ids: list[int]) -> list[Document]:
        if not knowledge_ids:
            return []
        result = await self.session.execute(
            select(Document).where(Document.knowledge_id.in_(knowledge_ids))
        )
        return list(result.scalars().all())

    async def count_by_knowledge(self, knowledge_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Document.id)).where(Document.knowledge_id == knowledge_id)
        )
        return int(result.scalar_one())

    async def count_by_knowledge_ids(self, knowledge_ids: list[int]) -> int:
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            select(func.count(Document.id)).where(Document.knowledge_id.in_(knowledge_ids))
        )
        return int(result.scalar_one())

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Document.id)).where(Document.user_id == user_id)
        )
        return int(result.scalar_one())

    async def count_map(self, knowledge_ids: list[int]) -> dict[int, int]:
        """一次聚合查询取回所有知识库的切片数（替代 N 次单查）。"""
        if not knowledge_ids:
            return {}
        result = await self.session.execute(
            select(Document.knowledge_id, func.count(Document.id))
            .where(Document.knowledge_id.in_(knowledge_ids))
            .group_by(Document.knowledge_id)
        )
        return {int(kid): int(count) for kid, count in result.all()}

    async def delete_by_knowledge(self, knowledge_id: int) -> int:
        result = await self.session.execute(
            delete(Document).where(Document.knowledge_id == knowledge_id)
        )
        return int(result.rowcount or 0)

    async def delete_by_knowledge_ids(self, knowledge_ids: list[int]) -> int:
        """批量删除多个知识库下的全部切片（一条语句）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            delete(Document).where(Document.knowledge_id.in_(knowledge_ids))
        )
        return int(result.rowcount or 0)
