"""澶栭儴涓栫晫鎺ュ彛娴嬭瘯锛堜笉鑱旂綉锛氱敤鍋?HTTP 鍝嶅簲 + 鍋?provider锛夈€?
楠岃瘉锛?1. 浜斾釜 provider 鐨勫搷搴旇В鏋愶紙缁村熀 / DuckDuckGo / SearXNG / Tavily / Serper锛?2. 澶?provider **骞惰**鎵ц锛堟€昏€楁椂 鈮?鏈€鎱㈢殑閭ｄ釜锛岃€屼笉鏄疮鍔狅級
3. 鍗曚釜 provider 澶辫触/瓒呮椂浼?*闈欓粯闄嶇骇**锛屼笉褰卞搷鍏跺畠鏉ユ簮
4. 璺ㄦ潵婧愭寜 URL 鍘婚噸銆佹寜 provider 椤哄簭鍚堝苟
5. 绌烘煡璇?/ 鏃犲彲鐢ㄦ潵婧愭椂鐨勮竟鐣岃涓?
鐢ㄦ硶锛?    python tests/web_search_test.py
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
    return asyncio.run(provider.search("娴嬭瘯", limit))


def test_parsers() -> None:
    print("== 1. 鍚?provider 鍝嶅簲瑙ｆ瀽 ==")
    wiki = parse_with(
        WikipediaProvider,
        {"query": {"search": [{"title": "浜哄伐鏅鸿兘", "snippet": "<span>AI</span> 绠€浠?}]}},
    )
    check("缁村熀鐧剧瑙ｆ瀽", len(wiki) == 1 and wiki[0].title == "浜哄伐鏅鸿兘", str(wiki))
    check("缁村熀鐧剧鍘婚櫎 HTML", wiki[0].snippet == "AI 绠€浠?, wiki[0].snippet if wiki else "")

    ddg = parse_with(
        DuckDuckGoProvider,
        {"Heading": "AI", "AbstractText": "浜哄伐鏅鸿兘绠€浠?, "AbstractURL": "https://example.com/a"},
    )
    check("DuckDuckGo 瑙ｆ瀽", len(ddg) == 1 and ddg[0].url == "https://example.com/a", str(ddg))

    searx = parse_with(
        SearxngProvider,
        {"results": [{"title": "T", "url": "https://e.com/1", "content": "C"}]},
    )
    check("SearXNG 瑙ｆ瀽", len(searx) == 1 and searx[0].title == "T", str(searx))

    tavily = parse_with(
        TavilyProvider, {"results": [{"title": "T2", "url": "https://e.com/2", "content": "C2"}]}
    )
    check("Tavily 瑙ｆ瀽", len(tavily) == 1 and tavily[0].url == "https://e.com/2", str(tavily))

    serper = parse_with(
        SerperProvider, {"organic": [{"title": "T3", "link": "https://e.com/3", "snippet": "S3"}]}
    )
    check("Serper 瑙ｆ瀽", len(serper) == 1 and serper[0].url == "https://e.com/3", str(serper))


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
            raise TimeoutError("妯℃嫙瓒呮椂")
        return list(self._results)


QUERY = "闃挎煆瑗挎櫘閫傚簲鐥?


def make(name, url, *, fail=False, delay=0.0):
    # 鐩稿叧鎬ц繃婊ゅ紑鍚悗锛屽亣缁撴灉蹇呴』涓庢煡璇㈢浉鍏筹紝鍚﹀垯浼氳姝ｇ‘涓㈠純
    return FakeProvider(
        results=[]
        if fail
        else [
            SearchResult(
                title=name + " 路 闃挎煆瑗挎櫘閫傚簲鐥?,
                url=url,
                snippet="闃挎煆瑗挎櫘閫傚簲鐥囧寘鎷硸灏跨梾榛勬枒姘磋偪涓庢柊鐢熻绠℃€ч粍鏂戝彉鎬с€?,
                provider=name,
            )
        ],
        fail=fail,
        delay=delay,
        name=name,
    )


def test_orchestration() -> None:
    print("\n== 2. 骞惰鎵ц锛堟€昏€楁椂 鈮?鏈€鎱㈣€咃級==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("a", "https://a", delay=0.3), make("b", "https://b", delay=0.3)]
    started = time.perf_counter()
    outcome = asyncio.run(tool.search(QUERY))
    elapsed = time.perf_counter() - started
    check("涓や釜 0.3s 鏉ユ簮骞惰瀹屾垚锛?0.55s锛?, elapsed < 0.55, "瀹為檯 " + str(round(elapsed, 2)) + "s")
    check("涓や釜鏉ユ簮缁撴灉閮芥嬁鍒?, len(outcome.results) == 2, str(len(outcome.results)))

    print("\n== 3. 鍗曟潵婧愬け璐ラ潤榛橀檷绾?==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("good", "https://good"), make("bad", "https://bad", fail=True)]
    outcome = asyncio.run(tool.search(QUERY))
    check("澶辫触鏉ユ簮涓嶅奖鍝嶆垚鍔熺殑", [item.provider for item in outcome.results] == ["good"], str(outcome.results))
    bad_report = [item for item in outcome.reports if item.provider == "bad"][0]
    check("澶辫触鐘舵€佸瀹炰笂鎶?, bad_report.ok is False and bool(bad_report.error), str(bad_report))

    print("\n== 4. 鍘婚噸涓庨『搴?==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [
        make("first", "https://same"),
        make("second", "https://same"),
        make("third", "https://other"),
    ]
    outcome = asyncio.run(tool.search(QUERY))
    check("鐩稿悓 URL 鍙繚鐣欑涓€鏉?, len(outcome.results) == 2, str([item.url for item in outcome.results]))
    check("淇濇寔 provider 椤哄簭", outcome.results[0].provider == "first", str(outcome.results[0]))

    print("\n== 5. 杈圭晫 ==")
    tool = WebSearchTool(["wikipedia"])
    tool.providers = [make("a", "https://a")]
    check("绌烘煡璇㈣繑鍥炵┖", asyncio.run(tool.search("   ")).results == [])
    tool.providers = [make("bad", "https://bad", fail=True)]
    check("鍏ㄩ儴澶辫触涔熶笉鎶涘紓甯?, asyncio.run(tool.search(QUERY)).results == [])


def main() -> int:
    test_parsers()
    test_orchestration()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("澶栭儴妫€绱紙澶氭潵婧愬苟琛?+ 澶辫触闄嶇骇 + 鍘婚噸锛夐獙璇侀€氳繃")
    return 0


if __name__ == "__main__":
    sys.exit(main())
