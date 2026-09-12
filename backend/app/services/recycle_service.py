"""回收站：软删除条目的列出 / 恢复 / 彻底删除（Repository + Service 能力）。

设计要点：
- 删除是软删除（deleted_at），**恢复**只是把 deleted_at 置空，数据没有任何损失；
- **彻底删除**才是物理 DELETE，因此权限收得更紧：
  普通用户只能处理自己删除的条目，公共库内容只有站长能彻底删除
  （方案 1：普通用户对公共内容只能软删除 / 恢复）。
- 公共库归属站长账号，但"谁把它删进回收站"不额外记录（不新增列）。
  因此 list_deleted 会把**所有已删除的公共内容**展示给任何登录用户：
  否则普通用户删了公共库之后既看不见、也恢复不了，等于把它弄丢了。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.models.document import Document
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService

KIND_KNOWLEDGE = "knowledge"
KIND_DOCUMENT = "document"


class RecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.documents = DocumentRepository(session)
        self.users = UserRepository(session)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_kind(kind: str) -> str:
        value = (kind or "").strip().lower()
        if value in {KIND_KNOWLEDGE, "kb", "knowledge_base"}:
            return KIND_KNOWLEDGE
        if value in {KIND_DOCUMENT, "doc", "file"}:
            return KIND_DOCUMENT
        raise ValidationError("kind 只能是 knowledge 或 document")

    async def _load_user(self, user_id: int):
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        return user

    @staticmethod
    def _ensure_can_touch(owner_id: int, is_public: bool, user_id: int) -> None:
        """恢复 / 查看：自己的条目，或公共内容（任何登录用户都可以恢复公共内容）。"""
        if owner_id != user_id and not is_public:
            # 用 404 而不是 403：不向他人泄露"这条记录存在"这一信息
            raise NotFoundError("回收站中不存在该条目或无权访问")

    @staticmethod
    def _ensure_can_purge(owner_id: int, is_public: bool, user) -> None:  # noqa: ANN001
        """彻底删除（物理）：公共内容只有站长能删；私有内容只有本人能删。"""
        if owner_id != user.id and not is_public:
            raise NotFoundError("回收站中不存在该条目或无权访问")
        if is_public and not AuthService.is_admin(user):
            raise PermissionDeniedError("公共库内容只有站长可以彻底删除")

    # ------------------------------------------------------------------ #
    async def list_deleted(self, user_id: int) -> list[dict]:
        """列出我删除的知识库与文档（含文件名、所属知识库、删除时间）。

        - 知识库：我自己的 + 已被删除的公共库；
        - 文档：按 (知识库, 文件名) 聚合成一条，避免一篇文档的每个切片各占一行。
        """
        await self._load_user(user_id)

        own_bases = await self.knowledge.list_deleted_for_user(user_id)
        public_bases = await self.knowledge.list_deleted_public()
        deleted_bases = {base.id: base for base in list(own_bases) + list(public_bases)}
        public_deleted_ids = [base.id for base in public_bases]

        documents = await self.documents.list_deleted_by_user(user_id)
        if public_deleted_ids:
            documents += await self.documents.list_deleted_by_knowledge_ids(public_deleted_ids)

        # 按 (知识库, 文件名) 聚合：同一篇文档的全部切片 = 回收站里的一条记录
        grouped: dict[tuple[int, str], list[Document]] = {}
        for document in documents:
            grouped.setdefault((document.knowledge_id, document.filename), []).append(document)

        # 单独查"所属知识库"的名字（包括仍然活着的库）：某个文档被单独删掉时，
        # 它的知识库还活着，不能因此把该知识库也列进回收站。
        lookup = dict(deleted_bases)
        extra_ids = {key[0] for key in grouped} - set(lookup)
        for base in await self.knowledge.list_by_ids(list(extra_ids)):
            lookup[base.id] = base

        items: list[dict] = []
        for base_id, base in deleted_bases.items():
            chunk_count = sum(len(chunks) for (kid, _name), chunks in grouped.items() if kid == base_id)
            items.append(
                {
                    "kind": KIND_KNOWLEDGE,
                    "id": base.id,
                    "name": base.name,
                    "deleted_at": base.deleted_at,
                    "owner_id": base.user_id,
                    "is_public": bool(base.is_public),
                    "knowledge_id": None,
                    "knowledge_name": None,
                    "parent_deleted": False,
                    "document_count": chunk_count,
                    "chunk_count": chunk_count,
                }
            )

        for (knowledge_id, filename), chunks in grouped.items():
            base = lookup.get(knowledge_id)
            deleted_times = [chunk.deleted_at for chunk in chunks if chunk.deleted_at]
            items.append(
                {
                    "kind": KIND_DOCUMENT,
                    "id": min(chunk.id for chunk in chunks),
                    "name": filename,
                    "deleted_at": max(deleted_times) if deleted_times else None,
                    "owner_id": chunks[0].user_id,
                    "is_public": bool(base.is_public) if base else False,
                    "knowledge_id": knowledge_id,
                    "knowledge_name": base.name if base else "",
                    # 父知识库也在回收站：恢复/彻底删除时会一并处理，避免产生孤儿
                    "parent_deleted": bool(base is not None and base.deleted_at is not None),
                    "document_count": len(chunks),
                    "chunk_count": len(chunks),
                }
            )

        # 排序：知识库在前、文档在后；同类里最近删除的排最前。
        # 两次稳定排序（Python sort 稳定）比写一个混合方向的 key 更直观。
        knowledge_first = {KIND_KNOWLEDGE: 0, KIND_DOCUMENT: 1}
        items.sort(key=lambda item: str(item["deleted_at"] or ""), reverse=True)
        items.sort(key=lambda item: knowledge_first.get(item["kind"], 2))
        return items

    # ------------------------------------------------------------------ #
    async def _restore_ancestors(self, user, parent_id: int | None) -> list[int]:  # noqa: ANN001
        """把还在回收站里的祖先一并恢复，避免出现"父级没了"的孤儿节点。"""
        restored: list[int] = []
        current = parent_id
        guard = 0
        while current is not None and guard < 64:
            guard += 1
            parent = await self.knowledge.get(current)
            if parent is None or parent.deleted_at is None:
                break
            if parent.user_id != user.id and not parent.is_public:
                break
            await self.knowledge.restore_by_ids([parent.id])
            await self.documents.restore_by_knowledge_ids([parent.id])
            restored.append(parent.id)
            current = parent.parent_id
        return restored

    async def restore(self, user_id: int, kind: str, item_id: int) -> dict:
        """恢复：把 deleted_at 置空（父级若也在回收站则一并恢复）。"""
        user = await self._load_user(user_id)
        normalized = self._normalize_kind(kind)

        if normalized == KIND_KNOWLEDGE:
            base = await self.knowledge.get(item_id)
            if base is None or base.deleted_at is None:
                raise NotFoundError("回收站中不存在该知识库")
            self._ensure_can_touch(base.user_id, bool(base.is_public), user.id)
            # 整棵子树（含已在回收站里的子节点）一起回来
            ids = await self.knowledge.list_subtree_ids([base.id], include_deleted=True)
            await self.knowledge.restore_by_ids(ids)
            await self.documents.restore_by_knowledge_ids(ids)
            parents = await self._restore_ancestors(user, base.parent_id)
            await self.session.commit()
            return {
                "kind": KIND_KNOWLEDGE,
                "id": base.id,
                "affected": len(ids),
                "message": "已恢复知识库及整棵子树（含 " + str(len(parents)) + " 个父级）",
            }

        document = await self.documents.get(item_id)
        if document is None or document.deleted_at is None:
            raise NotFoundError("回收站中不存在该文档")
        parent_base = await self.knowledge.get(document.knowledge_id)
        is_public = bool(parent_base is not None and parent_base.is_public)
        self._ensure_can_touch(document.user_id, is_public, user.id)
        if parent_base is not None and parent_base.deleted_at is not None:
            # 父级也在回收站：先恢复父级（整库），文档自然一起回来
            await self.knowledge.restore_by_ids([parent_base.id])
            await self.documents.restore_by_knowledge_ids([parent_base.id])
            await self._restore_ancestors(user, parent_base.parent_id)
        else:
            await self.documents.restore_by_file(document.knowledge_id, document.filename)
        await self.session.commit()
        return {
            "kind": KIND_DOCUMENT,
            "id": document.id,
            "affected": 1,
            "message": "已恢复文档：" + document.filename,
        }

    # ------------------------------------------------------------------ #
    async def purge(self, user_id: int, kind: str, item_id: int) -> dict:
        """彻底删除（物理 DELETE）。公共库内容只有站长能执行。"""
        user = await self._load_user(user_id)
        normalized = self._normalize_kind(kind)

        if normalized == KIND_KNOWLEDGE:
            base = await self.knowledge.get(item_id)
            if base is None:
                raise NotFoundError("知识库不存在")
            if base.deleted_at is None:
                raise ValidationError("只能彻底删除回收站中的内容，请先删除")
            self._ensure_can_purge(base.user_id, bool(base.is_public), user)
            ids = await self.knowledge.list_subtree_ids([base.id], include_deleted=True)
            await self.documents.delete_by_knowledge_ids(ids)
            await self.knowledge.delete_by_ids(ids)
            await self.session.commit()
            return {
                "kind": KIND_KNOWLEDGE,
                "id": base.id,
                "affected": len(ids),
                "message": "已彻底删除知识库及整棵子树（不可恢复）",
            }

        document = await self.documents.get(item_id)
        if document is None:
            raise NotFoundError("文档不存在")
        if document.deleted_at is None:
            raise ValidationError("只能彻底删除回收站中的内容，请先删除")
        parent_base = await self.knowledge.get(document.knowledge_id)
        is_public = bool(parent_base is not None and parent_base.is_public)
        self._ensure_can_purge(document.user_id, is_public, user)
        removed = await self.documents.delete_by_file(document.knowledge_id, document.filename)
        await self.session.commit()
        return {
            "kind": KIND_DOCUMENT,
            "id": document.id,
            "affected": removed,
            "message": "已彻底删除文档：" + document.filename,
        }
