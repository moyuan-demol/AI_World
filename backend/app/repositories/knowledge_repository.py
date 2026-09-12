from datetime import datetime

from sqlalchemy import delete, func, select, update

from app.models.knowledge import KnowledgeBase
from app.repositories.base import BaseRepository


def _expand_tree(bases: list[KnowledgeBase], root_ids: list[int]) -> list[int]:
    """把给定节点扩展为「自身 + 全部后代」（内存展开，无需递归 SQL）。"""
    children: dict[int | None, list[int]] = {}
    for base in bases:
        children.setdefault(base.parent_id, []).append(base.id)
    result: list[int] = []
    seen: set[int] = set()
    stack = list(root_ids)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        stack.extend(children.get(current, []))
    return result


class KnowledgeRepository(BaseRepository[KnowledgeBase]):
    model = KnowledgeBase

    def _active(self):  # noqa: ANN201
        """所有"未删除"查询的公共条件（回收站里的记录绝不能出现在正常列表里）。"""
        return KnowledgeBase.deleted_at.is_(None)

    async def list_by_user(self, user_id: int) -> list[KnowledgeBase]:
        """我自己的（未删除、非公共）知识库。

        为什么把公共库排除在"我的知识库"之外：公共库归属站长账号，
        若混进个人列表，普通用户会以为是自己创建的而误删/误改；
        公共库统一走 list_public()，在「知识世界」里单独展示。
        """
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.user_id == user_id,
                self._active(),
                KnowledgeBase.is_public.is_(False),
            )
            .order_by(KnowledgeBase.id.desc())
        )
        return list(result.scalars().all())

    async def list_public(self) -> list[KnowledgeBase]:
        """全部未删除的公共库（归属站长，但所有人可检索）。"""
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(self._active(), KnowledgeBase.is_public.is_(True))
            .order_by(KnowledgeBase.id.asc())
        )
        return list(result.scalars().all())

    async def list_public_ids(self) -> list[int]:
        result = await self.session.execute(
            select(KnowledgeBase.id).where(
                self._active(), KnowledgeBase.is_public.is_(True)
            )
        )
        return [int(item) for item in result.scalars().all()]

    async def get_for_user(self, knowledge_id: int, user_id: int) -> KnowledgeBase | None:
        """取"我的"某个未删除知识库（不排除公共：站长要能维护自己的公共库）。

        已删除的记录对普通业务path 一律不可见，因此这里过滤 deleted_at。
        """
        result = await self.session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_id,
                KnowledgeBase.user_id == user_id,
                self._active(),
            )
        )
        return result.scalars().first()

    async def list_subtree_ids(
        self, root_ids: list[int], *, include_deleted: bool = False
    ) -> list[int]:
        """全局子树展开（不按 user 过滤），用于公共库与回收站场景。

        - 检索 / 删除用 include_deleted=False：只作用于还活着的节点；
        - 恢复 / 彻底删除用 include_deleted=True：回收站里的子节点也必须被一起处理，
          否则会出现"父级回来了、子级却永远留在回收站"的孤儿。
        """
        if not root_ids:
            return []
        statement = select(KnowledgeBase)
        if not include_deleted:
            statement = statement.where(self._active())
        bases = list((await self.session.execute(statement)).scalars().all())
        return _expand_tree(bases, list(root_ids))

    async def get_public_by_name(self, name: str) -> KnowledgeBase | None:
        """按名字找公共库（**含已删除**）：幂等种子数据靠它判重。"""
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.name == name, KnowledgeBase.is_public.is_(True))
            .order_by(KnowledgeBase.id.asc())
        )
        return result.scalars().first()

    async def list_by_ids(self, knowledge_ids: list[int]) -> list[KnowledgeBase]:
        """按 id 批量取回（含已删除），用于把回收站条目的归属知识库名字带出来。"""
        if not knowledge_ids:
            return []
        result = await self.session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id.in_(list(knowledge_ids)))
        )
        return list(result.scalars().all())

    async def list_deleted_for_user(self, user_id: int) -> list[KnowledgeBase]:
        """我自己的回收站条目（含公共？不含 —— 公共条目单独由 list_deleted_public 补）。"""
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.user_id == user_id,
                KnowledgeBase.deleted_at.is_not(None),
            )
            .order_by(KnowledgeBase.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def list_deleted_public(self) -> list[KnowledgeBase]:
        """已被删进回收站的公共库（任何登录用户都能看到并恢复它们）。"""
        result = await self.session.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.is_public.is_(True),
                KnowledgeBase.deleted_at.is_not(None),
            )
            .order_by(KnowledgeBase.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def soft_delete_by_ids(self, knowledge_ids: list[int], deleted_at: datetime) -> int:
        """一条 UPDATE 把多个节点打上删除时间（整棵子树，避免 N 次往返）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id.in_(knowledge_ids))
            .values(deleted_at=deleted_at)
        )
        return int(result.rowcount or 0)

    async def restore_by_ids(self, knowledge_ids: list[int]) -> int:
        """恢复：把 deleted_at 置空（一条 UPDATE）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id.in_(knowledge_ids))
            .values(deleted_at=None)
        )
        return int(result.rowcount or 0)

    async def delete_by_ids(self, knowledge_ids: list[int]) -> int:
        """物理删除多个节点（仅在"彻底删除"时使用）。"""
        if not knowledge_ids:
            return 0
        result = await self.session.execute(
            delete(KnowledgeBase).where(KnowledgeBase.id.in_(knowledge_ids))
        )
        return int(result.rowcount or 0)

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(KnowledgeBase.id)).where(
                KnowledgeBase.user_id == user_id,
                self._active(),
                KnowledgeBase.is_public.is_(False),
            )
        )
        return int(result.scalar_one())
