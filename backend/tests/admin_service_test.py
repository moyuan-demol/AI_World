"""站长（管理员）只读数据查看测试 —— 离线、临时 SQLite、确定性。

验证：
1. AdminService.overview 能同时列出多个用户及其计数（伙伴/知识库/切片/消息/最后活跃）
2. 管理员能显式越权读到其他用户的角色 / 知识库 / 文档切片 / 对话 / 消息
3. 普通路径仍然按 user_id 隔离（回归保护：证明没有为了站长而削弱隔离）
4. 不存在的 user_id 返回空列表或明确错误，不抛未处理异常

用法：
    python tests/admin_service_test.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：测试必须使用独立的临时数据库，绝不能碰 data/database.db（真实数据）。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_admin_test_"))
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_DIR / "test.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.repositories.chat_repository import ConversationRepository, MessageRepository  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.usage_repository import UsageRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate  # noqa: E402
from app.services.admin_service import AdminService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def db_call(handler):
    async def wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return asyncio.run_coroutine_threadsafe(wrapper(), _loop).result()


async def seed(session) -> dict:
    """构造两个用户，各自拥有伙伴 / 知识库 / 文档 / 对话 / 消息。

    user_a：1 伙伴、1 知识库、2 切片、1 对话、2 消息、1 条审计（最后活跃非空）
    user_b：2 伙伴、1 知识库、3 切片、1 对话、3 消息、无审计（最后活跃为空）
    """
    users = UserRepository(session)
    user_a = await users.create(username="admin_test_a", password_hash="x", email=None, role="user")
    user_b = await users.create(username="admin_test_b", password_hash="x", email=None, role="user")
    await session.commit()

    characters = CharacterService(session)
    char_a = await characters.create(
        user_a.id,
        CharacterCreate(
            name="A医生",
            role="医学专家",
            personality="严谨",
            expertise="医疗 AI",
            speaking_style="专业",
        ),
    )
    char_b1 = await characters.create(
        user_b.id, CharacterCreate(name="B助手一", role="技术架构师")
    )
    char_b2 = await characters.create(user_b.id, CharacterCreate(name="B助手二", role="商业顾问"))

    knowledge = KnowledgeService(session)
    kb_a = await knowledge.create(user_a.id, KnowledgeCreate(name="A资料库", description="A"))
    kb_b = await knowledge.create(user_b.id, KnowledgeCreate(name="B资料库", description="B"))

    # B 的伙伴绑定知识边界，用来验证越权读取能带出 knowledge_ids
    await characters.set_knowledge_ids(user_b.id, char_b1.id, [kb_b.id])

    documents = DocumentRepository(session)
    for index in range(2):
        await documents.create(
            knowledge_id=kb_a.id,
            user_id=user_a.id,
            filename="a.txt",
            chunk_index=index,
            content="A 文档切片 " + str(index),
            embedding="[]",
        )
    for index in range(3):
        await documents.create(
            knowledge_id=kb_b.id,
            user_id=user_b.id,
            filename="b.txt",
            chunk_index=index,
            content="B 文档切片 " + str(index),
            embedding="[]",
        )
    await session.commit()

    conversations = ConversationRepository(session)
    messages = MessageRepository(session)
    conv_a = await conversations.create(user_id=user_a.id, character_id=char_a.id, title="A 的对话")
    conv_b = await conversations.create(
        user_id=user_b.id, character_id=char_b1.id, title="B 的对话"
    )
    await messages.create(conversation_id=conv_a.id, user_id=user_a.id, role="user", content="A 提问")
    await messages.create(
        conversation_id=conv_a.id, user_id=user_a.id, role="assistant", content="A 回答"
    )
    for role, content in (("user", "B 提问"), ("assistant", "B 回答"), ("user", "B 再问")):
        await messages.create(
            conversation_id=conv_b.id, user_id=user_b.id, role=role, content=content
        )

    # 审计记录：user_a 有一条 -> 最后活跃时间非空；user_b 没有 -> 为空
    await UsageRepository(session).create(user_id=user_a.id, action="chat", model="fake")
    await session.commit()

    return {
        "user_a": user_a.id,
        "user_b": user_b.id,
        "char_a": char_a.id,
        "char_b1": char_b1.id,
        "kb_a": kb_a.id,
        "kb_b": kb_b.id,
        "conv_a": conv_a.id,
        "conv_b": conv_b.id,
    }


def run() -> int:
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)
    print(
        "user_a=#"
        + str(ctx["user_a"])
        + "  user_b=#"
        + str(ctx["user_b"])
        + "  kb_b=#"
        + str(ctx["kb_b"])
    )
    print()

    print("== 1. 用户总览（一条聚合 SQL 列出全部用户与计数）==")

    async def overview(session):
        return await AdminService(session).overview()

    rows = db_call(overview)
    by_name = {row["username"]: row for row in rows}
    check("overview 同时列出两个用户", {"admin_test_a", "admin_test_b"} <= set(by_name), str(sorted(by_name)))
    row_a = by_name.get("admin_test_a", {})
    row_b = by_name.get("admin_test_b", {})
    check(
        "user_a 计数正确（1 伙伴 / 1 知识库 / 2 切片 / 2 消息）",
        row_a.get("character_count") == 1
        and row_a.get("knowledge_count") == 1
        and row_a.get("document_count") == 2
        and row_a.get("message_count") == 2,
        str(row_a),
    )
    check(
        "user_b 计数正确（2 伙伴 / 1 知识库 / 3 切片 / 3 消息）",
        row_b.get("character_count") == 2
        and row_b.get("knowledge_count") == 1
        and row_b.get("document_count") == 3
        and row_b.get("message_count") == 3,
        str(row_b),
    )
    check("user_a 最后活跃时间取自 usage_logs", bool(row_a.get("last_active")), str(row_a))
    check("user_b 无审计记录时最后活跃为空", row_b.get("last_active") in (None, ""), str(row_b))

    print("\n== 2. 管理员越权读取 user_b 的数据 ==")

    async def admin_reads(session):
        admin = AdminService(session)
        characters = await admin.characters(ctx["user_b"])
        bases = await admin.knowledge(ctx["user_b"])
        documents = bases[0]["documents"] if bases else []
        chunks = (
            await admin.document_chunks(ctx["user_b"], documents[0]["document_id"], limit=50)
            if documents
            else {"total": 0, "chunks": []}
        )
        conversations = await admin.conversations(ctx["user_b"])
        messages = (
            await admin.conversation_messages(conversations[0]["id"])
            if conversations
            else []
        )
        return characters, bases, chunks, conversations, messages

    characters, bases, chunks, conversations, messages = db_call(admin_reads)
    check(
        "越权读到 user_b 的角色",
        {item["name"] for item in characters} == {"B助手一", "B助手二"},
        str([item["name"] for item in characters]),
    )
    bound = {item["name"]: item["knowledge_ids"] for item in characters}
    check("越权读取带出角色的知识边界", bound.get("B助手一") == [ctx["kb_b"]], str(bound))
    check(
        "越权读到 user_b 的知识库与文档计数",
        len(bases) == 1
        and bases[0]["name"] == "B资料库"
        and bases[0]["document_count"] == 3
        and bases[0]["documents"][0]["filename"] == "b.txt"
        and bases[0]["documents"][0]["chunk_count"] == 3,
        str(bases),
    )
    check(
        "越权读到 user_b 的文档切片正文",
        chunks["total"] == 3
        and len(chunks["chunks"]) == 3
        and chunks["chunks"][0]["content"] == "B 文档切片 0",
        str(chunks)[:200],
    )
    check(
        "越权读到 user_b 的对话列表",
        len(conversations) == 1
        and conversations[0]["title"] == "B 的对话"
        and conversations[0]["message_count"] == 3,
        str(conversations),
    )
    check(
        "越权读到 user_b 的消息记录",
        [item["content"] for item in messages] == ["B 提问", "B 回答", "B 再问"],
        str([item["content"] for item in messages]),
    )

    print("\n== 3. 普通路径仍然按 user_id 隔离（回归保护）==")

    async def isolation(session):
        character_service = CharacterService(session)
        knowledge_service = KnowledgeService(session)
        chat_service = ChatService(session)
        a_characters = await character_service.list(ctx["user_a"])
        a_knowledge = await knowledge_service.list(ctx["user_a"])
        a_conversations = await chat_service.list_conversations(ctx["user_a"])
        try:
            await character_service.get(ctx["user_a"], ctx["char_b1"])
            cross_character = "allowed"
        except Exception as exc:  # noqa: BLE001
            cross_character = type(exc).__name__
        try:
            await knowledge_service.get(ctx["user_a"], ctx["kb_b"])
            cross_knowledge = "allowed"
        except Exception as exc:  # noqa: BLE001
            cross_knowledge = type(exc).__name__
        return a_characters, a_knowledge, a_conversations, cross_character, cross_knowledge

    a_characters, a_knowledge, a_conversations, cross_character, cross_knowledge = db_call(
        isolation
    )
    check(
        "CharacterService(user_a).list 看不到 user_b 的角色",
        {item.name for item in a_characters} == {"A医生"},
        str([item.name for item in a_characters]),
    )
    check(
        "KnowledgeService(user_a).list 看不到 user_b 的知识库",
        {item.name for item in a_knowledge} == {"A资料库"},
        str([item.name for item in a_knowledge]),
    )
    check(
        "ChatService(user_a) 看不到 user_b 的对话",
        [item.title for item in a_conversations] == ["A 的对话"],
        str([item.title for item in a_conversations]),
    )
    check("普通路径跨用户取角色被拒绝(404)", cross_character == "NotFoundError", cross_character)
    check("普通路径跨用户取知识库被拒绝(404)", cross_knowledge == "NotFoundError", cross_knowledge)

    print("\n== 4. 不存在的 user_id / document_id 不抛未处理异常 ==")

    async def missing(session):
        admin = AdminService(session)
        characters = await admin.characters(999999)
        knowledge = await admin.knowledge(999999)
        conversations = await admin.conversations(999999)
        messages = await admin.conversation_messages(999999)
        try:
            await admin.document_chunks(999999, 999999)
            chunks_error = "none"
        except Exception as exc:  # noqa: BLE001
            chunks_error = type(exc).__name__
        return characters, knowledge, conversations, messages, chunks_error

    missing_chars, missing_knowledge, missing_conversations, missing_messages, chunks_error = (
        db_call(missing)
    )
    check("不存在的 user 角色返回空列表", missing_chars == [], str(missing_chars))
    check("不存在的 user 知识库返回空列表", missing_knowledge == [], str(missing_knowledge))
    check("不存在的 user 对话返回空列表", missing_conversations == [], str(missing_conversations))
    check("不存在的对话消息返回空列表", missing_messages == [], str(missing_messages))
    check("不存在的文档抛出明确 NotFoundError", chunks_error == "NotFoundError", chunks_error)

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("站长只读数据查看（总览 / 越权读取 / 隔离回归 / 缺失数据）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
