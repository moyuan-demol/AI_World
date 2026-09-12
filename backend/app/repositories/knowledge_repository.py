from sqlalchemy import delete, func, select

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

    async def list_descendant_ids(self, user_id: int, root_ids: list[int]) -> list[int]:
        """把给定节点扩展为「自身 + 全部后代」。

        用途：给角色绑定一个文件夹时自动包含其下所有子库；检索范围据此展开。
        数据来源限定在该用户自己的知识库内（内存展开，无需递归 SQL）。
        """
        bases = await self.list_by_user(user_id)
        owned = {base.id for base in bases}
        children: dict[int | None, list[int]] = {}
        for base in bases:
            children.setdefault(base.parent_id, []).append(base.id)

        result: list[int] = []
        seen: set[int] = set()
        stack = [item for item in root_ids if item in owned]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            result.append(current)
            stack.extend(children.get(current, []))
        return result

    async def delete_by_ids(self, knowledge_ids: list[int]) -> int:
        """一条语句删除多个节点（用于级联删除整棵子树）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            delete(KnowledgeBase).where(KnowledgeBase.id.in_(knowledge_ids))
        )
        return int(result.rowcount or 0)

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(KnowledgeBase.id)).where(KnowledgeBase.user_id == user_id)
        )
        return int(result.scalar_one())
