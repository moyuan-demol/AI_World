"""普通模式查询改写：把口语化的长问题改写成 1-3 条关键词式检索查询。

为什么需要：
- 用户的长问句里往往同时包含背景、限定条件与追问，直接整句做向量检索，
  语义会被"稀释"；改写成关键词式查询能显著提升召回质量。
- 这一步需要模型能力，因此**必须优雅降级**：模型未配置、调用失败、
  返回为空或过长时，一律回退为"用原问题检索一次"，绝不因此报错。

本模块只放纯函数（可测）；真正的模型调用在 ChatService 里完成。
"""

from __future__ import annotations

import re

# 只剥离列表符号/编号前缀，绝不能把查询本身的数字（如"2026版指南"）也吃掉
_LIST_MARKER_RE = re.compile(r"^(?:[-*•]\s*|\d+\s*[.、)．)]\s*)")

# 触发改写的并列连接词（一句话里在问好几件事）
_CONJUNCTIONS = ("并且", "以及", "还有", "同时", "另外")

REWRITE_SYSTEM = (
    "你是检索查询改写器。把用户的口语化问题改写成 1-3 条适合向量检索的关键词式查询，"
    "覆盖问题的不同侧面；只输出查询本身，每条一行，不要编号、不要解释、不要引号。"
)


def needs_rewrite(text: str, min_chars: int) -> bool:
    """纯函数：是否值得花一次模型调用做查询改写。

    条件（满足其一即可）：问题超过 min_chars 字；出现多个问句；含并列连接词。
    """
    question = (text or "").strip()
    if not question:
        return False
    if len(question) > max(0, int(min_chars)):
        return True
    if question.count("?") + question.count("？") >= 2:
        return True
    return any(word in question for word in _CONJUNCTIONS)


def parse_rewritten_queries(text: str, max_items: int = 3, max_length: int = 60) -> list[str]:
    """纯函数：解析模型输出，返回清洗后的查询列表（最多 max_items 条）。

    解析失败 / 结果为空时返回空列表，由调用方决定是否回退原问题。
    """
    if not text:
        return []
    queries: list[str] = []
    for raw_line in text.splitlines():
        # 只去掉列表符号/编号前缀与引号，保留查询自身的数字与文字
        line = _LIST_MARKER_RE.sub("", raw_line.strip()).strip()
        line = line.strip("\"'“”‘’").strip()
        if not line:
            continue
        if len(line) > max_length:
            # 单条过长说明模型没按要求输出关键词，视为不可用
            return []
        if line not in queries:
            queries.append(line)
        if len(queries) >= max_items:
            break
    return queries


def build_rewrite_messages(question: str) -> list[dict]:
    """构造查询改写的一次模型调用（system + user）。"""
    return [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": (question or "").strip()},
    ]
