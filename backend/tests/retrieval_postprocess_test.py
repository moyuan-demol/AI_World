"""检索后处理测试：相邻切片合并（small-to-big）+ 同文档多样性限制。

覆盖：
1. 纯函数 merge_adjacent_chunks：相邻（差 <= 1）合并、内容升序拼接、score 取最大、
   非相邻不合并、不同文件不合并、空输入安全、合并段顺序稳定
2. 纯函数 limit_per_document：同文档超上限丢弃、其它文档保留、<=0 表示不限制
3. 真实 Retriever/RagService 集成（独立临时库，离线、确定性）：
   相邻切片确实被拼成一段连贯上下文，且同一篇文档最多只保留 2 条

用法：
    python tests/retrieval_postprocess_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：独立临时数据库，绝不触碰真实 data/database.db
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_postprocess_test_"))
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_DIR / "test.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import settings  # noqa: E402
from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.rag.embedding import embed_texts  # noqa: E402
from app.rag.rag_service import RagService  # noqa: E402
from app.rag.retriever import (  # noqa: E402
    RetrievedChunk,
    limit_per_document,
    merge_adjacent_chunks,
)
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


def chunk(document_id, filename, index, content, score, knowledge_id=1):
    return RetrievedChunk(
        document_id=document_id,
        knowledge_id=knowledge_id,
        filename=filename,
        chunk_index=index,
        content=content,
        score=score,
    )


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def db_call(handler):
    async def wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return asyncio.run_coroutine_threadsafe(wrapper(), _loop).result()


async def seed(session) -> dict:
    token, _created = await AuthService(session).demo_login()
    user_id = token.user.id
    knowledge = await KnowledgeRepository(session).create(
        user_id=user_id, name="医学资料", description=""
    )
    await session.commit()

    documents = DocumentRepository(session)
    # 同一篇文档切成多片：0/1 相邻、3/4 相邻、6/7 相邻，中间故意留空档
    segments = {
        0: "心肌梗死需要尽快溶栓治疗。",
        1: "心肌梗死患者要立刻做心电图。",
        3: "心肌梗死溶栓时间窗是十二小时。",
        4: "心肌梗死禁忌症包括活动性出血。",
        6: "心肌梗死护理要点是绝对卧床。",
        7: "心肌梗死随访需要复查心脏功能。",
    }
    vectors = await embed_texts(list(segments.values()))
    for (index, text), vector in zip(segments.items(), vectors):
        await documents.create(
            knowledge_id=knowledge.id,
            user_id=user_id,
            filename="心肌梗死指南.txt",
            chunk_index=index,
            content=text,
            embedding=json.dumps(vector),
        )
    other = "心肌梗死急救流程与转运规范。"
    await documents.create(
        knowledge_id=knowledge.id,
        user_id=user_id,
        filename="急救手册.txt",
        chunk_index=0,
        content=other,
        embedding=json.dumps((await embed_texts([other]))[0]),
    )
    await session.commit()
    return {"user_id": user_id, "knowledge_id": knowledge.id}


def run() -> int:
    print("== 1. 相邻切片合并（纯函数）==")
    merged = merge_adjacent_chunks(
        [
            chunk(11, "a.txt", 0, "第一段", 0.3),
            chunk(12, "a.txt", 1, "第二段", 0.5),
            chunk(13, "a.txt", 2, "第三段", 0.4),
            chunk(14, "a.txt", 3, "第四段", 0.2),
        ]
    )
    check("4 条相邻切片合并成 1 段", len(merged) == 1, str(len(merged)))
    check("内容按 chunk_index 升序换行拼接", merged[0].content == "第一段\n第二段\n第三段\n第四段", merged[0].content)
    check("score 取组内最大值", merged[0].score == 0.5, str(merged[0].score))
    check("filename 保留", merged[0].filename == "a.txt")
    check("代表 id 取组内最小", merged[0].document_id == 11, str(merged[0].document_id))

    gap = merge_adjacent_chunks(
        [chunk(21, "b.txt", 0, "A", 0.1), chunk(22, "b.txt", 1, "B", 0.1), chunk(23, "b.txt", 3, "D", 0.1)]
    )
    check("差值 > 1 不合并（2 段）", len(gap) == 2, str(len(gap)))
    check("断裂处在正确的 chunk_index", [item.chunk_index for item in gap] == [0, 3], str([i.chunk_index for i in gap]))

    cross = merge_adjacent_chunks(
        [chunk(31, "x.txt", 0, "X0", 0.1), chunk(41, "y.txt", 1, "Y1", 0.1)]
    )
    check("不同文件不合并", len(cross) == 2, str(len(cross)))

    check("空输入返回空", merge_adjacent_chunks([]) == [])

    scrambled = merge_adjacent_chunks(
        [chunk(52, "c.txt", 1, "后", 0.2), chunk(51, "c.txt", 0, "前", 0.9)]
    )
    check("乱序输入也按 chunk_index 升序拼接", scrambled[0].content == "前\n后", scrambled[0].content)

    print("\n== 2. 同文档多样性限制（纯函数）==")
    candidates = [
        chunk(1, "甲.txt", 0, "甲0", 0.9),
        chunk(2, "甲.txt", 2, "甲2", 0.8),
        chunk(3, "甲.txt", 4, "甲4", 0.7),
        chunk(4, "乙.txt", 0, "乙0", 0.6),
    ]
    limited = limit_per_document(candidates, 2)
    check("同文档超过上限的切片被丢弃", sum(1 for item in limited if item.filename == "甲.txt") == 2, str([i.filename for i in limited]))
    check("其它文档被保留", any(item.filename == "乙.txt" for item in limited), str([i.filename for i in limited]))
    check("<=0 表示不限制", len(limit_per_document(candidates, 0)) == 4)

    print("\n== 3. 真实检索链路集成（合并 + 多样性）==")
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)

    async def retrieve(session):
        return await RagService(session).retrieve(
            user_id=ctx["user_id"],
            query="心肌梗死如何处理",
            knowledge_id=ctx["knowledge_id"],
            top_k=6,
        )

    results = db_call(retrieve)
    by_file: dict[str, list] = {}
    for item in results:
        by_file.setdefault(item.filename, []).append(item)

    guide = by_file.get("心肌梗死指南.txt", [])
    check("检索命中两类文件", "心肌梗死指南.txt" in by_file and "急救手册.txt" in by_file, str(list(by_file)))
    check(
        "同一篇文档最多保留 2 条（多样性生效）",
        len(guide) <= settings.retrieval_max_per_document,
        "命中 " + str(len(guide)) + " 条",
    )
    check(
        "相邻切片确实被拼成一段连贯上下文",
        any("\n" in item.content and item.content.count("心肌梗死") >= 2 for item in guide),
        str([item.content for item in guide]),
    )
    check("统计总条数不超过 top_k", len(results) <= 6, str(len(results)))

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("相邻切片合并 + 同文档多样性验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
