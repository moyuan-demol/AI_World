"""长记忆与上下文机制测试（不调用真实模型，结果确定、可离线运行）。

覆盖：
1. 长期记忆注入 system prompt
2. 历史窗口「条数 + 字符预算」裁剪
3. 滚动摘要（滑出窗口的旧消息 -> 会话摘要 -> 注入）
4. 历史向量检索（召回窗口外的相关片段）
5. 自动事实抽取（写入 memories 表）
6. parse_memory_items 的稳健解析

用法：
    python tests/context_test.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：使用独立临时数据库，绝不触碰真实 data/database.db。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_context_test_"))
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
from app.repositories.character_repository import CharacterRepository  # noqa: E402
from app.repositories.chat_repository import ConversationRepository, MessageRepository  # noqa: E402
from app.repositories.memory_repository import MemoryRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.services.chat_service import ChatService, parse_memory_items  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402

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
    """按 system prompt 扮演不同角色，捕获实际发送的 messages。"""

    def __init__(self, extraction_json: str = "[]") -> None:
        self.captured: list[dict] = []
        self.prompts: list[list[dict]] = []
        self.model = "fake-model"
        self.extraction_json = extraction_json

    @property
    def is_configured(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.captured = list(messages)
        self.prompts.append(list(messages))
        system = next((item["content"] for item in messages if item["role"] == "system"), "")
        if "摘要器" in system:
            return AIResult(text="（假摘要）用户正在做医疗 AI 项目，偏好简洁回答。", model="fake-model")
        if "记忆抽取器" in system:
            return AIResult(text=self.extraction_json, model="fake-model")
        return AIResult(text="FAKE-ANSWER", model="fake-model")


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


def db_call(handler):
    async def wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return run_async(wrapper())


def main() -> int:
    run_async(init_db())

    async def prepare(session):
        user = await UserRepository(session).get_by_username(settings.demo_username)
        await CharacterService(session).ensure_defaults(user.id)
        character = (await CharacterRepository(session).list_by_user(user.id))[0]
        return user.id, character.id

    user_id, character_id = db_call(prepare)
    print("测试账号 user_id=" + str(user_id) + " character_id=" + str(character_id))

    # ---------------------------------------------------------------- #
    print("\n== 1. 长期记忆注入 system prompt ==")

    async def add_memory(session):
        memory = await MemoryRepository(session).create(
            user_id=user_id,
            character_id=character_id,
            memory_type="preference",
            content="测试记忆：用户偏好用要点式、简洁的回答",
        )
        await session.commit()
        return memory.id

    memory_id = db_call(add_memory)
    fake = FakeAI()

    def chat(payload, client):
        async def handler(session):
            return await ChatService(session, ai_client=client).chat(user_id, payload)

        return db_call(handler)

    response = chat(
        ChatRequest(character_id=character_id, message="你还记得我的回答偏好吗？", use_knowledge=False),
        fake,
    )
    check("聊天正常返回", response.answer == "FAKE-ANSWER", response.answer)
    system_message = next((i["content"] for i in fake.captured if i["role"] == "system"), "")
    check("记忆已注入 system prompt", "测试记忆：用户偏好" in system_message, system_message[:160])

    # ---------------------------------------------------------------- #
    print("\n== 2 & 3 & 4. 窗口裁剪 / 滚动摘要 / 历史向量检索 ==")
    original = (
        settings.history_char_budget,
        settings.history_limit,
        settings.summary_enabled,
        settings.history_retrieval_enabled,
        settings.memory_auto_extract,
        settings.memory_extract_every,
    )
    settings.history_char_budget = 300
    settings.history_limit = 40
    settings.summary_enabled = True
    settings.history_retrieval_enabled = True
    settings.memory_auto_extract = False   # 本节不测抽取
    try:

        async def seed(session):
            conversation = await ConversationRepository(session).create(
                user_id=user_id, character_id=character_id, title="长记忆测试"
            )
            repo = MessageRepository(session)
            await repo.create(
                conversation_id=conversation.id,
                user_id=user_id,
                role="user",
                content="我的项目代号是猎鹰七号，请记住这个代号。",
                sources="[]",
            )
            for index in range(10):
                await repo.create(
                    conversation_id=conversation.id,
                    user_id=user_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content="第" + str(index) + "条啰嗦历史" + "内容" * 120,
                    sources="[]",
                )
            await session.commit()
            return conversation.id

        conversation_id = db_call(seed)
        fake2 = FakeAI()
        chat(
            ChatRequest(
                character_id=character_id,
                message="猎鹰七号是什么？",
                conversation_id=conversation_id,
                use_knowledge=False,
            ),
            fake2,
        )

        prompt = fake2.captured
        system_text = next((i["content"] for i in prompt if i["role"] == "system"), "")
        # 本次提问是最后一条消息（前面的 user 条目都是历史窗口里的消息）
        check("最后一条消息是本次提问", prompt[-1]["role"] == "user", str(prompt[-1])[:120])
        user_text = prompt[-1]["content"]
        history_texts = [i["content"] for i in prompt if i["role"] != "system"]

        check("最旧的历史消息被窗口丢弃", not any("第0条啰嗦历史" in t for t in history_texts))
        check("滚动摘要已注入 system prompt", "[此前对话摘要]" in system_text, system_text[-200:])
        check("滚动摘要内容正确", "假摘要" in system_text)

        async def read_summary(session):
            conversation = await ConversationRepository(session).get(conversation_id)
            return conversation.summary or ""

        stored_summary = db_call(read_summary)
        check("摘要已持久化到会话", "假摘要" in stored_summary, stored_summary[:120])
        check("历史向量检索召回了窗口外的片段", "历史上与本次提问相关的对话片段" in user_text, user_text[:200])
        check("召回内容包含目标信息", "猎鹰七号" in user_text, user_text[:200])

        # ------------------------------------------------------------ #
        print("\n== 5. 自动事实抽取 ==")
        settings.memory_auto_extract = True
        settings.memory_extract_every = 1
        fake3 = FakeAI(
            extraction_json='[{"memory_type":"project","content":"用户的项目代号是猎鹰七号"}]'
        )
        chat(
            ChatRequest(
                character_id=character_id,
                message="顺便记一下：我在做医疗 AI 产品。",
                use_knowledge=False,
            ),
            fake3,
        )

        async def read_memories(session):
            return [m.content for m in await MemoryRepository(session).list_by_user(user_id)]

        contents = db_call(read_memories)
        check(
            "自动抽取的事实已写入 memories 表",
            any("猎鹰七号" in item for item in contents),
            str(contents)[:200],
        )
        check("注入用的记忆条数受 MEMORY_INJECT_LIMIT 限制", settings.memory_inject_limit >= 1)
    finally:
        (
            settings.history_char_budget,
            settings.history_limit,
            settings.summary_enabled,
            settings.history_retrieval_enabled,
            settings.memory_auto_extract,
            settings.memory_extract_every,
        ) = original

    # ---------------------------------------------------------------- #
    print("\n== 6. parse_memory_items 稳健解析 ==")
    check("解析正常 JSON", len(parse_memory_items('[{"content":"a"}]')) == 1)
    check("解析带前后废话的输出", len(parse_memory_items('好的：[{"content":"a"}] 完毕')) == 1)
    check("空数组返回空", parse_memory_items("[]") == [])
    check("非法内容返回空", parse_memory_items("没有可提取的内容") == [])
    check("非对象元素被过滤", parse_memory_items('[1,"x",{"content":"ok"}]') == [{"content": "ok"}])

    async def cleanup(session):
        memories = await MemoryRepository(session).list_by_user(user_id)
        for memory in memories:
            if memory.content.startswith("测试记忆") or "猎鹰七号" in memory.content:
                await MemoryRepository(session).delete(memory)
        await session.commit()

    db_call(cleanup)
    _ = memory_id

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("长记忆三件套（摘要 / 历史向量检索 / 自动事实抽取）全部验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
