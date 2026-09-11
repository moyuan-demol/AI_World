"""Deterministic offline answers used when no DeepSeek key is configured.

They keep the whole product demo-able (chat, RAG, round table) without any
external API, and make it obvious to the user that the model was not called.
"""

from collections.abc import Sequence

from app.rag.retriever import RetrievedChunk


def offline_chat_answer(
    character_name: str,
    character_role: str,
    question: str,
    chunks: Sequence[RetrievedChunk],
) -> str:
    lines = [
        "【离线演示模式】未检测到 DEEPSEEK_API_KEY，本回答由本地模板生成。",
        "",
        "角色：" + character_name + ("（" + character_role + "）" if character_role else ""),
        "问题：" + question.strip(),
        "",
    ]
    if chunks:
        lines.append("检索到 " + str(len(chunks)) + " 条知识库片段，最相关的内容摘要：")
        for index, chunk in enumerate(chunks, start=1):
            snippet = chunk.content.strip().replace("\n", " ")[:160]
            lines.append(str(index) + ". [" + chunk.filename + "] " + snippet)
        lines.append("")
        lines.append("配置 API Key 后，模型会基于以上片段组织完整的专业回答。")
    else:
        lines.append("当前没有任何知识库命中，回答将完全依赖角色设定。")
        lines.append("配置 API Key 后即可获得真实的模型回答。")
    return "\n".join(lines)


def offline_agent_answer(agent: str, role: str, question: str, brief: str) -> str:
    return "\n".join(
        [
            "【离线演示模式】" + agent + "（" + role + "）的占位发言。",
            "",
            "议题：" + question.strip(),
            "主持人拆解：" + (brief.strip()[:200] if brief.strip() else "（无）"),
            "",
            "配置 DEEPSEEK_API_KEY 后，该 Agent 会按照其角色设定输出专业的分析、"
            "风险提示与可执行建议。",
        ]
    )


def offline_summary(question: str, answers: Sequence[tuple[str, str]]) -> str:
    lines = [
        "【离线演示模式】主持人汇总（本地模板）。",
        "",
        "议题：" + question.strip(),
        "",
        "各角色发言要点：",
    ]
    for agent, answer in answers:
        lines.append("- " + agent + "：" + answer.strip().replace("\n", " ")[:120])
    lines.append("")
    lines.append("配置 DEEPSEEK_API_KEY 后，这里会输出结构化的最终方案。")
    return "\n".join(lines)
