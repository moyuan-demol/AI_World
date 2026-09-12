"""Async DeepSeek (OpenAI compatible) chat client.

The API key is never hard coded: it is read from the environment through
app.config.settings. When no key is configured the client answers in an
explicit offline demo mode so the product is still fully explorable.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config.settings import settings
from app.core.errors import AIUnavailableError

logger = logging.getLogger(__name__)

Message = dict[str, str]


@dataclass
class AIResult:
    text: str
    model: str
    offline: bool = False


class AIClient:
    """Thin wrapper around the DeepSeek chat completions endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        # 允许运行时传入凭据（站点配置，或访客自带的 Key）。
        # 都不传就回落到服务端 .env；仍然没有则进入离线演示模式。
        self.api_key = (api_key if api_key is not None else settings.deepseek_api_key).strip()
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def endpoint(self) -> str:
        return self.base_url + "/chat/completions"

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        offline_fallback: Callable[[], str] | None = None,
    ) -> AIResult:
        if not self.is_configured:
            text = offline_fallback() if offline_fallback else self._default_offline_text(messages)
            return AIResult(text=text, model="offline-demo", offline=True)

        payload = {
            "model": model or self.model,
            "messages": list(messages),
            "temperature": settings.ai_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.ai_max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            logger.error("DeepSeek HTTP error %s: %s", exc.response.status_code if exc.response else "?", detail)
            raise AIUnavailableError("DeepSeek 接口返回错误：" + str(exc.response.status_code if exc.response else "")) from exc
        except Exception as exc:  # network / timeout / json
            logger.error("DeepSeek request failed: %s", exc)
            raise AIUnavailableError("无法连接 DeepSeek 服务：" + str(exc)) from exc

        choices = data.get("choices") or []
        if not choices:
            raise AIUnavailableError("DeepSeek 未返回任何候选结果")
        text = (choices[0].get("message") or {}).get("content") or ""
        text = text.strip()
        if not text:
            raise AIUnavailableError("DeepSeek 返回了空内容")
        return AIResult(text=text, model=data.get("model") or payload["model"])

    @staticmethod
    def _default_offline_text(messages: Sequence[Message]) -> str:
        question = ""
        for message in reversed(list(messages)):
            if message.get("role") == "user":
                question = message.get("content", "")
                break
        return (
            "【离线演示模式】当前没有可用的模型 API Key，以下为本地占位回答。\n\n"
            "你的问题是：" + question[:200] + "\n\n"
            "在左侧边栏「使用我自己的 API Key」里填入你自己的 Key，"
            "或由站点管理员配置 DEEPSEEK_API_KEY，这里就会返回真实的模型回答。"
        )


@lru_cache(maxsize=1)
def get_ai_client() -> AIClient:
    return AIClient()
