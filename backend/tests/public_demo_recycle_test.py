"""公共示例库（所有人可查）+ 回收站（软删除 / 恢复 / 彻底删除）测试。

离线、临时 SQLite、确定性；不调用真实模型、不联网。

覆盖：
1. ensure_public_demo() 幂等：连续调用两次，公共库与文档都不重复；
2. 公共库可被任意用户检索到（上下文包含事实点 0.18 / RRF），
   且公共库**不出现**在该用户的"我的知识库"列表里；
3. 软删除：删除自己的知识库后检索不到、列表不再返回，回收站能看到；
4. 恢复：restore 后重新可检索、列表恢复出现；
5. 彻底删除：purge 后物理记录消失；
6. 权限：普通用户 purge 公共库内容被拒绝(403)，站长可以；
7. 子树：删除父知识库时子库与文档一并进回收站，恢复父级时子级一并恢复；
8. 回归：普通用户 A 看不到用户 B 的私有数据（隔离未被削弱）。

用法：
    python tests/public_demo_recycle_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

# 数据安全：独立临时数据库 + 独立上传目录，绝不触碰真实 data/database.db。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_public_recycle_test_"))
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
from app.rag.rag_service import RagService  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
import app.services.demo_seed as demo_seed  # noqa: E402
from app.services.demo_seed import (  # noqa: E402
    PUBLIC_DOC_FILENAME,
    PUBLIC_DOC_STORED_FILENAME,
    PUBLIC_KB_NAME,
    ensure_public_demo,
)
from app.services.knowledge_service import KnowledgeService  # noqa: E402
from app.services.recycle_service import RecycleService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

FACT_QUESTION = "检索最低相关性阈值是多少？"
ALICE_SECRET = "ALICE_SECRET_9F3A"
BOB_SECRET = "BOB_SECRET_8E2B"
SUBTREE_SECRET = "SUBTREE_SECRET_7C1D"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeAI:
    """已配置的假模型：捕获 messages，返回带 .text 的结果（与其它集成测试一致）。"""

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


async def seed_users(session) -> dict:
    users = UserRepository(session)
    admin = await users.create(
        username="pub_owner", password_hash="x", email=None, role="admin"
    )
    alice = await users.create(username="pub_alice", password_hash="x", email=None, role="user")
    bob = await users.create(username="pub_bob", password_hash="x", email=None, role="user")
    await session.commit()
    return {"admin": admin.id, "alice": alice.id, "bob": bob.id}


async def add_chunk(session, *, user_id: int, knowledge_id: int, filename: str, content: str) -> None:
    vector = (await embed_texts([content]))[0]
    await DocumentRepository(session).create(
        knowledge_id=knowledge_id,
        user_id=user_id,
        filename=filename,
        chunk_index=0,
        content=content,
        embedding=json.dumps(vector),
    )
    await session.commit()


def section_one(ctx: dict) -> None:
    print("== 1. ensure_public_demo() 幂等 ==")

    async def handler(session):
        first = await ensure_public_demo(session)
        second = await ensure_public_demo(session)
        knowledge = KnowledgeRepository(session)
        documents = DocumentRepository(session)
        public = [base for base in await knowledge.list_public() if base.name == PUBLIC_KB_NAME]
        chunks = await documents.list_by_knowledge(first["knowledge_id"])
        return first, second, public, chunks

    first, second, public, chunks = db_call(handler)
    ctx["public_kb"] = first["knowledge_id"]
    check("第一次调用确实创建了公共库", first["created_knowledge"] is True, str(first))
    check("第二次调用不再创建（幂等）", second["created_knowledge"] is False, str(second))
    check("同名公共库全局只有 1 个", len(public) == 1, str([base.id for base in public]))
    check(
        "第二次调用不再重复入库文档",
        second["created_document"] is False,
        str(second),
    )
    check(
        "手册文档已切分 + 向量化入库",
        len(chunks) > 0 and {chunk.filename for chunk in chunks} == {PUBLIC_DOC_STORED_FILENAME},
        str([chunk.filename for chunk in chunks]),
    )
    check(
        "公共库归属站长账号",
        len(public) == 1 and public[0].user_id == ctx["admin"],
        str(public[0].user_id if public else None),
    )
    check(
        "公共库内容已写入文档正文（含事实点 0.18）",
        any("0.18" in chunk.content for chunk in chunks),
        str(chunks[0].content[:80] if chunks else ""),
    )
    check(
        "预置文档名符合需求（入库名由安全净化得到）",
        PUBLIC_DOC_FILENAME == "AI 世界 · 使用手册与检索测试题.md"
        and PUBLIC_DOC_STORED_FILENAME.endswith(".md"),
        PUBLIC_DOC_FILENAME + " -> " + PUBLIC_DOC_STORED_FILENAME,
    )
    check(
        "公共手册切片数明显增加（>= 8 条）",
        len(chunks) >= 8,
        "切片数=" + str(len(chunks)),
    )
    check(
        "至少一条切片包含事实点 RRF",
        any("RRF" in chunk.content for chunk in chunks),
        str([chunk.content[:40] for chunk in chunks]),
    )
    check(
        "手册正文明显扩写（>= 2500 字）",
        sum(len(chunk.content) for chunk in chunks) >= 2500,
        "总字数=" + str(sum(len(chunk.content) for chunk in chunks)),
    )


def section_version(ctx: dict) -> None:
    print("\n== 1b. 版本机制：PUBLIC_DOC_VERSION 变化会替换切片，且仍幂等 ==")

    async def handler(session):
        documents = DocumentRepository(session)
        before = await documents.list_by_knowledge(ctx["public_kb"])
        before_ids = {chunk.id for chunk in before}
        before_contents = {chunk.content for chunk in before}
        original_version = demo_seed.PUBLIC_DOC_VERSION
        original_fingerprint = demo_seed.public_doc_fingerprint()
        try:
            demo_seed.PUBLIC_DOC_VERSION = original_version + 1
            bumped_fingerprint = demo_seed.public_doc_fingerprint()
            bumped = await ensure_public_demo(session)
            after = await documents.list_by_knowledge(ctx["public_kb"])
            after_ids = {chunk.id for chunk in after}
            after_contents = {chunk.content for chunk in after}
            again = await ensure_public_demo(session)
            again_ids = {
                chunk.id for chunk in await documents.list_by_knowledge(ctx["public_kb"])
            }
        finally:
            # 还原版本号，并把库重建回"当前最新版本"，避免影响后续小节
            demo_seed.PUBLIC_DOC_VERSION = original_version
            restored = await ensure_public_demo(session)
            restored_chunks = await documents.list_by_knowledge(ctx["public_kb"])
        return {
            "before_ids": before_ids,
            "after_ids": after_ids,
            "again_ids": again_ids,
            "before_contents": before_contents,
            "after_contents": after_contents,
            "original_fingerprint": original_fingerprint,
            "bumped_fingerprint": bumped_fingerprint,
            "bumped": bumped,
            "again": again,
            "restored": restored,
            "restored_count": len(restored_chunks),
            "before_count": len(before),
        }

    result = db_call(handler)
    check(
        "版本变化后会重新切片入库（created_document=True）",
        result["bumped"]["created_document"] is True,
        str(result["bumped"]),
    )
    check(
        "版本变化被标记为替换（replaced_document=True）",
        result["bumped"]["replaced_document"] is True,
        str(result["bumped"]),
    )
    # 注意：SQLite 没有 AUTOINCREMENT 时，删空后再插入会复用 rowid，
    # 因此不能用"切片 id 不同"判断替换；用"版本指纹是否换掉"才是可靠证据。
    check(
        "旧版本内容被替换（切片正文集合发生变化）",
        result["before_contents"] != result["after_contents"],
        str(list(result["after_contents"])[:1])[:120],
    )
    check(
        "旧版本指纹已从切片中消失",
        not any(result["original_fingerprint"] in item for item in result["after_contents"]),
        result["original_fingerprint"],
    )
    check(
        "新版本指纹已写入切片",
        any(result["bumped_fingerprint"] in item for item in result["after_contents"]),
        result["bumped_fingerprint"],
    )
    check(
        "替换后切片数不累积（与替换前一致且 >= 8）",
        len(result["after_ids"]) == result["before_count"] and len(result["after_ids"]) >= 8,
        "before=" + str(result["before_count"]) + " after=" + str(len(result["after_ids"])),
    )
    check(
        "同一版本连续调用两次仍幂等（第二次不重复插入）",
        result["again"]["created_document"] is False,
        str(result["again"]),
    )
    check(
        "幂等调用不改变切片集合",
        result["again_ids"] == result["after_ids"],
        str((result["again_ids"], result["after_ids"])),
    )
    check(
        "还原版本号后会重建为当前版本（>= 8 条切片）",
        result["restored"]["replaced_document"] is True and result["restored_count"] >= 8,
        str(result["restored"])[:200],
    )


def section_recycled_doc(ctx: dict) -> None:
    print("\n== 1c. 回收站中的公共文档：跳过而不是复活 ==")

    async def handler(session):
        documents = DocumentRepository(session)
        # 只把"文档切片"软删除，知识库本身仍然活着 —— 模拟文档级回收站场景
        await documents.soft_delete_by_knowledge_ids([ctx["public_kb"]], datetime.utcnow())
        await session.commit()
        skipped = await ensure_public_demo(session)
        still_deleted = await documents.list_deleted_by_knowledge_ids([ctx["public_kb"]])
        active_after_skip = await documents.list_by_knowledge(ctx["public_kb"])
        # 模拟站长在回收站恢复整篇文档，再让种子逻辑处理一次
        await documents.restore_by_knowledge_ids([ctx["public_kb"]])
        await session.commit()
        restored = await ensure_public_demo(session)
        active_after_restore = await documents.list_by_knowledge(ctx["public_kb"])
        return skipped, still_deleted, active_after_skip, restored, active_after_restore

    skipped, still_deleted, active_after_skip, restored, active_after_restore = db_call(handler)
    check(
        "文档在回收站时跳过（skipped_reason=document_recycled）",
        skipped["skipped_reason"] == "document_recycled" and skipped["created_document"] is False,
        str(skipped),
    )
    check(
        "跳过时不复活切片（切片仍留在回收站）",
        active_after_skip == [] and len(still_deleted) >= 8,
        "active=" + str(len(active_after_skip)) + " deleted=" + str(len(still_deleted)),
    )
    check(
        "恢复后指纹一致则幂等跳过、不重复插入",
        restored["created_document"] is False and restored["skipped_reason"] is None,
        str(restored),
    )
    check(
        "恢复后可重新读到公共手册切片",
        len(active_after_restore) >= 8,
        "active=" + str(len(active_after_restore)),
    )


def section_two(ctx: dict) -> None:
    print("\n== 2. 任意用户都能检索公共库，且公共库不在「我的知识库」里 ==")

    async def handler(session):
        chunks = await RagService(session).retrieve(user_id=ctx["alice"], query=FACT_QUESTION)
        listed = [item.name for item in await KnowledgeService(session).list(ctx["alice"])]
        public_names = [item.name for item in await KnowledgeService(session).list_public()]
        return chunks, listed, public_names

    chunks, listed, public_names = db_call(handler)
    check(
        "公共手册进入用户A的检索结果",
        any(chunk.filename == PUBLIC_DOC_STORED_FILENAME for chunk in chunks),
        str([chunk.filename for chunk in chunks]),
    )
    check(
        "检索上下文包含事实点 0.18",
        any("0.18" in chunk.content for chunk in chunks),
        str([chunk.content[:60] for chunk in chunks]),
    )
    check(
        "公共库不出现在「我的知识库」列表",
        PUBLIC_KB_NAME not in listed,
        str(listed),
    )
    check(
        "公共库出现在「公共库」列表",
        PUBLIC_KB_NAME in public_names,
        str(public_names),
    )

    async def chat_handler(session):
        character = await CharacterService(session).create(
            ctx["alice"], CharacterCreate(name="检索助手", role="助手")
        )
        fake = FakeAI()
        original = settings.memory_auto_extract
        settings.memory_auto_extract = False  # 本节不测记忆抽取，保持确定性
        try:
            await ChatService(session, ai_client=fake).chat(
                ctx["alice"],
                ChatRequest(
                    character_id=character.id,
                    message=FACT_QUESTION,
                    use_knowledge=True,
                ),
            )
        finally:
            settings.memory_auto_extract = original
        return "\n".join(item["content"] for item in fake.captured)

    prompt = db_call(chat_handler)
    check("聊天送进模型的上下文包含事实点 0.18", "0.18" in prompt, prompt[-300:])


def section_three(ctx: dict) -> None:
    print("\n== 3. 软删除：自己的知识库进回收站 ==")

    async def handler(session):
        service = KnowledgeService(session)
        base = await service.create(ctx["alice"], KnowledgeCreate(name="Alice 私库", description=""))
        await add_chunk(
            session,
            user_id=ctx["alice"],
            knowledge_id=base.id,
            filename="alice.txt",
            content=ALICE_SECRET + " 专属内容：心肌梗死溶栓流程与时间窗。",
        )
        before = await RagService(session).retrieve(
            user_id=ctx["alice"], query=ALICE_SECRET + " 是什么"
        )
        await service.delete(ctx["alice"], base.id)
        after = await RagService(session).retrieve(
            user_id=ctx["alice"], query=ALICE_SECRET + " 是什么"
        )
        listed = [item.id for item in await service.list(ctx["alice"])]
        recycle = await RecycleService(session).list_deleted(ctx["alice"])
        raw = await KnowledgeRepository(session).get(base.id)
        deleted_docs = await DocumentRepository(session).list_deleted_by_knowledge_ids([base.id])
        return base.id, before, after, listed, recycle, raw, deleted_docs

    kb_id, before, after, listed, recycle, raw, deleted_docs = db_call(handler)
    ctx["alice_kb"] = kb_id
    check("删除前能检索到私有内容", any(ALICE_SECRET in c.content for c in before), str(len(before)))
    check("软删除后检索不到私有内容", not any(ALICE_SECRET in c.content for c in after), str(len(after)))
    check("软删除后「我的知识库」不再返回它", kb_id not in listed, str(listed))
    check(
        "回收站能看到被删知识库",
        any(item["kind"] == "knowledge" and item["id"] == kb_id for item in recycle),
        str(recycle),
    )
    check(
        "回收站条目带删除时间",
        all(item.get("deleted_at") for item in recycle),
        str([item.get("deleted_at") for item in recycle]),
    )
    check("是软删除：物理记录仍在", raw is not None and raw.deleted_at is not None, str(raw))
    check("文档切片一并进入回收站", len(deleted_docs) > 0, str(len(deleted_docs)))


def section_four(ctx: dict) -> None:
    print("\n== 4. 恢复：restore 后重新可检索 ==")

    async def handler(session):
        await RecycleService(session).restore(ctx["alice"], "knowledge", ctx["alice_kb"])
        chunks = await RagService(session).retrieve(
            user_id=ctx["alice"], query=ALICE_SECRET + " 是什么"
        )
        listed = [item.id for item in await KnowledgeService(session).list(ctx["alice"])]
        raw = await KnowledgeRepository(session).get(ctx["alice_kb"])
        recycle_ids = [
            item["id"]
            for item in await RecycleService(session).list_deleted(ctx["alice"])
            if item["kind"] == "knowledge"
        ]
        return chunks, listed, raw, recycle_ids

    chunks, listed, raw, recycle_ids = db_call(handler)
    check("恢复后重新可检索", any(ALICE_SECRET in c.content for c in chunks), str(len(chunks)))
    check("恢复后重新出现在「我的知识库」", ctx["alice_kb"] in listed, str(listed))
    check("恢复后 deleted_at 已置空", raw is not None and raw.deleted_at is None, str(raw))
    check("恢复后不再出现在回收站", ctx["alice_kb"] not in recycle_ids, str(recycle_ids))


def section_five(ctx: dict) -> None:
    print("\n== 5. 彻底删除：purge 后物理记录消失 ==")

    async def handler(session):
        service = KnowledgeService(session)
        await service.delete(ctx["alice"], ctx["alice_kb"])
        await RecycleService(session).purge(ctx["alice"], "knowledge", ctx["alice_kb"])
        raw = await KnowledgeRepository(session).get(ctx["alice_kb"])
        deleted_docs = await DocumentRepository(session).list_deleted_by_knowledge_ids(
            [ctx["alice_kb"]]
        )
        active_docs = await DocumentRepository(session).list_by_knowledge(ctx["alice_kb"])
        return raw, deleted_docs, active_docs

    raw, deleted_docs, active_docs = db_call(handler)
    check("彻底删除后知识库物理记录为 None", raw is None, str(raw))
    check("彻底删除后文档切片也物理清空", deleted_docs == [] and active_docs == [], str(deleted_docs))


def section_seven(ctx: dict) -> None:
    print("\n== 7. 子树：父知识库删除/恢复时子库与文档跟随 ==")

    async def handler(session):
        service = KnowledgeService(session)
        folder = await service.create(ctx["alice"], KnowledgeCreate(name="Alice 文件夹", description=""))
        child = await service.create(
            ctx["alice"], KnowledgeCreate(name="Alice 子库", description="", parent_id=folder.id)
        )
        grand = await service.create(
            ctx["alice"], KnowledgeCreate(name="Alice 孙库", description="", parent_id=child.id)
        )
        await add_chunk(
            session,
            user_id=ctx["alice"],
            knowledge_id=grand.id,
            filename="deep.txt",
            content=SUBTREE_SECRET + " 深藏的规范条文。",
        )
        await service.delete(ctx["alice"], folder.id)
        knowledge = KnowledgeRepository(session)
        deleted_flags = [
            (await knowledge.get(item)).deleted_at is not None
            for item in (folder.id, child.id, grand.id)
        ]
        recycle = await RecycleService(session).list_deleted(ctx["alice"])
        knowledge_ids = {item["id"] for item in recycle if item["kind"] == "knowledge"}
        doc_entries = [
            item
            for item in recycle
            if item["kind"] == "document" and item.get("knowledge_name") == "Alice 孙库"
        ]
        await RecycleService(session).restore(ctx["alice"], "knowledge", folder.id)
        restored_flags = [
            (await knowledge.get(item)).deleted_at is None
            for item in (folder.id, child.id, grand.id)
        ]
        chunks = await RagService(session).retrieve(
            user_id=ctx["alice"], query=SUBTREE_SECRET + " 是什么"
        )
        return {
            "folder": folder.id,
            "child": child.id,
            "grand": grand.id,
            "deleted_flags": deleted_flags,
            "knowledge_ids": knowledge_ids,
            "doc_entries": doc_entries,
            "restored_flags": restored_flags,
            "chunks": chunks,
        }

    result = db_call(handler)
    check(
        "删除父级后整棵子树都进入回收站",
        all(result["deleted_flags"]),
        str(result["deleted_flags"]),
    )
    check(
        "回收站列出了父/子/孙三个知识库",
        {result["folder"], result["child"], result["grand"]} <= result["knowledge_ids"],
        str(sorted(result["knowledge_ids"])),
    )
    check(
        "子库下的文档也进入回收站（标记父级已删除）",
        len(result["doc_entries"]) == 1 and result["doc_entries"][0]["parent_deleted"] is True,
        str(result["doc_entries"]),
    )
    check(
        "恢复父级时子级一并恢复",
        all(result["restored_flags"]),
        str(result["restored_flags"]),
    )
    check(
        "恢复后子库文档重新可检索",
        any(SUBTREE_SECRET in c.content for c in result["chunks"]),
        str(len(result["chunks"])),
    )


def section_eight(ctx: dict) -> None:
    print("\n== 8. 回归：普通用户 A 看不到用户 B 的私有数据 ==")

    async def handler(session):
        service = KnowledgeService(session)
        bob_kb = await service.create(ctx["bob"], KnowledgeCreate(name="Bob 私库", description=""))
        await add_chunk(
            session,
            user_id=ctx["bob"],
            knowledge_id=bob_kb.id,
            filename="bob.txt",
            content=BOB_SECRET + " 只属于 Bob 的资料。",
        )
        chunks = await RagService(session).retrieve(
            user_id=ctx["alice"], query=BOB_SECRET + " 是什么"
        )
        listed = [item.id for item in await service.list(ctx["alice"])]
        try:
            await service.get(ctx["alice"], bob_kb.id)
            cross = "allowed"
        except Exception as exc:  # noqa: BLE001
            cross = type(exc).__name__
        await service.delete(ctx["bob"], bob_kb.id)
        alice_recycle = await RecycleService(session).list_deleted(ctx["alice"])
        return chunks, listed, cross, bob_kb.id, alice_recycle

    chunks, listed, cross, bob_kb, alice_recycle = db_call(handler)
    check("A 检索不到 B 的私有内容", not any(BOB_SECRET in c.content for c in chunks), str(len(chunks)))
    check("A 的知识库列表看不到 B 的知识库", bob_kb not in listed, str(listed))
    check("A 读取 B 的知识库被拒绝(404)", cross == "NotFoundError", cross)
    check(
        "B 的回收站条目不会出现在 A 的回收站",
        bob_kb not in {item["id"] for item in alice_recycle if item["kind"] == "knowledge"},
        str(alice_recycle),
    )


def section_six(ctx: dict) -> None:
    print("\n== 6. 权限：公共库内容只有站长能彻底删除 ==")

    async def handler(session):
        service = KnowledgeService(session)
        # 方案 1：普通用户对公共内容只能软删除 / 恢复
        await service.delete(ctx["alice"], ctx["public_kb"])
        knowledge = KnowledgeRepository(session)
        after_soft = await knowledge.get(ctx["public_kb"])
        bob_chunks = await RagService(session).retrieve(user_id=ctx["bob"], query=FACT_QUESTION)
        recycle = await RecycleService(session).list_deleted(ctx["alice"])
        public_entries = [
            item for item in recycle if item["kind"] == "knowledge" and item["id"] == ctx["public_kb"]
        ]
        try:
            await RecycleService(session).purge(ctx["alice"], "knowledge", ctx["public_kb"])
            normal_purge = "allowed"
        except Exception as exc:  # noqa: BLE001
            normal_purge = type(exc).__name__
        admin_result = await RecycleService(session).purge(
            ctx["admin"], "knowledge", ctx["public_kb"]
        )
        after_purge = await knowledge.get(ctx["public_kb"])
        return after_soft, bob_chunks, public_entries, normal_purge, admin_result, after_purge

    after_soft, bob_chunks, public_entries, normal_purge, admin_result, after_purge = db_call(handler)
    check(
        "普通用户可以软删除公共库（进回收站）",
        after_soft is not None and after_soft.deleted_at is not None,
        str(after_soft),
    )
    check(
        "公共库软删除后任何用户都检索不到",
        not any("0.18" in c.content for c in bob_chunks),
        str(len(bob_chunks)),
    )
    check(
        "公共条目出现在回收站并标记为公共",
        len(public_entries) == 1 and public_entries[0]["is_public"] is True,
        str(public_entries),
    )
    check(
        "普通用户彻底删除公共库被拒绝(403)",
        normal_purge == "PermissionDeniedError",
        normal_purge,
    )
    check(
        "站长可以彻底删除公共库内容",
        admin_result.get("affected", 0) >= 1,
        str(admin_result),
    )
    check(
        "彻底删除后公共库物理记录消失",
        after_purge is None,
        str(after_purge),
    )


def run() -> int:
    # 让公共库归属这个临时库里的站长账号（否则会回落到 demo / 第一个用户）
    settings.admin_usernames = "pub_owner"
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed_users)
    print(
        "站长=#" + str(ctx["admin"])
        + "  用户A=#" + str(ctx["alice"])
        + "  用户B=#" + str(ctx["bob"])
    )

    section_one(ctx)
    section_version(ctx)
    section_recycled_doc(ctx)
    section_two(ctx)
    section_three(ctx)
    section_four(ctx)
    section_five(ctx)
    section_seven(ctx)
    section_eight(ctx)
    section_six(ctx)

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("公共示例库 + 回收站（软删除/恢复/彻底删除）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
