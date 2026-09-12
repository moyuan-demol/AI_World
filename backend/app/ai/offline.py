"""Deterministic offline answers used when no DeepSeek key is configured.

They keep the whole product demo-able (chat, RAG, round table) without any
external API, and make it obvious to the user that the model was not called.

离线回答的呈现原则（为什么这样组织）：
- 检索本身已经是对的，缺的是"可读性"：把每条片段开头 160 字原样摊开，会让
  用户在几十行里反复看到同一个长达 60 字的文件名，既读不出重点，也会误判成
  "检索质量差"。
- 所以离线兜底只做"挑选 + 排版"：先用问题的实词在片段里挑出最相关的 1~2 句，
  再按文档分组（文件名只出现一次），最后逐行展示。**不改动任何检索逻辑**。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.rag.embedding import tokenize
from app.rag.rerank import meaningful_terms
from app.rag.retriever import RetrievedChunk

# 句子切分：在中文/英文句末标点处切开，**保留标点**。
# 为什么保留标点：它是"句子到哪里结束"的证据，去掉后相邻句子会挤在一起更难读。
# 为什么把换行也算分隔符：PDF 抽出的标题行/作者行/摘要行之间常常没有句号。
_SENTENCE_RE = re.compile(r"[^。！？；.!?;\n]*[。！？；.!?;]|[^。！？；.!?;\n]+")

# 每个切片最多展示几句：只留最贴题的一两句，避免又变回"一坨"。
MAX_SENTENCES_PER_CHUNK = 2
# 单句展示上限：超长句截断加省略号（否则一句摘要就能把整个回答撑爆）。
MAX_SENTENCE_CHARS = 140
# 该块所有句子都没有实词命中（例如纯元信息块）时的兜底摘要长度。
MAX_FALLBACK_CHARS = 120
# 文件名前的图标：让"文档分组"一眼可辨。
_FILENAME_PREFIX = "📄 "
# 组内每行句子的前缀。
_SENTENCE_PREFIX = "· "


def split_sentences(text: str) -> list[str]:
    """纯函数：把一段正文切成句子（保留句末标点，过滤空白句/纯标点句）。

    - 只含标点的"句子"（例如连续句号切出来的 "。"）没有信息量，直接丢弃；
    - 这样作者行、摘要行即使没有标点，也能各自成为可独立打分/展示的单元。
    """
    sentences: list[str] = []
    for match in _SENTENCE_RE.finditer(text or ""):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        # isalnum() 对汉字同样成立，因此"含至少一个实字"才算有效句子
        if not any(char.isalnum() for char in sentence):
            continue
        sentences.append(sentence)
    return sentences


def _sentence_score(sentence: str, terms: Sequence[str]) -> float:
    """句子与问题的实词重合度（0~1）：命中种类数 / 问题实词种类数。

    只统计长度 >= 2 的实词（中文双字词 / 英文单词，来自 meaningful_terms），
    这样"是""的"这类常见单字同时出现在任何句子里也不会制造假命中。
    """
    if not terms:
        return 0.0
    tokens = set(tokenize(sentence))
    hits = [term for term in terms if term in tokens]
    if not hits:
        return 0.0
    return len(hits) / len(terms)


def _select_relevant_sentences(content: str, terms: Sequence[str]) -> list[str]:
    """内部实现：用预先算好的实词挑句，避免按切片重复 tokenize 问题。"""
    sentences = split_sentences(content or "")
    if not sentences or not terms:
        return []
    scored = [
        (_sentence_score(sentence, terms), index, sentence)
        for index, sentence in enumerate(sentences)
    ]
    matched = [item for item in scored if item[0] > 0.0]
    if not matched:
        # 整段都没有实词命中 -> 交给调用方回退成开头摘要
        return []
    # 先按分数降序取下限 N 句；再按下标升序还原原文顺序（阅读顺序优先于分数顺序）
    matched.sort(key=lambda item: (-item[0], item[1]))
    chosen = matched[:MAX_SENTENCES_PER_CHUNK]
    chosen.sort(key=lambda item: item[1])
    return [sentence for _score, _index, sentence in chosen]


def select_relevant_sentences(content: str, question: str) -> list[str]:
    """纯函数：从一段切片正文里挑出与问题最相关的 1~2 句（保持原文顺序）。

    返回空列表表示"整段都没有实词命中"，由调用方回退成开头摘要。
    """
    return _select_relevant_sentences(content or "", meaningful_terms(question or ""))


def summarize_chunk(content: str, limit: int = MAX_FALLBACK_CHARS) -> str:
    """纯函数：把切片压成一行摘要（该块没有相关句时的兜底）。"""
    flat = re.sub(r"\s+", " ", (content or "").strip())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "…"


def _clip_sentence(sentence: str) -> str:
    """单句展示上限：超长句截断并加省略号。"""
    text = (sentence or "").strip()
    if len(text) <= MAX_SENTENCE_CHARS:
        return text
    return text[:MAX_SENTENCE_CHARS].rstrip() + "…"


def _highlight_lines(chunks: Sequence[RetrievedChunk], question: str) -> list[str]:
    """按文件名分组，组内逐行展示提取出的句子（组内重复句子只留一份）。

    为什么这样分组：用户反馈的痛点正是"每行都重复完整文件名"——文件名只写一次
    之后，剩下的每一行都是真正的信息，扫读成本立刻降下来。
    """
    terms = meaningful_terms(question or "")
    groups: dict[str, list[str]] = {}
    order: list[str] = []

    for chunk in chunks:
        filename = (getattr(chunk, "filename", "") or "").strip() or "（未命名文档）"
        if filename not in groups:
            groups[filename] = []
            order.append(filename)
        body = groups[filename]

        content = getattr(chunk, "content", "") or ""
        highlights = _select_relevant_sentences(content, terms)
        if not highlights:
            # 纯元信息块 / 与问题无实词重合的块：给开头摘要，而不是整段搬出来
            summary = summarize_chunk(content)
            highlights = [summary] if summary else []
        for sentence in highlights:
            clipped = _clip_sentence(sentence)
            if clipped and clipped not in body:
                body.append(clipped)

    lines: list[str] = []
    for filename in order:
        body = groups[filename]
        if not body:
            continue
        if lines:
            lines.append("")  # 组间空行：把"一坨"拆成可扫读的条目
        lines.append(_FILENAME_PREFIX + filename)
        for sentence in body:
            lines.append(_SENTENCE_PREFIX + sentence)
    return lines


def offline_chat_answer(
    character_name: str,
    character_role: str,
    question: str,
    chunks: Sequence[RetrievedChunk],
) -> str:
    """离线兜底回答：相关句提取 + 按文档分组 + 去重，不加工成答案。"""
    items = list(chunks or [])
    lines = [
        "【离线模式】未检测到 DEEPSEEK_API_KEY —— 以下是检索到的【原始资料】，未经模型加工（不是答案）。",
        "",
        "角色：" + character_name + ("（" + character_role + "）" if character_role else ""),
        "问题：" + (question or "").strip(),
        "",
    ]
    if items:
        lines.append(
            "检索到 " + str(len(items)) + " 条资料片段，以下是与问题最相关的原文句子："
        )
        lines.append("")
        lines.extend(_highlight_lines(items, question))
        lines.append("")
        lines.append("配置 API Key 后，模型会基于以上资料组织完整的专业回答。")
    else:
        # 无片段时保持原有行为：角色自我介绍 + 说明知识库没有相关内容
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
