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
        "【离线模式】未检测到 DEEPSEEK_API_KEY —— 以下是检索到的【原始资料】，未经模型加工（不是答案）。",
        "",
        "角色：" + character_name + ("（" + character_role + "）" if character_role else ""),
        "问题：" + question.strip(),
        "",
    ]
    if chunks:
        lines.append("检索到 " + str(len(chunks)) + " 条资料片段，原文摘要如下（如需成段回答，请配置 API Key）：")
        for index, chunk in enumerate(chunks, start=1):
            snippet = chunk.content.strip().replace("\n", " ")[:160]
            lines.append(str(index) + ". [" + chunk.filename + "] " + snippet)
        lines.append("")
        lines.append("配置 API Key 后，模型会基于以上片段组织完整的专业回答。")
    else:
        lines.append(
            "我是 "
            + character_name
            + ("（" + character_role + "）" if character_role else "")
            + "，很高兴见到你。"
        )
        lines.append(
            "知识库里没有与这个问题相关的内容（低于相关性阈值的片段不会被列出，"
            "以免给你不沾边的资料）。"
        )
        lines.append("你可以直接问我这个领域的问题；配置 API Key 后我会给出更完整的回答。")
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
