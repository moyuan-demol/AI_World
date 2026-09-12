"""Knowledge base + document upload business logic."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.files import sanitize_filename
from app.models.document import Document
from app.models.knowledge import KnowledgeBase
from app.rag.embedding import EmbeddingConfig
from app.rag.rag_service import RagService
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.schemas.knowledge import KnowledgeCreate, KnowledgeOut, UploadResult

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
            created_time=base.created_time,
            document_count=document_count,
        )

    # ------------------------------------------------------------------ #
    async def create(self, user_id: int, payload: KnowledgeCreate) -> KnowledgeOut:
        base = await self.knowledge.create(
            user_id=user_id,
            name=payload.name.strip(),
            description=payload.description or "",
        )
        await self.session.commit()
        return self._to_out(base, 0)

    async def list(self, user_id: int) -> list[KnowledgeOut]:
        bases = await self.knowledge.list_by_user(user_id)
        result: list[KnowledgeOut] = []
        for base in bases:
            count = await self.documents.count_by_knowledge(base.id)
            result.append(self._to_out(base, count))
        return result

    async def get(self, user_id: int, knowledge_id: int) -> KnowledgeBase:
        base = await self.knowledge.get_for_user(knowledge_id, user_id)
        if base is None:
            raise NotFoundError("知识库不存在或无权访问")
        return base

    async def get_out(self, user_id: int, knowledge_id: int) -> KnowledgeOut:
        base = await self.get(user_id, knowledge_id)
        count = await self.documents.count_by_knowledge(base.id)
        return self._to_out(base, count)

    async def delete(self, user_id: int, knowledge_id: int) -> None:
        base = await self.get(user_id, knowledge_id)
        await self.documents.delete_by_knowledge(base.id)
        await self.knowledge.delete(base)
        await self.session.commit()

    async def list_documents(self, user_id: int, knowledge_id: int, limit: int = 200) -> list[Document]:
        await self.get(user_id, knowledge_id)
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
