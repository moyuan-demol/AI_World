"""外部世界接口测试（不联网：用假 HTTP 响应 + 假 provider）。

验证：
1. 五个 provider 的响应解析（维基 / DuckDuckGo / SearXNG / Tavily / Serper）
2. 多 provider **并行**执行（总耗时 ≈ 最慢的那个，而不是累加）
3. 单个 provider 失败/超时会**静默降级**，不影响其它来源
4. 跨来源按 URL 去重、按 provider 顺序合并
5. 空查询 / 无可用来源时的边界行为

用法：
    python tests/web_search_test.py
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tools.web_search import (  # noqa: E402
    DuckDuckGoProvider,
    SearchProvider,
    SearchResult,
    SearxngProvider,
    SerperProvider,
    TavilyProvider,
    WebSearchTool,
    WikipediaProvider,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return FakeResponse(self._data)

    async def post(self, *args, **kwargs):
        return FakeResponse(self._data)


def parse_with(provider_cls, canned, limit=3):
    provider = provider_cls()
    provider._client = lambda: FakeClient(canned)  # type: ignore[assignment]
    return asyncio.run(provider.search("测试", limit))


def test_parsers() -> None:
    print("== 1. 各 provider 响应解析 ==")
    wiki = parse_with(
        WikipediaProvider,
        {"query": {"search": [{"title": "人工智能", "snippet": "<span>AI</span> 简介"}]}},
    )
    check("维基百科解析", len(wiki) == 1 and wiki[0].title == "人工智能", str(wiki))
    check("维基百科去除 HTML", wiki[0].snippet == "AI 简介", wiki[0].snippet if wiki else "")

    ddg = parse_with(
        DuckDuckGoProvider,
        {"Heading": "AI", "AbstractText": "人工智能简介", "AbstractURL": "https://example.com/a"},
    )
    check("DuckDuckGo 解析", len(ddg) == 1 and ddg[0].url == "https://example.com/a", str(ddg))

    searx = parse_with(
        SearxngProvider,
        {"results": [{"title": "T", "url": "https://e.com/1", "content": "C"}]},
    )
    check("SearXNG 解析", len(searx) == 1 and searx[0].title == "T", str(searx))

    tavily = parse_with(
        TavilyProvider, {"results": [{"title": "T2", "url": "https://e.com/2", "content": "C2"}]}
    )
    check("Tavily 解析", len(tavily) == 1 and tavily[0].url == "https://e.com/2", str(tavily))

    serper = parse_with(
        SerperProvider, {"organic": [{"title": "T3", "link": "https://e.com/3", "snippet": "S3"}]}
    )
    check("Serper 解析", len(serper) == 1 and serper[0].url == "https://e.com/3", str(serper))


class FakeProvider(SearchProvider):
    name = "fake"
    label = "fake"

    def __init__(self, *, results=None, fail=False, delay=0.0, name="fake"):  # noqa: ANN001
        super().__init__()
        self._results = results or []
        self._fail = fail
        self._delay = delay
        self.name = name
        self.label = name

    async def search(self, query: str, limit: int):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise TimeoutError("模拟超时")
        return list(self._results)


QUERY = "阿柏西普适应症"


def make(name, url, *, fail=False, delay=0.0):
    # 相关性过滤开启后，假结果必须与查询相关，否则会被正确丢弃
    return FakeProvider(
        results=[]
        if fail
        else [
            SearchResult(
                title=name + " · 阿柏西普适应症",
                url=url,
                snippet="阿柏西普适应症包括糖尿病黄斑水肿与新生血管性黄斑变性。",
                provider=name,
            )
        ],
        fail=fail,
        delay=delay,
        name=name,
    )


def test_orchestration() -> None:
    print("\n== 2. 并行执行（总耗时 ≈ 最慢者）==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("a", "https://a", delay=0.3), make("b", "https://b", delay=0.3)]
    started = time.perf_counter()
    outcome = asyncio.run(tool.search(QUERY))
    elapsed = time.perf_counter() - started
    check("两个 0.3s 来源并行完成（<0.55s）", elapsed < 0.55, "实际 " + str(round(elapsed, 2)) + "s")
    check("两个来源结果都拿到", len(outcome.results) == 2, str(len(outcome.results)))

    print("\n== 3. 单来源失败静默降级 ==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("good", "https://good"), make("bad", "https://bad", fail=True)]
    outcome = asyncio.run(tool.search(QUERY))
    check("失败来源不影响成功的", [item.provider for item in outcome.results] == ["good"], str(outcome.results))
    bad_report = [item for item in outcome.reports if item.provider == "bad"][0]
    check("失败状态如实上报", bad_report.ok is False and bool(bad_report.error), str(bad_report))

    print("\n== 4. 去重与顺序 ==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [
        make("first", "https://same"),
        make("second", "https://same"),
        make("third", "https://other"),
    ]
    outcome = asyncio.run(tool.search(QUERY))
    check("相同 URL 只保留第一条", len(outcome.results) == 2, str([item.url for item in outcome.results]))
    check("保持 provider 顺序", outcome.results[0].provider == "first", str(outcome.results[0]))

    print("\n== 5. 边界 ==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("a", "https://a")]
    check("空查询返回空", asyncio.run(tool.search("   ")).results == [])
    tool.providers = [make("bad", "https://bad", fail=True)]
    check("全部失败也不抛异常", asyncio.run(tool.search(QUERY)).results == [])


def main() -> int:
    test_parsers()
    test_orchestration()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("外部检索（多来源并行 + 失败降级 + 去重）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
