"""Embedding 向量化层。

配置优先级（从高到低）：
1. 调用方传入的 EmbeddingConfig —— 例如访客在界面上填的「自带向量服务」，
   只在其本人会话内生效，不入库、不写日志、不共享；
2. 服务端 .env / Secrets 配置（EMBEDDING_PROVIDER 等）；
3. 内置离线哈希实现 —— 零依赖、可离线，但**只是关键词重合度，不是语义模型**。

对接协议：任何兼容 OpenAI 的 POST {api_base}/embeddings 服务
（例如 SiliconFlow 的 BAAI/bge-m3、智谱 embedding-3、OpenAI text-embedding-3-small）。
"""

import hashlib
import logging
import math
import re
from dataclasses import dataclass

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class EmbeddingConfig:
    """一次请求所使用的向量化配置。"""

    provider: str = "local"  # local | openai
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    dim: int = 0

    @property
    def is_remote(self) -> bool:
        return (
            self.provider.lower() == "openai"
            and bool(self.api_base.strip())
            and bool(self.api_key.strip())
        )

    @property
    def effective_dim(self) -> int:
        return self.dim or settings.embedding_dim

    @property
    def label(self) -> str:
        if self.is_remote:
            return "外部语义向量服务 " + (self.model.strip() or "(未指定模型)")
        return "内置离线（关键词哈希，非语义模型）"

    @classmethod
    def from_settings(cls) -> "EmbeddingConfig":
        return cls(
            provider=settings.embedding_provider,
            api_base=settings.embedding_api_base,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )


def resolve_config(config: EmbeddingConfig | None = None) -> EmbeddingConfig:
    return config if config is not None else EmbeddingConfig.from_settings()


def tokenize(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    tokens.extend(cjk)
    for index in range(len(cjk) - 1):
        tokens.append(cjk[index] + cjk[index + 1])
    return tokens


def local_embed(text: str, dim: int | None = None) -> list[float]:
    """内置离线实现：带符号的哈希词袋 + L2 归一化（确定性、无依赖）。"""
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


async def _remote_embed(texts: list[str], config: EmbeddingConfig) -> list[list[float]]:
    url = config.api_base.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": "Bearer " + config.api_key,
        "Content-Type": "application/json",
    }
    payload = {"model": config.model, "input": texts}
    async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    return [item.get("embedding", []) for item in items]


async def embed_texts(
    texts: list[str], config: EmbeddingConfig | None = None
) -> list[list[float]]:
    if not texts:
        return []
    resolved = resolve_config(config)
    if resolved.is_remote:
        try:
            vectors = await _remote_embed(texts, resolved)
            if len(vectors) == len(texts):
                return vectors
            logger.warning("远端 embedding 返回数量不匹配，回退本地实现")
        except Exception as exc:
            logger.warning("远端 embedding 调用失败，回退本地实现: %s", exc)
    return [local_embed(text, resolved.effective_dim) for text in texts]


async def embed_query(text: str, config: EmbeddingConfig | None = None) -> list[float]:
    vectors = await embed_texts([text], config)
    return vectors[0] if vectors else []
