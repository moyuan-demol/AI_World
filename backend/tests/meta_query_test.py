"""文档元信息直答测试（纯函数 + 伪切片，离线、确定性、不碰数据库）。

覆盖：
1. is_meta_question：作者 / 页数 / 上传时间 / 文件名 / 第几页 等判定，
   以及普通语义问题不应被误判
2. extract_pages：从切片正文提取"第 X 页""X 页"
3. build_meta_context：组织出含 filename / 上传时间 / 切片数 / 页码 的上下文
4. build_meta_chunks：包装成 build_context_block 兼容的伪 RetrievedChunk（id 为负）

用法：
    python tests/meta_query_test.py
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.prompts import build_context_block  # noqa: E402
from app.rag.meta_query import (  # noqa: E402
    build_meta_chunks,
    build_meta_context,
    extract_pages,
    format_time,
    is_meta_question,
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


DOCS = [
    {
        "knowledge_id": 1,
        "filename": "临床指南.pdf",
        "created_time": datetime(2025, 1, 2, 3, 4, 5),
        "chunk_count": 3,
        "contents": ["第 1 页 概述", "第 2 页 诊断标准", "第 7 页 随访"],
    }
]


def main() -> int:
    print("== 1. 元信息问句判定 ==")
    positive = [
        "这篇文档的作者是谁？",
        "这份资料是谁写的",
        "这篇论文一共有多少页",
        "PDF 的页数是多少",
        "第几页讲了诊断标准",
        "文档是什么时候上传的",
        "它的上传时间是什么时候",
        "文件名是什么",
        "这个文档的标题叫什么",
        "知识库里有哪些文件",
    ]
    negative = [
        "心肌梗死如何处理？",
        "阿柏西普的适应症有哪些",
        "医疗 AI 的技术架构和合规风险是什么？",
        "",
    ]
    for text in positive:
        check("判定为元信息：" + text[:12], is_meta_question(text) is True, text)
    for text in negative:
        check("不判为元信息：" + (text[:12] or "(空串)"), is_meta_question(text) is False, text)

    print("\n== 2. 页码提取 ==")
    pages = extract_pages(["见第 3 页", "共 12 页，第3页有图", "没有页码标注"])
    check("提取并去重升序", pages == [3, 12], str(pages))
    check("无页码返回空列表", extract_pages(["普通正文"]) == [])
    check("空输入安全", extract_pages([]) == [])

    print("\n== 3. 时间格式化 ==")
    check("datetime 格式化", format_time(datetime(2025, 1, 2, 3, 4, 5)) == "2025-01-02 03:04:05")
    check("None 显示未知", format_time(None) == "未知")

    print("\n== 4. 组织元信息上下文 ==")
    context = build_meta_context(DOCS)
    check("包含文件名", "临床指南.pdf" in context, context)
    check("包含上传时间", "2025-01-02 03:04:05" in context, context)
    check("包含切片数", "切片数：3" in context, context)
    check("包含页码信息", "第 1 页" in context and "第 7 页" in context, context)
    check("没有文档时给出明确说明", "还没有任何文档" in build_meta_context([]), build_meta_context([]))

    print("\n== 5. 伪 RetrievedChunk（build_context_block 兼容）==")
    chunks = build_meta_chunks(DOCS)
    check("生成 1 条伪切片", len(chunks) == 1, str(len(chunks)))
    check("document_id 为负数", chunks[0].document_id < 0, str(chunks[0].document_id))
    check("filename 保留", chunks[0].filename == "临床指南.pdf")
    check("content 含元信息", "临床指南.pdf" in chunks[0].content, chunks[0].content)
    check("能被 build_context_block 正常渲染", "临床指南.pdf" in build_context_block(chunks))

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("文档元信息直答验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
