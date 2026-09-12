"""Knowledge base + document upload business logic."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.core.files import sanitize_filename
from app.models.document import Document
from app.models.knowledge import KnowledgeBase
from app.rag.embedding import EmbeddingConfig
from app.rag.rag_service import RagService
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.user_repository import UserRepository
from app.schemas.knowledge import KnowledgeCreate, KnowledgeOut, UploadResult
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(
        self, session: AsyncSession, embedding_config: EmbeddingConfig | None = None
    ) -> None:
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.documents = DocumentRepository(session)
        self.rag = RagService(session, embedding_config)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_out(base: KnowledgeBase, document_count: int) -> KnowledgeOut:
        return KnowledgeOut(
            id=base.id,
            name=base.name,
            description=base.description,
            parent_id=base.parent_id,
            is_public=bool(base.is_public),
            created_time=base.created_time,
            document_count=document_count,
        )

    # ------------------------------------------------------------------ #
    async def create(self, user_id: int, payload: KnowledgeCreate) -> KnowledgeOut:
        # 公共库只有站长能创建：公共内容所有人可见，必须由站长统一管理。
        if payload.is_public:
            user = await UserRepository(self.session).get(user_id)
            if user is None or not AuthService.is_admin(user):
                raise PermissionDeniedError("只有站长可以创建公共知识库")

        parent_id = payload.parent_id
        if parent_id is not None:
            # 只能挂到自己的节点下
            if await self.knowledge.get_for_user(parent_id, user_id) is None:
                raise NotFoundError("父级知识库不存在或无权访问")
        base = await self.knowledge.create(
            user_id=user_id,
            name=payload.name.strip(),
            description=payload.description or "",
            parent_id=parent_id,
            is_public=bool(payload.is_public),
        )
        await self.session.commit()
        return self._to_out(base, 0)

    async def list(self, user_id: int) -> list[KnowledgeOut]:
        """我的知识库（不含公共库 —— 公共库在「知识世界」单独展示）。"""
        bases = await self.knowledge.list_by_user(user_id)
        # 一次聚合查询拿到所有切片数（原来每个知识库查一次 → N+1）
        counts = await self.documents.count_map([base.id for base in bases])
        return [self._to_out(base, counts.get(base.id, 0)) for base in bases]

    async def list_public(self) -> list[KnowledgeOut]:
        """公共库列表（所有人可查；未删除）。"""
        bases = await self.knowledge.list_public()
        counts = await self.documents.count_map([base.id for base in bases])
        return [self._to_out(base, counts.get(base.id, 0)) for base in bases]

    async def get(self, user_id: int, knowledge_id: int) -> KnowledgeBase:
        base = await self.knowledge.get_for_user(knowledge_id, user_id)
        if base is None:
            raise NotFoundError("知识库不存在或无权访问")
        return base

    async def get_viewable(self, user_id: int, knowledge_id: int) -> KnowledgeBase:
        """可读的知识库：我自己的，或**公共库**（公共库只读，不提供写操作）。"""
        base = await self.knowledge.get_for_user(knowledge_id, user_id)
        if base is not None:
            return base
        candidate = await self.knowledge.get(knowledge_id)
        if candidate is None or candidate.deleted_at is not None or not candidate.is_public:
            raise NotFoundError("知识库不存在或无权访问")
        return candidate

    async def get_out(self, user_id: int, knowledge_id: int) -> KnowledgeOut:
        base = await self.get(user_id, knowledge_id)
        count = await self.documents.count_by_knowledge(base.id)
        return self._to_out(base, count)

    async def subtree_ids(self, user_id: int, knowledge_id: int) -> list[int]:
        """自身 + 全部后代（分级：绑定/删除都以整棵子树为单位）。"""
        await self.get_viewable(user_id, knowledge_id)
        return await self.knowledge.list_subtree_ids([knowledge_id])

    async def delete(self, user_id: int, knowledge_id: int) -> None:
        """软删除该节点**及其整棵子树**（进回收站，可恢复）。

        与旧行为的唯一区别：不再物理删除，而是给整棵子树 + 其下 Document 打
        deleted_at。物理删除只能从回收站由有权限的人显式执行（RecycleService.purge）。
        公共库：任何登录用户都可以"删进回收站"（方案 1：只有站长能彻底删除）。
        """
        base = await self.knowledge.get_for_user(knowledge_id, user_id)
        if base is None:
            candidate = await self.knowledge.get(knowledge_id)
            if candidate is None or candidate.deleted_at is not None or not candidate.is_public:
                raise NotFoundError("知识库不存在或无权访问")
            base = candidate
        # list_subtree_ids 只取"还活着"的节点：已删除的后代保留自己原来的删除时间
        ids = await self.knowledge.list_subtree_ids([base.id])
        deleted_at = datetime.utcnow()
        # 两条批量语句搞定整棵子树：云端往返从 N 次降到 2 次
        await self.documents.soft_delete_by_knowledge_ids(ids, deleted_at)
        await self.knowledge.soft_delete_by_ids(ids, deleted_at)
        await self.session.commit()

    async def list_documents(self, user_id: int, knowledge_id: int, limit: int = 200) -> list[Document]:
        """查看切片：我自己的库或**公共库**均可（公共库只读，所有人可查）。"""
        await self.get_viewable(user_id, knowledge_id)
        documents = await self.documents.list_by_knowledge(knowledge_id)
        return documents[:limit]

    async def count_documents(self, user_id: int) -> int:
        return await self.documents.count_by_user(user_id)

    # ------------------------------------------------------------------ #
    async def upload(
        self,
        user_id: int,
        *,
        filename: str,
        data: bytes,
        knowledge_id: int | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> UploadResult:
        safe_name = sanitize_filename(filename or "upload.txt")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in settings.allowed_extension_list:
            raise ValidationError(
                "只允许上传以下格式：" + ", ".join(settings.allowed_extension_list)
            )
        if not data:
            raise ValidationError("上传的文件为空")
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(data) > max_bytes:
            raise ValidationError("文件超过 " + str(settings.max_upload_mb) + " MB 上限")

        if knowledge_id is None:
            base_name = (name or Path(safe_name).stem or "未命名知识库").strip()[:128]
            base = await self.knowledge.create(
                user_id=user_id,
                name=base_name,
                description=(description or ("由文件 " + safe_name + " 自动创建")),
            )
        else:
            base = await self.get(user_id, knowledge_id)

        chunk_count, char_count = await self.rag.ingest(
            user_id=user_id,
            knowledge_id=base.id,
            filename=safe_name,
            data=data,
        )
        self._store_file(user_id, base.id, safe_name, data)
        await self.session.commit()

        total_chunks = await self.documents.count_by_knowledge(base.id)
        logger.info("Uploaded %s -> knowledge %s (%s chunks)", safe_name, base.id, chunk_count)
        return UploadResult(
            knowledge=self._to_out(base, total_chunks),
            filename=safe_name,
            chunk_count=chunk_count,
            char_count=char_count,
        )

    @staticmethod
    def _store_file(user_id: int, knowledge_id: int, safe_name: str, data: bytes) -> Path:
        """Keep the original file on disk for traceability."""
        target_dir = Path(settings.upload_dir) / ("user_" + str(user_id)) / ("kb_" + str(knowledge_id))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (uuid4().hex[:8] + "_" + safe_name)
        target.write_bytes(data)
        return target

    async def stats(self, user_id: int) -> dict[str, int]:
        bases = await self.knowledge.list_by_user(user_id)
        knowledge_ids = [base.id for base in bases]
        return {
            "knowledge_count": len(bases),
            "document_count": await self.documents.count_by_knowledge_ids(knowledge_ids),
        }
