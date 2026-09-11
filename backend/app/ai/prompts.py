"""Prompt construction for characters, RAG answers and the round table."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.models.character import Character

if TYPE_CHECKING:  # avoid a runtime import cycle with app.rag
    from app.rag.retriever import RetrievedChunk

BASE_ASSISTANT_RULES = (
    "你是 AI World 中的 AI 伙伴，回答要专业、准确、条理清晰，使用中文。\n"
    "当提供了知识库片段时，优先依据片段回答，并在引用处标注来源编号，例如 [1]。\n"
    "如果知识库中没有相关信息，请明确说明，不要编造事实。"
)

DEFAULT_ROUNDTABLE_AGENTS: list[dict[str, str]] = [
    {
        "agent": "产品经理",
        "role": "需求分析与产品定义",
        "goal": "澄清用户问题，输出目标用户、核心场景、功能范围与优先级",
        "personality": "善于提问，关注用户价值，输出结构化的需求清单",
    },
    {
        "agent": "技术专家",
        "role": "架构设计与技术选型",
        "goal": "给出系统架构、关键技术选型、数据流与工程风险",
        "personality": "严谨、直接，关注可行性、性能与成本",
    },
    {
        "agent": "商业顾问",
        "role": "市场与 ROI 分析",
        "goal": "分析市场规模、竞争格局、商业模式与投入产出",
        "personality": "数据驱动，关注盈利路径与风险收益比",
    },
]


def build_character_system_prompt(character: Character) -> str:
    parts = [BASE_ASSISTANT_RULES, "", "你的角色信息："]
    parts.append("名称：" + (character.name or "AI 伙伴"))
    if character.role:
        parts.append("身份：" + character.role)
    if character.personality:
        parts.append("人格：" + character.personality)
    if character.expertise:
        parts.append("擅长领域：" + character.expertise)
    if character.speaking_style:
        parts.append("说话方式：" + character.speaking_style)
    if character.system_prompt:
        parts.append("")
        parts.append("补充设定：")
        parts.append(character.system_prompt)
    return "\n".join(parts)


def build_context_block(chunks: Sequence[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    lines = ["以下是检索到的知识库片段，请结合它们回答问题："]
    for index, chunk in enumerate(chunks, start=1):
        lines.append("")
        lines.append("[" + str(index) + "] 来源文件：" + chunk.filename)
        lines.append(chunk.content.strip())
    return "\n".join(lines)


def build_rag_user_message(question: str, context_block: str) -> str:
    if not context_block:
        return question
    return context_block + "\n\n用户问题：" + question


def build_agent_prompt(agent: str, role: str, goal: str, personality: str) -> str:
    lines = [
        "你是 AI 圆桌会议的与会专家。",
        "角色名称：" + agent,
        "职责：" + role,
    ]
    if goal:
        lines.append("你的目标：" + goal)
    if personality:
        lines.append("你的风格：" + personality)
    lines.append("")
    lines.append("请只代表你自己的角色发言，不要替其他角色说话。")
    lines.append("输出要求：先给出结论，再给出 3-5 条要点，最后给出一个风险提示。")
    lines.append("控制在 400 字以内，使用中文。")
    return "\n".join(lines)


def build_manager_brief_prompt(question: str) -> str:
    return (
        "你是 AI 圆桌会议的主持人（Manager Agent）。\n"
        "请把下面的议题拆解成 3 个最关键的讨论子问题，并说明每个子问题的关注点。\n"
        "输出格式：每个子问题一行，以短横线开头，控制在 200 字以内。\n\n"
        "议题：" + question
    )


def build_manager_summary_prompt(question: str, answers: Sequence[tuple[str, str]]) -> str:
    lines = [
        "你是 AI 圆桌会议的主持人（Manager Agent），请汇总与会专家的发言，形成最终方案。",
        "",
        "原始议题：" + question,
        "",
        "与会专家发言：",
    ]
    for agent, answer in answers:
        lines.append("")
        lines.append("### " + agent)
        lines.append(answer.strip())
    lines.append("")
    lines.append(
        "请输出：\n"
        "1. 核心结论（3 条以内）\n"
        "2. 推荐方案（分步骤）\n"
        "3. 主要风险与应对\n"
        "4. 下一步行动清单\n"
        "使用中文，Markdown 格式输出。"
    )
    return "\n".join(lines)


def character_to_agent_spec(character: Character) -> dict[str, str]:
    return {
        "agent": character.name,
        "role": character.role or "AI 专家",
        "goal": character.expertise or character.role or "从你的专业角度分析问题",
        "personality": character.personality or character.speaking_style or "",
    }
