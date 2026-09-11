"""Embedding providers.

Default is a dependency-free local hashing embedder (deterministic, offline).
Set EMBEDDING_PROVIDER=openai plus EMBEDDING_API_BASE / EMBEDDING_API_KEY /
EMBEDDING_MODEL to use any OpenAI compatible embedding endpoint (for example a
BGE-M3 service). Vectors are stored as JSON text in SQLite; migrate the column
to pgvector later without touching the RAG code.
"""

import hashlib
import logging
import math
import re

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    tokens.extend(cjk)
    for index in range(len(cjk) - 1):
        tokens.append(cjk[index] + cjk[index + 1])
    return tokens


def local_embed(text: str, dim: int | None = None) -> list[float]:
    """Signed hashing bag-of-words embedding, L2 normalised."""
    dim = dim or settings.embedding_dim
    vector = [0.0] * dim
    tokens = tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0:
        vector = [value / norm for value in vector]
    return vector


async def _remote_embed(texts: list[str]) -> list[list[float]]:
    url = settings.embedding_api_base.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": "Bearer " + settings.embedding_api_key,
        "Content-Type": "application/json",
    }
    payload = {"model": settings.embedding_model, "input": texts}
    async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    return [item.get("embedding", []) for item in items]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if settings.embedding_provider.lower() == "openai" and settings.embedding_api_base and settings.embedding_api_key:
        try:
            vectors = await _remote_embed(texts)
            if len(vectors) == len(texts):
                return vectors
            logger.warning("远端 embedding 返回数量不匹配，回退本地实现")
        except Exception as exc:
            logger.warning("远端 embedding 调用失败，回退本地实现: %s", exc)
    return [local_embed(text) for text in texts]


async def embed_query(text: str) -> list[float]:
    vectors = await embed_texts([text])
    return vectors[0] if vectors else []
