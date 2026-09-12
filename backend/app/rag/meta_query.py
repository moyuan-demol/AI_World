"""文档元信息识别与组织：作者 / 页数 / 上传时间 / 文件名 这类问题的公共纯函数。

为什么需要它（注意设计已修正）：
- 早期版本命中这类问句时"只给元数据、跳过向量检索"，结果问"论文作者是谁"
  只能看到文件名/切片数，而真正的作者行就在被丢弃的正文第一页里 —— 这是缺陷。
- 现在 ChatService 命中这类问句时**照常检索正文**，只用本模块把文档元数据
  整理成补充证据（伪切片）附加在正文之后，两者不冲突。

对外提供纯函数（便于离线、确定性测试）：
- is_meta_question(text)  -> bool
- build_meta_context(documents) -> str
另外提供 build_meta_chunks(documents)，把元信息包装成与 build_context_block
兼容的伪 RetrievedChunk（document_id 用负数，避免与真实切片 id 冲突，
也让"离线兜底回答"仍然能展示来源）。
"""

from __future__ import annotations

import re
from datetime import datetime

# 元信息类问句的关键词表。只收录"指向文档属性"的词，避免误伤正文语义问题。
META_KEYWORDS: tuple[str, ...] = (
    "作者",
    "谁写",
    "谁创作",
    "谁整理",
    "页数",
    "多少页",
    "几页",
    "第几页",
    "什么时候",
    "上传时间",
    "创建时间",
    "更新时间",
    "修改时间",
    "文件名",
    "文档名",
    "标题",
    "文档列表",
    "哪些文件",
    "哪些文档",
    "有几篇",
    "多少篇",
)

# "第 X 页" / "X 页" 两种页码写法
_PAGE_WITH_PREFIX_RE = re.compile(r"第\s*(\d+)\s*页")
_PAGE_SUFFIX_RE = re.compile(r"(\d+)\s*页")


def is_meta_question(text: str) -> bool:
    """纯函数：问题是否属于"文档元信息"类（是则跳过向量检索）。"""
    if not text:
        return False
    return any(keyword in text for keyword in META_KEYWORDS)


def extract_pages(contents) -> list[int]:
    """从切片正文里提取出现的页码（去重、升序）。

    切片里若出现"第 3 页"或"3 页"，就认为该文档涉及这些页码。
    """
    pages: set[int] = set()
    for content in contents or []:
        text = content or ""
        for match in _PAGE_WITH_PREFIX_RE.findall(text):
            pages.add(int(match))
        for match in _PAGE_SUFFIX_RE.findall(text):
            pages.add(int(match))
    return sorted(pages)


def format_time(value) -> str:
    """把 created_time 规范成可读字符串（兼容 datetime / 字符串 / None）。"""
    if value is None:
        return "未知"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _field(document, key: str, default=None):
    """兼容 dict 与对象两种文档表示。"""
    if isinstance(document, dict):
        return document.get(key, default)
    return getattr(document, key, default)


def describe_document(document) -> str:
    """把一篇文档的元信息整理成一行中文描述。"""
    filename = _field(document, "filename", "（未命名文档）")
    created = format_time(_field(document, "created_time"))
    chunk_count = _field(document, "chunk_count", 0) or 0
    pages = extract_pages(_field(document, "contents", []) or [])
    parts = [
        "文件名：" + str(filename),
        "上传时间：" + created,
        "切片数：" + str(chunk_count),
    ]
    if pages:
        parts.append("页码信息：" + "、".join("第 " + str(page) + " 页" for page in pages))
    else:
        parts.append("页码信息：切片正文中未标注页码")
    return "；".join(parts)


def build_meta_context(documents) -> str:
    """纯函数：把文档元数据组织成可读的上下文文本。"""
    items = list(documents or [])
    if not items:
        return "该用户的知识库中还没有任何文档，无法回答元信息类问题。"
    lines = ["以下是知识库中匹配文档的元信息（作为检索正文的补充证据）："]
    for index, document in enumerate(items, start=1):
        lines.append("[" + str(index) + "] " + describe_document(document))
    return "\n".join(lines)


def build_meta_chunks(documents) -> list:
    """把元信息包装成伪 RetrievedChunk，供 build_context_block / 离线兜底复用。

    document_id 用负数：与联网检索的伪切片同一约定，绝不与真实切片 id 冲突。
    """
    # 延迟导入，避免 app.rag 包内出现循环导入
    from app.rag.retriever import RetrievedChunk

    chunks: list[RetrievedChunk] = []
    for index, document in enumerate(list(documents or []), start=1):
        chunks.append(
            RetrievedChunk(
                document_id=-index,
                knowledge_id=int(_field(document, "knowledge_id", 0) or 0),
                filename=str(_field(document, "filename", "（未命名文档）")),
                chunk_index=0,
                content=describe_document(document),
                score=1.0,
            )
        )
    return chunks
