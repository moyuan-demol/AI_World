"""多 Agent RAG 工作流测试（不联网、不依赖数据库，结果确定）。

验证：
1. 四个 Agent 按序执行（拆解 -> 查找 -> 审查 -> 整理）
2. 子问题解析与检索调用（原问题 + 每个子问题）
3. 审查判定「不充分」时触发补检，且轮次有界
4. 最终答案来自「答案整理 Agent」
5. 离线模式（无 Key）仍能跑通全流程

用法：
    python tests/multi_agent_test.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.deepseek_client import AIResult  # noqa: E402
from app.rag.multi_agent import (  # noqa: E402
    MultiAgentRag,
    parse_json_array,
    parse_json_object,
)
from app.rag.retriever import RetrievedChunk  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeAI:
    """按 system prompt 扮演各 Agent；第一次审查故意判「不充分」以触发补检。"""

    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.review_count = 0

    @property
    def is_configured(self) -> bool:
        return self.configured

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        # 忠实模拟真实 AIClient 的契约：未配置 Key 时使用 offline_fallback
        if not self.configured:
            fallback = kwargs.get("offline_fallback")
            return AIResult(
                text=fallback() if fallback else "（离线）", model="offline-demo", offline=True
            )
        system = messages[0]["content"]
        if "问题拆解" in system:
            return AIResult(text='["子问题A","子问题B"]', model="fake")
        if "证据审查" in system:
            self.review_count += 1
            if self.review_count == 1:
                return AIResult(
                    text='{"sufficient": false, "reason": "缺少风险数据", '
                    '"missing": ["风险"], "followups": ["医疗AI风险"]}',
                    model="fake",
                )
            return AIResult(
                text='{"sufficient": true, "reason": "证据已充分", "missing": []}', model="fake"
            )
        return AIResult(text="最终回答：结论如下 [1]", model="fake")


def make_retrieve(calls: list[str], counter: list[int]):
    async def retrieve(query: str) -> list[RetrievedChunk]:
        calls.append(query)
        counter[0] += 1
        return [
            RetrievedChunk(
                document_id=counter[0],
                knowledge_id=1,
                filename="资料.txt",
                chunk_index=0,
                content="关于「" + query + "」的说明内容",
                score=0.5,
            )
        ]

    return retrieve


async def scenario_online() -> None:
    print("\n== 1. 在线模式：完整四段协作 + 有界补检 ==")
    calls: list[str] = []
    counter = [0]
    ai = FakeAI(configured=True)
    pipeline = MultiAgentRag(
        ai,
        make_retrieve(calls, counter),
        system_prompt="你是 AI 伙伴。",
        max_rounds=2,
    )
    result = await pipeline.run("如何开发一款医疗 AI 产品？")

    names = [step.agent for step in result.steps]
    check("按序执行四个 Agent", names == ["问题拆解 Agent", "资料查找 Agent", "证据审查 Agent", "答案整理 Agent"], str(names))
    check("子问题解析正确", result.sub_questions == ["子问题A", "子问题B"], str(result.sub_questions))
    check(
        "第一轮检索覆盖原问题与每个子问题",
        calls[:3] == ["如何开发一款医疗 AI 产品？", "子问题A", "子问题B"],
        str(calls[:3]),
    )
    check("审查判定不充分后触发补检", calls[3:] == ["医疗AI风险"], str(calls))
    check("轮次为 2（有界循环生效）", result.rounds == 2, str(result.rounds))
    check("第二轮审查判定充分", result.sufficient is True, result.review_reason)
    check("最终答案来自答案整理 Agent", result.answer.startswith("最终回答"), result.answer[:60])
    check("证据按 document_id 去重", len(result.sources) == len({c.document_id for c in result.sources}))
    check("未离线", result.offline is False)


async def scenario_bounded() -> None:
    print("\n== 2. 轮次上限：审查一直说不充分也不会无限循环 ==")

    class AlwaysUnsatisfied(FakeAI):
        async def chat(self, messages, **kwargs):  # noqa: ANN001
            system = messages[0]["content"]
            if "问题拆解" in system:
                return AIResult(text='["子问题A"]', model="fake")
            if "证据审查" in system:
                return AIResult(
                    text='{"sufficient": false, "reason": "永远不充分", "followups": ["再查"]}',
                    model="fake",
                )
            return AIResult(text="勉强作答", model="fake")

    calls: list[str] = []
    pipeline = MultiAgentRag(
        AlwaysUnsatisfied(configured=True),
        make_retrieve(calls, [0]),
        max_rounds=3,
    )
    result = await pipeline.run("问题")
    check("轮次不超过上限", result.rounds <= 3, str(result.rounds))
    check("仍然返回答案", bool(result.answer))


async def scenario_offline() -> None:
    print("\n== 3. 离线模式（无 Key）：流程照样跑通 ==")
    calls: list[str] = []
    pipeline = MultiAgentRag(
        FakeAI(configured=False),
        make_retrieve(calls, [0]),
        system_prompt="你是 AI 伙伴。",
        max_rounds=2,
    )
    result = await pipeline.run("医疗 AI 的合规风险是什么？")
    check("离线仍执行四个阶段", len(result.steps) == 4, str(len(result.steps)))
    check("标记为离线", result.offline is True)
    check("离线答案有明确标注", "离线演示模式" in result.answer, result.answer[:80])
    check("离线也完成了检索", len(calls) >= 1, str(calls))


async def scenario_parsers() -> None:
    print("\n== 4. JSON 解析健壮性 ==")
    check("数组解析", parse_json_array('好的 ["a","b"] 完') == ["a", "b"])
    check("数组非法返回空", parse_json_array("没有数组") == [])
    check("对象解析", parse_json_object('前 {"sufficient": true} 后') == {"sufficient": True})
    check("对象非法返回空", parse_json_object("{}") == {})


async def main_async() -> int:
    await scenario_online()
    await scenario_bounded()
    await scenario_offline()
    await scenario_parsers()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("多 Agent RAG（拆解 / 查找 / 审查 / 整理）验证通过")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
