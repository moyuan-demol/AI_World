"""纯 Python Rerank 精排：在"混合检索 + RRF 融合"之后再做一次逐条对比。

为什么要单独精排：
- 稠密向量与 BM25 用 RRF 融合，看的是"排名"，无法区分两条都命中的切片谁更贴题；
- 工业界的 cross-encoder 精排效果最好，但需要额外模型与算力，这里用纯 Python 的
  可解释启发式替代（零新依赖、离线可测）。

三个打分因素（权重可解释，和为 1.0）：
1. 覆盖率 0.55：命中的"实词"种类 / 查询实词总种类 —— 术语越密集越贴题；
2. 命中位置 0.20：首个命中越靠前越相关（标题/首句命中通常更关键）；
3. 短语命中 0.25：查询里连续多字/多词原样出现在切片中（最长公共子串占比），
   是强相关信号，能区分"词都出现过"与"原话出现过"。
BM25 的词频/逆文档频率已在融合通道里体现，这里不重复加权。

防噪：
- 只有长度 >= 2 的记号（中文双字词 / 英文单词）才算"实词"，
  只命中一个常见单字（"是""的"）不产生命中，会被排到最后；
- 完全没有实词命中的切片会被排到最后，但**仍然保留** ——
  因为它们已经通过了稠密通道的最低相关性阈值，可能是不同措辞的相关内容。
"""

from __future__ import annotations

import re

from app.rag.embedding import tokenize

# 权重之和为 1.0，便于解释每一项对最终排序的贡献
COVERAGE_WEIGHT = 0.55
POSITION_WEIGHT = 0.20
PHRASE_WEIGHT = 0.25

# 归一化：去掉空白与标点，只保留字母/数字/汉字/下划线，用于"短语命中"比较
_PUNCT_RE = re.compile(r"[^\w]+")


def _normalized(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").lower())


def meaningful_terms(query: str) -> list[str]:
    """查询里的"实词"记号（去重、保序）：中文双字词、英文/数字词（长度 >= 2）。

    单字既噪声大又几乎必然命中，因此不计入实词。
    """
    terms: list[str] = []
    for token in tokenize(query or ""):
        if len(token) >= 2 and token not in terms:
            terms.append(token)
    return terms


def longest_common_substring_length(left: str, right: str) -> int:
    """最长公共子串长度（滚动数组 DP，O(len(right)) 空间）。"""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        char = left[i - 1]
        for j in range(1, len(right) + 1):
            if char == right[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def _position_score(content_lower: str, hits: list[str]) -> float:
    """首个命中越靠前分数越高：1.0 表示开头就命中，趋近 0 表示在结尾。"""
    positions = [content_lower.find(term) for term in hits]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return 0.0
    first = min(positions)
    return max(0.0, 1.0 - first / max(1, len(content_lower)))


def rerank(query: str, chunks: list, top_k: int | None = None) -> list:
    """对候选切片按相关性重新精排，返回前 top_k 条。

    - 入参 chunks 可以是任意带 content 属性的对象（项目里即 RetrievedChunk）；
    - 空查询 / 纯符号查询：不报错，保持原顺序并截断；
    - 无实词命中的切片：得分 0，排在最后但保留。
    """
    items = list(chunks or [])
    if not items:
        return []
    limit = top_k if top_k and top_k > 0 else len(items)

    terms = meaningful_terms(query)
    query_normalized = _normalized(query)
    if not terms or not query_normalized:
        # 没有可用的实词（空查询/纯标点）：不做精排，保持上游顺序
        return items[:limit]

    scored: list[tuple[float, int, object]] = []
    for index, chunk in enumerate(items):
        content = getattr(chunk, "content", "") or ""
        content_lower = content.lower()
        content_tokens = set(tokenize(content))
        hits = [term for term in terms if term in content_tokens]
        if not hits:
            # 完全没有实词命中：排到最后，但保留（稠密通道已判定其可能相关）
            scored.append((0.0, index, chunk))
            continue

        coverage = len(hits) / len(terms)
        position = _position_score(content_lower, hits)
        phrase = longest_common_substring_length(query_normalized, _normalized(content))
        phrase = min(1.0, phrase / len(query_normalized))
        score = COVERAGE_WEIGHT * coverage + POSITION_WEIGHT * position + PHRASE_WEIGHT * phrase
        scored.append((score, index, chunk))

    # 分数降序；同分时按原始下标升序（Python 排序稳定，这里显式写出来更清楚）
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for _score, _index, chunk in scored[:limit]]
