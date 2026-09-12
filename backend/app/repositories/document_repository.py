from datetime import datetime

from sqlalchemy import delete, func, select, update

from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def _active(self):  # noqa: ANN201
        """所有"未删除"查询的公共条件（回收站里的切片绝不能被检索/计数）。"""
        return Document.deleted_at.is_(None)

    async def list_by_knowledge(self, knowledge_id: int) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.knowledge_id == knowledge_id, self._active())
            .order_by(Document.chunk_index.asc())
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: int) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.user_id == user_id, self._active())
            .order_by(Document.id.desc())
        )
        return list(result.scalars().all())

    async def list_by_knowledge_ids(self, knowledge_ids: list[int]) -> list[Document]:
        if not knowledge_ids:
            return []
        result = await self.session.execute(
            select(Document).where(
                Document.knowledge_id.in_(knowledge_ids), self._active()
            )
        )
        return list(result.scalars().all())

    async def count_by_knowledge(self, knowledge_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Document.id)).where(
                Document.knowledge_id == knowledge_id, self._active()
            )
        )
        return int(result.scalar_one())

    async def exists_filename(self, knowledge_id: int, filename: str) -> bool:
        """某知识库下是否已有同名文档（**含已删除**），用于幂等种子数据判重。"""
        result = await self.session.execute(
            select(func.count(Document.id)).where(
                Document.knowledge_id == knowledge_id, Document.filename == filename
            )
        )
        return int(result.scalar_one()) > 0

    async def list_by_file(
        self, knowledge_id: int, filename: str, *, include_deleted: bool = False
    ) -> list[Document]:
        """按 (知识库, 文件名) 取回整篇文档的全部切片（可含回收站）。

        公共示例库的"版本化替换"需要同时看到活着的切片（判断版本指纹）和
        回收站里的切片（判断该文档是否被有意删除），因此把两种视角合并成一个方法，
        避免调用方各自拼查询条件。
        """
        conditions = [Document.knowledge_id == knowledge_id, Document.filename == filename]
        if not include_deleted:
            conditions.append(self._active())
        result = await self.session.execute(
            select(Document).where(*conditions).order_by(Document.chunk_index.asc())
        )
        return list(result.scalars().all())

    async def count_by_knowledge_ids(self, knowledge_ids: list[int]) -> int:
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            select(func.count(Document.id)).where(
                Document.knowledge_id.in_(knowledge_ids), self._active()
            )
        )
        return int(result.scalar_one())

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Document.id)).where(
                Document.user_id == user_id, self._active()
            )
        )
        return int(result.scalar_one())

    async def count_map(self, knowledge_ids: list[int]) -> dict[int, int]:
        """一次聚合查询取回所有知识库的切片数（替代 N 次单查）。"""
        if not knowledge_ids:
            return {}
        result = await self.session.execute(
            select(Document.knowledge_id, func.count(Document.id))
            .where(Document.knowledge_id.in_(knowledge_ids), self._active())
            .group_by(Document.knowledge_id)
        )
        return {int(kid): int(count) for kid, count in result.all()}

    # ---- 回收站（软删除 / 恢复） --------------------------------------- #
    async def soft_delete_by_knowledge_ids(
        self, knowledge_ids: list[int], deleted_at: datetime
    ) -> int:
        """一条 UPDATE 把多个知识库下的切片全部打上删除时间。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            update(Document)
            .where(Document.knowledge_id.in_(knowledge_ids), self._active())
            .values(deleted_at=deleted_at)
        )
        return int(result.rowcount or 0)

    async def restore_by_knowledge_ids(self, knowledge_ids: list[int]) -> int:
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            update(Document)
            .where(Document.knowledge_id.in_(knowledge_ids))
            .values(deleted_at=None)
        )
        return int(result.rowcount or 0)

    async def restore_by_file(self, knowledge_id: int, filename: str) -> int:
        """按 (知识库, 文件名) 恢复整篇文档的全部切片。"""
        result = await self.session.execute(
            update(Document)
            .where(Document.knowledge_id == knowledge_id, Document.filename == filename)
            .values(deleted_at=None)
        )
        return int(result.rowcount or 0)

    async def list_deleted_by_user(self, user_id: int) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.user_id == user_id, Document.deleted_at.is_not(None))
            .order_by(Document.deleted_at.desc(), Document.id.asc())
        )
        return list(result.scalars().all())

    async def list_deleted_by_knowledge_ids(self, knowledge_ids: list[int]) -> list[Document]:
        if not knowledge_ids:
            return []
        result = await self.session.execute(
            select(Document)
            .where(
                Document.knowledge_id.in_(knowledge_ids),
                Document.deleted_at.is_not(None),
            )
            .order_by(Document.id.asc())
        )
        return list(result.scalars().all())

    # ---- 物理删除（仅在"彻底删除"时使用） ------------------------------- #
    async def delete_by_knowledge(self, knowledge_id: int) -> int:
        result = await self.session.execute(
            delete(Document).where(Document.knowledge_id == knowledge_id)
        )
        return int(result.rowcount or 0)

    async def delete_by_knowledge_ids(self, knowledge_ids: list[int]) -> int:
        """批量物理删除多个知识库下的全部切片（一条语句）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            delete(Document).where(Document.knowledge_id.in_(knowledge_ids))
        )
        return int(result.rowcount or 0)

    async def delete_by_file(self, knowledge_id: int, filename: str) -> int:
        result = await self.session.execute(
            delete(Document).where(
                Document.knowledge_id == knowledge_id, Document.filename == filename
            )
        )
        return int(result.rowcount or 0)
