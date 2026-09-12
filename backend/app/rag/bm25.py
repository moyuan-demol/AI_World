"""BM25 稀疏检索 + RRF 融合（纯 Python，无额外依赖）。

为什么要加这一层：
- 稠密向量擅长"语义相近"（换个说法也能命中），但对**精确术语/缩写/专有名词**不够敏感；
- BM25 正好相反：对"阿柏西普""VEGF""CRVO"这类词命中极准；
- 两者用 RRF（Reciprocal Rank Fusion）按**排名**融合，取长补短 —— 这就是工业界的混合检索。

防噪：BM25 要求与查询共享至少一个"实词"记号（中文双字/英文2位以上），
避免只共享一个常见字（"是""的"）就被当成命中。
"""

from __future__ import annotations

import math
from collections import Counter

from app.rag.embedding import tokenize

K1 = 1.5   # BM25 词频饱和参数（标准值）
B = 0.75   # BM25 长度归一化参数（标准值）
RRF_K = 60  # RRF 平滑常数（论文标准值）


def _shares_meaningful_token(query_tokens: set[str], doc_tokens: Counter) -> bool:
    """必须共享至少一个实词记号，避免单字噪声造成误命中。"""
    return any(len(token) >= 2 and token in doc_tokens for token in query_tokens)


def bm25_search(query: str, documents: list, limit: int = 20) -> list[tuple[float, object]]:
    """对候选文档做 BM25 打分，返回 [(分数, 文档)]，按分数降序。"""
    if not query.strip() or not documents:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    query_set = set(query_tokens)

    prepared = [(doc, Counter(tokenize(doc.content or ""))) for doc in documents]
    lengths = [sum(counter.values()) for _doc, counter in prepared]
    total = len(prepared)
    avg_len = (sum(lengths) / total) if total else 1.0

    # 文档频率（只统计与查询共享的词，省去全量统计开销）
    doc_freq: Counter = Counter()
    for _doc, counter in prepared:
        for token in set(counter) & query_set:
            doc_freq[token] += 1

    scored: list[tuple[float, object]] = []
    for (doc, counter), length in zip(prepared, lengths):
        if not _shares_meaningful_token(query_set, counter):
            continue
        score = 0.0
        for token in query_tokens:
            tf = counter.get(token, 0)
            if not tf:
                continue
            idf = math.log(1.0 + (total - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
            denominator = tf + K1 * (1 - B + B * (length / (avg_len or 1.0)))
            score += idf * (tf * (K1 + 1)) / (denominator or 1.0)
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def reciprocal_rank_fusion(*rankings: list[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """把多路排名的 doc_id 序列融合成一个排名。

    RRF 只看"排名"不看分数，因此可以安全地融合量纲完全不同的两路结果
    （余弦相似度 0~1 与 BM25 的 0~n）。返回 [(doc_id, 融合分)] 降序。
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
