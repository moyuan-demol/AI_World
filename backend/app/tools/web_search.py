"""外部世界接口：可切换、可并行、失败自动降级的联网检索工具。

设计要点（对应实际使用中的四个问题）：

1. **谁来回答？** —— 检索工具只负责"找资料"，**最终回答仍由语言模型生成**；
   没有模型 Key 时，直接把检索到的资料原样展示（不编造）。
2. **优先级？** —— providers 按配置顺序排列，结果先到先排；
   同一条结果只保留一次（按 URL 去重）。
3. **网络不支持怎么办？** —— 每个 provider 独立超时（默认 8 秒）+
   失败静默降级；任何一个挂掉都不会阻塞回答，状态会如实显示给用户。
4. **国内部署怎么办？** —— provider 可自由组合：维基/DuckDuckGo 免费但国内服务器不可达；
   国内服务器可改用 SearXNG / Tavily / Serper（填 Key）。
   注意：**应用部署在哪个网络，检索就在哪个网络发起**。
"""

from __future__ import annotations

import abc
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str


@dataclass
class ProviderReport:
    """某个 provider 这次的执行结果（用于界面如实展示）。"""

    provider: str
    ok: bool
    count: int = 0
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class SearchOutcome:
    results: list[SearchResult] = field(default_factory=list)
    reports: list[ProviderReport] = field(default_factory=list)

    @property
    def used_providers(self) -> list[str]:
        return [item.provider for item in self.reports if item.count > 0]


class SearchProvider(abc.ABC):
    """一个检索来源。"""

    name: str = "base"
    label: str = "基础"
    requires_key: bool = False
    requires_base_url: bool = False

    def __init__(self, api_key: str = "", base_url: str = "", timeout: float = 8.0) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        if self.requires_key and not self.api_key:
            return False
        if self.requires_base_url and not self.base_url:
            return False
        return True

    @abc.abstractmethod
    async def search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)


class WikipediaProvider(SearchProvider):
    name = "wikipedia"
    label = "维基百科（免费，无需 Key；国内服务器不可达）"

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        url = (
            "https://zh.wikipedia.org/w/api.php?action=query&list=search"
            "&format=json&utf8=1&srlimit=" + str(limit) + "&srsearch=" + quote_plus(query)
        )
        async with self._client() as client:
            response = await client.get(url, headers={"User-Agent": "AIWorld/1.0"})
            response.raise_for_status()
            data = response.json()
        items = ((data.get("query") or {}).get("search")) or []
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url="https://zh.wikipedia.org/wiki/" + quote_plus(str(item.get("title", ""))),
                snippet=strip_html(str(item.get("snippet", ""))),
                provider=self.name,
            )
            for item in items
        ]


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"
    label = "DuckDuckGo（免费，无需 Key；偏摘要型）"

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        url = (
            "https://api.duckduckgo.com/?format=json&no_html=1&skip_disambig=1&q="
            + quote_plus(query)
        )
        async with self._client() as client:
            response = await client.get(url, headers={"User-Agent": "AIWorld/1.0"})
            response.raise_for_status()
            data = response.json()
        results: list[SearchResult] = []
        if data.get("AbstractText"):
            results.append(
                SearchResult(
                    title=str(data.get("Heading") or query),
                    url=str(data.get("AbstractURL") or "https://duckduckgo.com/"),
                    snippet=strip_html(str(data.get("AbstractText"))),
                    provider=self.name,
                )
            )
        for topic in data.get("RelatedTopics") or []:
            if len(results) >= limit:
                break
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(
                    SearchResult(
                        title=str(topic.get("Text"))[:60],
                        url=str(topic.get("FirstURL") or ""),
                        snippet=strip_html(str(topic.get("Text"))),
                        provider=self.name,
                    )
                )
        return results[:limit]


class SearxngProvider(SearchProvider):
    name = "searxng"
    label = "SearXNG 元搜索（免费；需填实例地址，国内可自建）"
    requires_base_url = True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        url = self.base_url.rstrip("/") + "/search?format=json&q=" + quote_plus(query)
        async with self._client() as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=strip_html(str(item.get("content", "")))[:400],
                provider=self.name,
            )
            for item in (data.get("results") or [])[:limit]
        ]


class TavilyProvider(SearchProvider):
    name = "tavily"
    label = "Tavily（需 Key，聚合式，适合 Agent）"
    requires_key = True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with self._client() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=strip_html(str(item.get("content", "")))[:400],
                provider=self.name,
            )
            for item in (data.get("results") or [])[:limit]
        ]


class SerperProvider(SearchProvider):
    name = "serper"
    label = "Serper（需 Key，Google 结果）"
    requires_key = True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with self._client() as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"q": query, "num": limit},
            )
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=strip_html(str(item.get("snippet", "")))[:400],
                provider=self.name,
            )
            for item in (data.get("organic") or [])[:limit]
        ]


PROVIDER_CLASSES: dict[str, type[SearchProvider]] = {
    cls.name: cls for cls in (
        WikipediaProvider,
        DuckDuckGoProvider,
        SearxngProvider,
        TavilyProvider,
        SerperProvider,
    )
}


def available_providers() -> list[dict]:
    """给界面用的 provider 清单。"""
    return [
        {
            "name": cls.name,
            "label": cls.label,
            "requires_key": cls.requires_key,
            "requires_base_url": cls.requires_base_url,
        }
        for cls in PROVIDER_CLASSES.values()
    ]


class WebSearchTool:
    """并行检索多个 provider，去重合并；单个失败不影响整体。"""

    def __init__(
        self,
        providers: list[str] | None = None,
        *,
        api_key: str = "",
        base_url: str = "",
        timeout: float | None = None,
        per_provider: int | None = None,
        max_results: int | None = None,
    ) -> None:
        names = providers if providers is not None else settings.search_provider_list
        self.timeout = timeout or settings.search_timeout_seconds
        self.per_provider = per_provider or settings.search_results_per_provider
        self.max_results = max_results or settings.search_max_results
        self.providers: list[SearchProvider] = []
        for name in names:
            cls = PROVIDER_CLASSES.get(name.strip().lower())
            if cls is None:
                continue
            self.providers.append(
                cls(api_key=api_key, base_url=base_url, timeout=self.timeout)
            )

    @property
    def enabled(self) -> bool:
        return bool([item for item in self.providers if item.configured])

    async def _run_one(self, provider: SearchProvider, query: str) -> tuple[ProviderReport, list[SearchResult]]:
        started = time.perf_counter()
        try:
            results = await provider.search(query, self.per_provider)
            elapsed = int((time.perf_counter() - started) * 1000)
            return (
                ProviderReport(provider=provider.name, ok=True, count=len(results), elapsed_ms=elapsed),
                results,
            )
        except Exception as exc:  # 超时、被墙、解析失败……一律降级
            elapsed = int((time.perf_counter() - started) * 1000)
            logger.warning("provider %s 检索失败: %s", provider.name, exc)
            return (
                ProviderReport(
                    provider=provider.name,
                    ok=False,
                    error=type(exc).__name__ + ": " + str(exc)[:120],
                    elapsed_ms=elapsed,
                ),
                [],
            )

    async def search(self, query: str) -> SearchOutcome:
        active = [item for item in self.providers if item.configured]
        if not query.strip() or not active:
            return SearchOutcome()

        outcomes = await asyncio.gather(*[self._run_one(item, query) for item in active])

        merged: list[SearchResult] = []
        seen: set[str] = set()
        reports: list[ProviderReport] = []
        for report, results in outcomes:
            reports.append(report)
            for item in results:
                key = (item.url or item.title).strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return SearchOutcome(results=merged[: self.max_results], reports=reports)
