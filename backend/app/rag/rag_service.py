"""RAG orchestration: ingest (load -> split -> embed -> store) and retrieve."""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import build_context_block
from app.config.settings import settings
from app.core.errors import ValidationError
from app.rag.embedding import EmbeddingConfig, embed_texts
from app.rag.loader import load_from_bytes
from app.rag.retriever import RetrievedChunk, Retriever
from app.rag.splitter import split_text
from app.repositories.document_repository import DocumentRepository

logger = logging.getLogger(__name__)


class RagService:
    def __init__(
        self, session: AsyncSession, embedding_config: EmbeddingConfig | None = None
    ) -> None:
        self.session = session
        self.embedding_config = embedding_config
        self.documents = DocumentRepository(session)
        self.retriever = Retriever(session, embedding_config)

    async def ingest(self, *, user_id: int, knowledge_id: int, filename: str, data: bytes) -> tuple[int, int]:
        """Parse, split, embed and persist one uploaded file.

        Returns (chunk_count, char_count).
        """
        text = load_from_bytes(data, filename)
        text = (text or "").strip()
        if not text:
            raise ValidationError("文件内容为空，或未能解析出任何文本")

        chunks = split_text(text)
        if not chunks:
            raise ValidationError("文本切片结果为空，请检查文件内容")

        vectors = await embed_texts(chunks, self.embedding_config)
        for index, chunk in enumerate(chunks):
            vector = vectors[index] if index < len(vectors) else []
            await self.documents.create(
                knowledge_id=knowledge_id,
                user_id=user_id,
                filename=filename,
                chunk_index=index,
                content=chunk,
                embedding=json.dumps(vector),
            )
        logger.info(
            "Ingested %s for user %s: %s chunks / %s chars",
            filename,
            user_id,
            len(chunks),
            len(text),
        )
        return len(chunks), len(text)

    async def retrieve(
        self,
        *,
        user_id: int,
        query: str,
        knowledge_id: int | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        return await self.retriever.search(
            user_id,
            query,
            knowledge_id=knowledge_id,
            top_k=top_k,
        )

    async def build_context(
        self,
        *,
        user_id: int,
        query: str,
        knowledge_id: int | None = None,
        top_k: int | None = None,
    ) -> tuple[str, list[RetrievedChunk]]:
        chunks = await self.retrieve(
            user_id=user_id,
            query=query,
            knowledge_id=knowledge_id,
            top_k=top_k,
        )
        context = build_context_block(chunks)
        if len(context) > settings.max_context_chars:
            context = context[: settings.max_context_chars]
        return context, chunks
