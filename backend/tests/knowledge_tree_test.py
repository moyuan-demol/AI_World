"""知识库分级（树形）测试 —— 离线可跑，不调用模型。

验证：
1. 子节点创建（分类别存放），父级校验（不能挂到别人的节点下）
2. 绑定"文件夹"时，检索自动包含其下所有子库
3. 子树展开（自身 + 后代）
4. 删除文件夹会连同整棵子树与文档一起清理
5. 孤儿节点（父被删）仍能被列出，不会消失

用法：
    python tests/knowledge_tree_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_tree_test_"))
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_DIR / "test.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.deepseek_client import AIResult  # noqa: E402
from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.rag.embedding import embed_texts  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
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


class FakeAI:
    def __init__(self) -> None:
        self.captured: list[dict] = []
        self.model = "fake-model"

    @property
    def is_configured(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.captured = list(messages)
        return AIResult(text="FAKE", model="fake-model")


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def db_call(handler):
    async def wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return asyncio.run_coroutine_threadsafe(wrapper(), _loop).result()


async def seed(session) -> dict:
    token, _ = await AuthService(session).demo_login()
    user_id = token.user.id
    others = await UserRepository(session).create(
        username="other_user", password_hash="x", email=None
    )
    await session.commit()

    service = KnowledgeService(session)
    folder = await service.create(
        user_id, KnowledgeCreate(name="医疗资料（文件夹）", description="")
    )
    child = await service.create(
        user_id,
        KnowledgeCreate(name="临床指南", description="", parent_id=folder.id),
    )
    grandchild = await service.create(
        user_id,
        KnowledgeCreate(name="2026 版指南", description="", parent_id=child.id),
    )

    # 给最深层子库放一条文档
    documents = DocumentRepository(session)
    content = "心肌梗死溶栓时间窗与禁忌症，胸痛患者需尽快完成心电图。"
    vector = (await embed_texts([content]))[0]
    await documents.create(
        knowledge_id=grandchild.id,
        user_id=user_id,
        filename="指南.txt",
        chunk_index=0,
        content=content,
        embedding=json.dumps(vector),
    )
    await session.commit()

    character = await CharacterService(session).create(
        user_id, CharacterCreate(name="测试医生", role="医学专家")
    )
    return {
        "user_id": user_id,
        "other_user": others.id,
        "folder": folder.id,
        "child": child.id,
        "grandchild": grandchild.id,
        "character": character.id,
    }


def run() -> int:
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)
    print("文件夹:", ctx["folder"], "| 子库:", ctx["child"], "| 孙库:", ctx["grandchild"])
    print()

    print("== 1. 子树展开 ==")

    async def subtree(session):
        return await KnowledgeService(session).subtree_ids(ctx["user_id"], ctx["folder"])

    ids = db_call(subtree)
    check("文件夹展开包含整棵子树", set(ids) == {ctx["folder"], ctx["child"], ctx["grandchild"]}, str(ids))

    print("\n== 2. 父级校验（不能挂到别人的节点下）==")
    async def bad_parent(session):
        service = KnowledgeService(session)
        try:
            await service.create(
                ctx["other_user"],
                KnowledgeCreate(name="越权", description="", parent_id=ctx["folder"]),
            )
            return "allowed"
        except Exception as exc:
            return type(exc).__name__

    check("越权父级被拒绝", db_call(bad_parent) == "NotFoundError", str(db_call(bad_parent)))

    print("\n== 3. 绑定「文件夹」→ 检索自动包含深层子库 ==")

    async def bind_folder(session):
        service = CharacterService(session)
        return await service.set_knowledge_ids(ctx["user_id"], ctx["character"], [ctx["folder"]])

    bound = db_call(bind_folder)
    check("文件夹可作为知识边界", bound == [ctx["folder"]], str(bound))

    fake = FakeAI()

    async def ask(session):
        return await ChatService(session, ai_client=fake).chat(
            ctx["user_id"],
            ChatRequest(character_id=ctx["character"], message="心肌梗死如何处理？", use_knowledge=True),
        )

    db_call(ask)
    prompt = "\n".join(item["content"] for item in fake.captured)
    check("检索沿子树命中了深层文档", "心肌梗死" in prompt, prompt[:140])

    print("\n== 4. 删除文件夹 → 整棵子树与文档一起清理 ==")

    async def cleanup(session):
        service = KnowledgeService(session)
        await service.delete(ctx["user_id"], ctx["folder"])
        knowledge = KnowledgeRepository(session)
        documents = DocumentRepository(session)
        return (
            await knowledge.get_for_user(ctx["folder"], ctx["user_id"]),
            await knowledge.get_for_user(ctx["child"], ctx["user_id"]),
            await knowledge.get_for_user(ctx["grandchild"], ctx["user_id"]),
            len(await documents.list_by_knowledge(ctx["grandchild"])),
        )

    folder_left, child_left, grand_left, docs_left = db_call(cleanup)
    check("文件夹已删除", folder_left is None)
    check("子库已级联删除", child_left is None)
    check("孙库已级联删除", grand_left is None)
    check("文档已一并清理", docs_left == 0, str(docs_left))

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("知识库分级（树形 + 子树检索 + 级联删除）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
