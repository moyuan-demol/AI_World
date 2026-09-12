"""上下文机制测试：验证「长期记忆注入」与「历史窗口裁剪」。

不调用真实模型（用假的 AI 客户端捕获 prompt），因此结果确定、可离线运行。

用法：
    python tests/context_test.py
"""

import asyncio
import sys
import threading
from pathlib import Path

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
from app.services.chat_service import ChatService  # noqa: E402
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
    """捕获实际发送给模型的 messages，不发起网络请求。"""

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
        # 清掉历史遗留的测试记忆，避免互相干扰
        for memory in await MemoryRepository(session).list_by_user(user.id):
            if memory.content.startswith("测试记忆"):
                await MemoryRepository(session).delete(memory)
        await session.commit()
        return user.id, character.id

    user_id, character_id = db_call(prepare)
    print("演示账号 user_id=" + str(user_id) + " character_id=" + str(character_id))

    print("\n== 1. 长期记忆会被注入 system prompt ==")

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

    async def do_chat(session):
        return await ChatService(session, ai_client=fake).chat(
            user_id,
            ChatRequest(character_id=character_id, message="你还记得我的回答偏好吗？", use_knowledge=False),
        )

    response = db_call(do_chat)
    check("聊天正常返回", response.answer == "FAKE-ANSWER", response.answer)
    system_message = next((item["content"] for item in fake.captured if item["role"] == "system"), "")
    check("记忆已注入 system prompt", "测试记忆：用户偏好" in system_message, system_message[:160])

    print("\n== 2. 历史窗口受字符预算限制（超出丢弃最旧）==")
    original_budget = settings.history_char_budget
    settings.history_char_budget = 1200
    try:
        async def seed_history(session):
            conversation = await ConversationRepository(session).create(
                user_id=user_id, character_id=character_id, title="上下文裁剪测试"
            )
            repo = MessageRepository(session)
            for index in range(12):
                await repo.create(
                    conversation_id=conversation.id,
                    user_id=user_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content="第" + str(index) + "条历史消息" + "内容" * 120,  # 约 250 字符
                    sources="[]",
                )
            await session.commit()
            return conversation.id

        conversation_id = db_call(seed_history)
        fake2 = FakeAI()

        async def chat_with_history(session):
            return await ChatService(session, ai_client=fake2).chat(
                user_id,
                ChatRequest(
                    character_id=character_id,
                    message="最新问题",
                    conversation_id=conversation_id,
                    use_knowledge=False,
                ),
            )

        db_call(chat_with_history)
        history_parts = [item["content"] for item in fake2.captured if item["role"] != "system"]
        total_chars = sum(len(part) for part in history_parts)
        check(
            "prompt 总长度受预算约束（< 预算 + 单条余量）",
            total_chars < 1200 + 300,
            "实际 " + str(total_chars),
        )
        check(
            "最旧的历史消息被丢弃",
            not any("第0条历史消息" in part for part in history_parts),
            "仍包含第0条",
        )
        check(
            "最新问题仍在 prompt 中",
            any("最新问题" in part for part in history_parts),
        )
    finally:
        settings.history_char_budget = original_budget

    async def cleanup(session):
        await MemoryRepository(session).delete(await MemoryRepository(session).get(memory_id))
        conversations = await ConversationRepository(session).list_by_user(user_id, character_id=character_id)
        for conversation in conversations:
            if conversation.title == "上下文裁剪测试":
                for message in await MessageRepository(session).list_by_conversation(conversation.id):
                    await MessageRepository(session).delete(message)
                await ConversationRepository(session).delete(conversation)
        await session.commit()

    db_call(cleanup)

    print("\n" + "=" * 56)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("上下文机制验证通过：记忆会注入，历史会被预算裁剪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
