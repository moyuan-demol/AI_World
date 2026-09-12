"""多 Agent RAG：把「搜一次就作答」升级为团队协作工作流。

流程（与业界多 Agent RAG 实践一致）：

    问题拆解 Agent  ->  把复杂问题拆成若干子问题（降低理解难度）
    资料查找 Agent  ->  逐个检索知识库并去重（扩大召回面）
    证据审查 Agent  ->  判断证据是否充分/一致；不足则提出补充检索（有界循环）
    答案整理 Agent  ->  综合证据生成带引用的最终回答

设计原则：
- 完全复用现有 rag/ 基础设施（Retriever），**不改变原有单轮 RAG 行为**（默认仍是 single）
- 有界：子问题数上限、审查-补检轮次上限，避免 token / 费用失控
- 无模型 Key 时，每个阶段都有确定性离线兜底，流程照样跑通、界面照样展示
- 本模块不依赖 app.ai.prompts，保持 RAG 包自包含，避免循环导入
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

DECOMPOSE_SYSTEM = (
    "你是「问题拆解 Agent」。把用户问题拆成 1-4 个更具体、可独立检索的子问题，"
    "覆盖问题里不同的侧面（背景、数据、风险、结论等）。"
    "只输出 JSON 字符串数组，例如 [\"子问题1\",\"子问题2\"]，不要任何解释。"
)

REVIEW_SYSTEM = (
    "你是「证据审查 Agent」，职责是审稿而不是写答案。"
    "判断这些证据是否足以可靠地回答用户问题：是否覆盖了问题要点、是否互相矛盾、是否缺少关键信息。"
    "只输出 JSON 对象："
    "{\"sufficient\": true/false, \"reason\": \"一句话理由\", \"missing\": [\"缺失点\"], "
    "\"followups\": [\"需要补充检索的查询\"]}"
)

SYNTHESIZE_SYSTEM = (
    "你是「答案整理 Agent」。基于给出的证据与审查意见，生成最终回答。"
    "要求：先给结论，再分点论述；引用证据时标注编号如 [1]；"
    "证据不足时明确说明不确定之处，不要编造。"
)

MAX_SUB_QUESTIONS = 4
MAX_ROUNDS = 2
MAX_EVIDENCE_CHARS = 6000


@dataclass
class AgentStep:
    agent: str
    role: str
    output: str


@dataclass
class MultiAgentResult:
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    rounds: int = 1
    sufficient: bool = False
    review_reason: str = ""
    sources: list[RetrievedChunk] = field(default_factory=list)
    model: str = ""
    offline: bool = False


def parse_json_array(text: str) -> list:
    """稳健解析模型输出的 JSON 数组。"""
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_json_object(text: str) -> dict:
    """稳健解析模型输出的 JSON 对象。"""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def format_evidence(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "（没有检索到任何知识库证据）"
    lines = []
    for index, chunk in enumerate(chunks, start=1):
        lines.append("[" + str(index) + "] 来源：" + chunk.filename)
        lines.append(chunk.content.strip())
        lines.append("")
    text = "\n".join(lines)
    return text[:MAX_EVIDENCE_CHARS]


def merge_chunks(*groups: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """按 document_id 去重合并，保留最高分。"""
    best: dict[int, RetrievedChunk] = {}
    for group in groups:
        for chunk in group:
            current = best.get(chunk.document_id)
            if current is None or chunk.score > current.score:
                best[chunk.document_id] = chunk
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


class MultiAgentRag:
    """多 Agent RAG 工作流。

    retrieve: 异步检索函数 (query) -> list[RetrievedChunk]，由调用方注入（通常是 RagService）
    """

    def __init__(
        self,
        ai,
        retrieve: Callable[[str], Awaitable[list[RetrievedChunk]]],
        system_prompt: str = "",
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self.ai = ai
        self.retrieve = retrieve
        self.system_prompt = system_prompt
        self.max_rounds = max(1, min(int(max_rounds or MAX_ROUNDS), 5))

    @property
    def offline(self) -> bool:
        return not getattr(self.ai, "is_configured", False)

    async def _decompose(self, question: str) -> tuple[list[str], AgentStep]:
        if self.offline:
            return [question], AgentStep(
                agent="问题拆解 Agent",
                role="拆分问题",
                output="（离线模式）未拆解，直接使用原问题：" + question,
            )
        result = await self.ai.chat(
            [
                {"role": "system", "content": DECOMPOSE_SYSTEM},
                {"role": "user", "content": question},
            ],
            temperature=0.2,
            max_tokens=400,
            offline_fallback=lambda: question,
        )
        items = [str(item).strip() for item in parse_json_array(result.text) if str(item).strip()]
        subs = items[:MAX_SUB_QUESTIONS] or [question]
        step = AgentStep(
            agent="问题拆解 Agent",
            role="拆分问题",
            output="\n".join("- " + item for item in subs),
        )
        return subs, step

    async def _gather(self, queries: list[str]) -> list[RetrievedChunk]:
        groups: list[list[RetrievedChunk]] = []
        for query in queries:
            if not query.strip():
                continue
            try:
                groups.append(await self.retrieve(query))
            except Exception:
                logger.exception("检索失败: %s", query)
        return merge_chunks(*groups)

    async def _review(self, question: str, chunks: list[RetrievedChunk]) -> dict:
        if self.offline:
            sufficient = bool(chunks)
            return {
                "sufficient": sufficient,
                "reason": "（离线模式）依据检索命中数量判断",
                "missing": [],
                "followups": [],
            }
        result = await self.ai.chat(
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {
                    "role": "user",
                    "content": "用户问题：" + question + "\n\n证据：\n" + format_evidence(chunks),
                },
            ],
            temperature=0.1,
            max_tokens=400,
            offline_fallback=lambda: "{}",
        )
        data = parse_json_object(result.text)
        if not data:
            return {"sufficient": bool(chunks), "reason": "（审查输出无法解析）", "followups": []}
        return data

    async def _synthesize(
        self, question: str, chunks: list[RetrievedChunk], review: dict
    ) -> tuple[str, AgentStep]:
        evidence = format_evidence(chunks)
        review_text = (
            "充分性：" + ("足够" if review.get("sufficient") else "不足")
            + "；理由：" + str(review.get("reason", ""))
            + ("；缺失：" + "、".join(str(x) for x in review.get("missing", [])) if review.get("missing") else "")
        )
        messages = [
            {"role": "system", "content": (self.system_prompt + "\n\n" + SYNTHESIZE_SYSTEM).strip()},
            {
                "role": "user",
                "content": "用户问题：" + question + "\n\n证据：\n" + evidence + "\n\n审查意见：" + review_text,
            },
        ]
        result = await self.ai.chat(
            messages,
            temperature=0.4,
            offline_fallback=lambda: self._offline_answer(question, chunks),
        )
        step = AgentStep(
            agent="答案整理 Agent",
            role="综合成稿",
            output=result.text[:500],
        )
        return result.text, step

    @staticmethod
    def _offline_answer(question: str, chunks: list[RetrievedChunk]) -> str:
        lines = [
            "【离线演示模式】多 Agent RAG 已完成「拆解 → 查找 → 审查 → 整理」流程，",
            "但未配置模型 Key，以下为本地汇总：",
            "",
            "问题：" + question.strip(),
            "",
        ]
        if chunks:
            lines.append("证据（按相关度排序）：")
            for index, chunk in enumerate(chunks[:5], start=1):
                snippet = chunk.content.strip().replace("\n", " ")[:140]
                lines.append("[" + str(index) + "] " + chunk.filename + "：" + snippet)
        else:
            lines.append("没有检索到知识库证据。")
        lines.append("")
        lines.append("配置 API Key（或使用自带的 Key）后，这里会输出真正的综合分析。")
        return "\n".join(lines)

    async def run(self, question: str) -> MultiAgentResult:
        steps: list[AgentStep] = []

        # 1) 拆解
        sub_questions, decompose_step = await self._decompose(question)
        steps.append(decompose_step)

        # 2) 查找（原问题 + 子问题）
        chunks = await self._gather([question] + sub_questions)
        steps.append(
            AgentStep(
                agent="资料查找 Agent",
                role="检索知识库",
                output="检索 " + str(len(sub_questions) + 1) + " 个查询，去重后命中 "
                + str(len(chunks)) + " 条证据",
            )
        )

        # 3) 审查 + 有界补检
        review = await self._review(question, chunks)
        rounds = 1
        while (
            not review.get("sufficient")
            and rounds < self.max_rounds
            and review.get("followups")
        ):
            rounds += 1
            followups = [str(item) for item in review.get("followups", [])][:MAX_SUB_QUESTIONS]
            more = await self._gather(followups)
            chunks = merge_chunks(chunks, more)
            review = await self._review(question, chunks)

        steps.append(
            AgentStep(
                agent="证据审查 Agent",
                role="审稿/判定充分性",
                output=("充分" if review.get("sufficient") else "仍不充分")
                + "｜轮次 " + str(rounds) + "｜" + str(review.get("reason", "")),
            )
        )

        # 4) 整理
        answer, synthesize_step = await self._synthesize(question, chunks, review)
        steps.append(synthesize_step)

        return MultiAgentResult(
            answer=answer,
            steps=steps,
            sub_questions=sub_questions,
            rounds=rounds,
            sufficient=bool(review.get("sufficient")),
            review_reason=str(review.get("reason", "")),
            sources=chunks,
            model="offline-demo" if self.offline else getattr(self.ai, "model", ""),
            offline=self.offline,
        )
