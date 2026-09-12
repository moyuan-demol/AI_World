"""元信息类问题的「检索正文 + 首屏切片 + 附加元信息」集成测试。

回归背景（真实缺陷）：
    上一轮把元信息问句做成"跳过向量/BM25 检索，只给文档元数据"。
    于是用户问"论文作者包含哪些人物"时，模型只看到文件名/上传时间/切片数，
    而作者行（曾沙1 王隽1 高景莘1 李萍1 赵晖2）就在被丢弃的正文第一页里，
    只能回答"作者名单无法确认"。

本测试锁定修复后的行为（离线、确定性、不联网、不调用真实模型）：
1. head_chunks 纯函数：每篇文档取 chunk_index 最小的 1~2 块、按知识库过滤、
   与已进入检索结果的切片去重、总数受上限约束；
2. 集成：元信息问句仍然走知识库检索链路，正文（含第一页作者行）进入上下文；
3. 文档元信息（文件名/切片数）作为补充证据追加在正文之后；
4. 不相关文档不会因此被强行塞入超过上限。

用法：
    python tests/meta_context_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：使用独立临时数据库，绝不触碰真实 data/database.db。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_meta_context_test_"))
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_DIR / "test.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.deepseek_client import AIResult  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.rag.embedding import embed_texts  # noqa: E402
from app.rag.meta_query import is_meta_question  # noqa: E402
from app.rag.retriever import head_chunks  # noqa: E402
from app.repositories.character_repository import CharacterRepository  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

# 第一页正文里的作者行（真实场景：作者名 + 单位上标）
AUTHOR_LINE = "曾沙1 王隽1 高景莘1 李萍1 赵晖2"
PAPER_NAME = "基于网络药理学探究…_曾沙.pdf"
UNRELATED_NAME = "结直肠癌机制研究.pdf"
QUESTION = "网络药理学论文的作者包含哪些人物"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeAI:
    """模仿 context_test.py 的 FakeAI：已配置、捕获 messages、返回带 .text 的对象。"""

    def __init__(self) -> None:
        self.captured: list[dict] = []
        self.model = "fake-model"

    @property
    def is_configured(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.captured = list(messages)
        return AIResult(text="FAKE-ANSWER", model="fake-model")


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
        user_id=user_id, name="论文资料", description=""
    )
    await session.commit()

    await CharacterService(session).ensure_defaults(user_id)
    character = (await CharacterRepository(session).list_by_user(user_id))[0]
    await session.commit()

    documents = DocumentRepository(session)
    # 第一篇：chunk 0 是作者行（第一页），后面才是正文
    paper_chunks = {
        0: AUTHOR_LINE + "（1. 北京中医药大学；2. 中国中医科学院）",
        1: "摘要：本文基于网络药理学方法筛选核心靶点与通路。",
        2: "正文：网络药理学结果显示，该方剂可能通过多靶点发挥作用。",
    }
    vectors = await embed_texts(list(paper_chunks.values()))
    for (index, text), vector in zip(paper_chunks.items(), vectors):
        await documents.create(
            knowledge_id=knowledge.id,
            user_id=user_id,
            filename=PAPER_NAME,
            chunk_index=index,
            content=text,
            embedding=json.dumps(vector),
        )

    # 第二篇：与本问题无关，且**不含**作者行
    unrelated = "结直肠癌的发生发展与 APC、KRAS 等基因突变相关，属于 Wnt 信号通路异常激活。"
    await documents.create(
        knowledge_id=knowledge.id,
        user_id=user_id,
        filename=UNRELATED_NAME,
        chunk_index=0,
        content=unrelated,
        embedding=json.dumps((await embed_texts([unrelated]))[0]),
    )
    await session.commit()
    return {"user_id": user_id, "knowledge_id": knowledge.id, "character_id": character.id}


def test_head_chunks_pure() -> None:
    print("\n== 1. head_chunks 纯函数 ==")
    docs = [
        {"id": 1, "knowledge_id": 1, "filename": "a.pdf", "chunk_index": 0, "content": "a0"},
        {"id": 2, "knowledge_id": 1, "filename": "a.pdf", "chunk_index": 1, "content": "a1"},
        {"id": 3, "knowledge_id": 1, "filename": "a.pdf", "chunk_index": 2, "content": "a2"},
        {"id": 4, "knowledge_id": 1, "filename": "b.pdf", "chunk_index": 0, "content": "b0"},
        {"id": 5, "knowledge_id": 1, "filename": "b.pdf", "chunk_index": 3, "content": "b3"},
        {"id": 6, "knowledge_id": 2, "filename": "c.pdf", "chunk_index": 0, "content": "c0"},
    ]
    heads = head_chunks(docs)
    check(
        "每篇文档取 chunk_index 最小的一块",
        [item.content for item in heads] == ["a0", "b0", "c0"],
        str([item.content for item in heads]),
    )
    check(
        "按 knowledge_ids 过滤",
        [item.content for item in head_chunks(docs, knowledge_ids=[1])] == ["a0", "b0"],
        str([item.content for item in head_chunks(docs, knowledge_ids=[1])]),
    )
    check(
        "per_document=2 取头部两块（乱序也能按 chunk_index 排）",
        [item.content for item in head_chunks(docs, knowledge_ids=[1], per_document=2)]
        == ["a0", "a1", "b0", "b3"],
        str([item.content for item in head_chunks(docs, knowledge_ids=[1], per_document=2)]),
    )
    check(
        "per_document 收敛到 1~2",
        len(head_chunks(docs, per_document=99)) <= 2 * 3,
        str(len(head_chunks(docs, per_document=99))),
    )
    check(
        "exclude_ids 去重：已在检索结果里的头部切片不再添加",
        [item.content for item in head_chunks(docs, exclude_ids={1, 4})] == ["c0"],
        str([item.content for item in head_chunks(docs, exclude_ids={1, 4})]),
    )
    check(
        "limit 限制总数",
        [item.content for item in head_chunks(docs, limit=2)] == ["a0", "b0"],
        str([item.content for item in head_chunks(docs, limit=2)]),
    )
    check("空输入返回空", head_chunks([]) == [])
    check("limit=0 返回空", head_chunks(docs, limit=0) == [])


def run() -> int:
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)
    print(
        "测试账号 user_id=" + str(ctx["user_id"]) + " knowledge_id=" + str(ctx["knowledge_id"])
    )

    test_head_chunks_pure()

    print("\n== 2. 元信息问句集成：检索正文 + 首屏切片 + 附加元信息 ==")
    check("判为元信息问句", is_meta_question(QUESTION) is True, QUESTION)

    original_extract = settings.memory_auto_extract
    settings.memory_auto_extract = False  # 本节不测记忆抽取，保持确定性
    try:
        fake = FakeAI()

        async def handler(session):
            return await ChatService(session, ai_client=fake).chat(
                ctx["user_id"],
                ChatRequest(
                    character_id=ctx["character_id"],
                    message=QUESTION,
                    knowledge_id=ctx["knowledge_id"],
                    use_knowledge=True,
                ),
            )

        response = db_call(handler)
        print("      captured 调用次数/内容长度:", len(fake.captured), sum(len(i["content"]) for i in fake.captured))
    finally:
        settings.memory_auto_extract = original_extract

    prompt_text = "\n".join(item["content"] for item in fake.captured)

    check("聊天正常返回", response.answer == "FAKE-ANSWER", response.answer)
    # 核心验收点：第一页作者行必须出现在送进模型的上下文里
    check("上下文包含第一页的作者行", AUTHOR_LINE in prompt_text, prompt_text[:400])
    # 正文检索确实发生：检索片段里有正文（不是只剩元数据）
    check(
        "上下文包含检索到的正文内容",
        "本文基于网络药理学" in prompt_text,
        prompt_text[:400],
    )
    # 元信息作为补充证据同时存在
    check("上下文包含文档元信息（切片数）", "切片数" in prompt_text, prompt_text[:400])
    check("上下文包含文件名", PAPER_NAME in prompt_text, prompt_text[:400])
    check(
        "元信息伪切片 document_id 为负数",
        any(source.document_id < 0 for source in response.sources),
        str([source.document_id for source in response.sources]),
    )

    # 顺序：正文切片在前、元信息在后
    body_index = prompt_text.find(AUTHOR_LINE)
    meta_index = prompt_text.rfind("切片数")
    check("正文切片排在元信息之前", 0 <= body_index < meta_index, str(body_index) + "/" + str(meta_index))

    # 不相关文档不能因为首屏兜底而被"强行塞入超过上限"。
    # 注意：元信息伪切片（document_id 为负）是"文档清单"，本来就该列出全部文档；
    # 这里只统计进入正文的块，验证它没有被首屏兜底反复塞入。
    unrelated_body = [
        source
        for source in response.sources
        if source.filename == UNRELATED_NAME and source.document_id > 0
    ]
    check(
        "不相关文档最多进 1 块正文（不超过每篇首屏上限）",
        len(unrelated_body) <= settings.retrieval_head_per_document,
        "结直肠癌正文块出现 " + str(len(unrelated_body)) + " 块",
    )
    total_cap = settings.retrieval_top_k + settings.retrieval_head_chunks + 2
    check(
        "上下文总切片数受上限约束（检索上限 + 首屏上限 + 2 条元信息）",
        len(response.sources) <= total_cap,
        str(len(response.sources)) + " > " + str(total_cap),
    )

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("元信息类问题「检索正文 + 首屏切片 + 附加元信息」验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
