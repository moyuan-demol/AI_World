"""Vector retrieval over the documents table (cosine similarity)."""

import json
import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.errors import NotFoundError
from app.models.document import Document
from app.rag.embedding import EmbeddingConfig, embed_query
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository


@dataclass
class RetrievedChunk:
    document_id: int
    knowledge_id: int
    filename: str
    chunk_index: int
    content: str
    score: float


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class Retriever:
    """Cosine similarity search constrained to the current user's data."""

    def __init__(
        self, session: AsyncSession, embedding_config: EmbeddingConfig | None = None
    ) -> None:
        self.session = session
        self.embedding_config = embedding_config
        self.documents = DocumentRepository(session)
        self.knowledge = KnowledgeRepository(session)

    async def _resolve_knowledge_ids(
        self,
        user_id: int,
        knowledge_id: int | None,
        knowledge_ids: list[int] | None = None,
    ) -> list[int]:
        """确定检索范围（永远限定在该用户自己的知识库内）。

        优先级：显式指定单个 knowledge_id > 传入的 knowledge_ids（角色知识边界）
                > 该用户的全部知识库
        """
        owned = {base.id for base in await self.knowledge.list_by_user(user_id)}
        if knowledge_id is not None:
            if knowledge_id not in owned:
                raise NotFoundError("知识库不存在或无权访问")
            return [knowledge_id]
        if knowledge_ids:
            scoped = [item for item in knowledge_ids if item in owned]
            return scoped
        return list(owned)

    async def search(
        self,
        user_id: int,
        query: str,
        *,
        knowledge_id: int | None = None,
        knowledge_ids: list[int] | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not query or not query.strip():
            return []
        scoped_ids = await self._resolve_knowledge_ids(user_id, knowledge_id, knowledge_ids)
        if not scoped_ids:
            return []
        documents: list[Document] = await self.documents.list_by_knowledge_ids(scoped_ids)
        if not documents:
            return []

        query_vector = await embed_query(query, self.embedding_config)
        if not query_vector:
            return []

        scored: list[RetrievedChunk] = []
        for document in documents:
            try:
                vector = json.loads(document.embedding or "[]")
            except json.JSONDecodeError:
                continue
            score = cosine_similarity(query_vector, vector)
            if score <= 0:
                continue
            scored.append(
                RetrievedChunk(
                    document_id=document.id,
                    knowledge_id=document.knowledge_id,
                    filename=document.filename,
                    chunk_index=document.chunk_index,
                    content=document.content,
                    score=round(score, 6),
                )
            )

        scored.sort(key=lambda chunk: chunk.score, reverse=True)
        return scored[: (top_k or settings.retrieval_top_k)]
