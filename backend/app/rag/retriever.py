"""Vector retrieval over the documents table (cosine similarity)."""

import json
import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.errors import NotFoundError
from app.models.document import Document
from app.rag.bm25 import bm25_search, reciprocal_rank_fusion
from app.rag.embedding import EmbeddingConfig, embed_query
from app.rag.rerank import rerank
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


def _document_key(chunk: RetrievedChunk) -> tuple[int, str]:
    """同一篇文档的标识：(知识库, 文件名)。

    说明：RetrievedChunk.document_id 在本项目里是"切片行 id"（每条切片唯一），
    若以它作为多样性分组键会永远不生效；真正标识"同一篇文档"的是文件名（加知识库）。
    这也是唯一能实现"让多篇文档都进入上下文"这一目的的解释。
    """
    return (chunk.knowledge_id, chunk.filename)


def _merge_run(run: list[RetrievedChunk]) -> RetrievedChunk:
    """把一段连续切片合并成一条（small-to-big 的最小单元）。"""
    first = run[0]
    return RetrievedChunk(
        # 合并后用组内最小切片 id 作为代表，保证结果稳定、可复现
        document_id=min(chunk.document_id for chunk in run),
        knowledge_id=first.knowledge_id,
        filename=first.filename,
        chunk_index=first.chunk_index,
        content="\n".join(chunk.content or "" for chunk in run),
        score=max(chunk.score for chunk in run),
    )


def merge_adjacent_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """small-to-big：把同一文档里 chunk_index 相邻（差 <= 1）的命中切片合并成一段。

    为什么这样做：一篇文档被切成 4 条相邻切片都命中时，给模型 4 条割裂的片段
    不如给 1 段连贯上下文 —— 语义完整、token 更省、引用也更清晰。
    合并段之间按"组内最早命中的原始排名"排序，保持与 RRF 结果一致。
    """
    if not chunks:
        return []
    # 记录每条切片在输入里的次序（输入已按 RRF 排名降序）
    order = {id(chunk): index for index, chunk in enumerate(chunks)}
    grouped: dict[tuple[int, str], list[RetrievedChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(_document_key(chunk), []).append(chunk)

    merged: list[tuple[int, RetrievedChunk]] = []
    for items in grouped.values():
        ordered = sorted(items, key=lambda chunk: chunk.chunk_index)
        run_start = 0
        for index in range(1, len(ordered) + 1):
            # 到达末尾，或与下一条不再相邻 -> 结算当前连续段
            if (
                index == len(ordered)
                or ordered[index].chunk_index - ordered[index - 1].chunk_index > 1
            ):
                run = ordered[run_start:index]
                merged.append((min(order[id(chunk)] for chunk in run), _merge_run(run)))
                run_start = index
    merged.sort(key=lambda item: item[0])
    return [chunk for _rank, chunk in merged]


def limit_per_document(chunks: list[RetrievedChunk], max_per_document: int) -> list[RetrievedChunk]:
    """同文档多样性限制：同一篇文档最多保留 max_per_document 条。

    为什么这样做：否则一篇长文档会把 top_k 全部占满，其它文档永远进不了上下文。
    max_per_document <= 0 表示不限制。
    """
    if max_per_document is None or max_per_document <= 0:
        return list(chunks)
    counts: dict[tuple[int, str], int] = {}
    kept: list[RetrievedChunk] = []
    for chunk in chunks:
        key = _document_key(chunk)
        if counts.get(key, 0) >= max_per_document:
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(chunk)
    return kept


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
        bases = await self.knowledge.list_by_user(user_id)
        owned = {base.id for base in bases}
        if knowledge_id is not None:
            if knowledge_id not in owned:
                raise NotFoundError("知识库不存在或无权访问")
            # 分级：选中文件夹时自动包含其下所有子库
            return await self.knowledge.list_descendant_ids(user_id, [knowledge_id])
        if knowledge_ids:
            scoped = [item for item in knowledge_ids if item in owned]
            return await self.knowledge.list_descendant_ids(user_id, scoped)
        return [base.id for base in bases]

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

        # ---- 通道 1：稠密向量（语义相近），并应用最低相关性阈值 ----
        dense: list[tuple[float, Document]] = []
        for document in documents:
            try:
                vector = json.loads(document.embedding or "[]")
            except json.JSONDecodeError:
                continue
            score = cosine_similarity(query_vector, vector)
            if score < settings.retrieval_min_score:
                continue
            dense.append((score, document))

        # ---- 通道 2：BM25 稀疏检索（精确术语/缩写/专有名词）----
        sparse = bm25_search(query, documents, limit=max(10, (top_k or settings.retrieval_top_k) * 4))

        # ---- RRF 融合：两路按"排名"合并，取长补短 ----
        ranked = reciprocal_rank_fusion(
            [document.id for _score, document in dense],
            [document.id for _score, document in sparse],
        )
        by_id: dict[int, Document] = {}
        dense_score: dict[int, float] = {}
        for score, document in dense:
            by_id[document.id] = document
            dense_score[document.id] = score
        for _score, document in sparse:
            by_id.setdefault(document.id, document)

        results: list[RetrievedChunk] = []
        for doc_id, _rrf in ranked:
            document = by_id.get(doc_id)
            if document is None:
                continue
            results.append(
                RetrievedChunk(
                    document_id=document.id,
                    knowledge_id=document.knowledge_id,
                    filename=document.filename,
                    chunk_index=document.chunk_index,
                    content=document.content,
                    score=round(dense_score.get(doc_id, 0.0), 6),
                )
            )
        # ---- 后处理三步（顺序有讲究）----
        # 1) 相邻切片合并：先把"割裂的小切片"拼成连贯上下文；
        # 2) 同文档多样性：再限制同一篇文档的条数，给其它文档留位置；
        # 3) Rerank 精排：最后按"与原问题的贴合度"重排，再截断 top_k。
        merged = merge_adjacent_chunks(results)
        diverse = limit_per_document(merged, settings.retrieval_max_per_document)
        return rerank(query, diverse, top_k or settings.retrieval_top_k)
