"""角色知识边界（身份隔离）测试 —— 不需要模型、离线可跑。

验证：
1. 绑定了知识库的角色，只检索绑定的那些（不串味）
2. 未绑定时，回落到该用户的全部知识库
3. 只能绑定属于自己的知识库（越权绑定会被过滤掉）
4. 删除角色会清理绑定关系

用法：
    python tests/character_knowledge_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：独立临时数据库，绝不触碰真实 data/database.db
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_boundary_test_"))
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
from app.services.auth_service import AuthService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402

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
    token, _created = await AuthService(session).demo_login()
    user_id = token.user.id

    knowledge = KnowledgeRepository(session)
    documents = DocumentRepository(session)
    characters = CharacterService(session)

    medical = await knowledge.create(user_id=user_id, name="医疗资料库", description="")
    market = await knowledge.create(user_id=user_id, name="市场资料库", description="")
    await session.commit()

    texts = {
        medical.id: "心肌梗死的早期识别与溶栓治疗规范，胸痛患者需尽快完成心电图检查。",
        market.id: "医疗人工智能市场规模预计持续增长，商业模式以订阅制与项目制为主。",
    }
    for knowledge_id, content in texts.items():
        vector = (await embed_texts([content]))[0]
        await documents.create(
            knowledge_id=knowledge_id,
            user_id=user_id,
            filename="资料.txt",
            chunk_index=0,
            content=content,
            embedding=json.dumps(vector),
        )
    await session.commit()

    doctor = await characters.create(
        user_id, CharacterCreate(name="测试医生", role="医学专家", expertise="临床")
    )
    advisor = await characters.create(
        user_id, CharacterCreate(name="测试顾问", role="商业顾问", expertise="市场")
    )
    await session.commit()
    return {
        "user_id": user_id,
        "medical": medical.id,
        "market": market.id,
        "doctor": doctor.id,
        "advisor": advisor.id,
    }


def run() -> int:
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)
    print("用户:", ctx["user_id"], "| 医疗库:", ctx["medical"], "| 市场库:", ctx["market"])
    print()

    print("== 1. 绑定知识边界 ==")

    async def bind(session):
        service = CharacterService(session)
        await service.set_knowledge_ids(ctx["user_id"], ctx["doctor"], [ctx["medical"]])
        await service.set_knowledge_ids(ctx["user_id"], ctx["advisor"], [ctx["market"]])
        return (
            await service.knowledge_ids(ctx["user_id"], ctx["doctor"]),
            await service.knowledge_ids(ctx["user_id"], ctx["advisor"]),
        )

    doctor_kbs, advisor_kbs = db_call(bind)
    check("医生绑定医疗库", doctor_kbs == [ctx["medical"]], str(doctor_kbs))
    check("顾问绑定市场库", advisor_kbs == [ctx["market"]], str(advisor_kbs))

    print("\n== 2. 同一问题、不同角色，检索到的内容互不串味 ==")
    question = "医疗人工智能的临床与市场情况如何？"

    def ask(character_id: int):
        """注意：这里必须是同步函数再调用 db_call。

        不能写成 async 函数后再用 run_coroutine_threadsafe 提交 ——
        那样会在事件循环线程内部再次向同一个循环提交任务并阻塞等待，直接死锁。
        """
        fake = FakeAI()

        async def handler(session):
            return await ChatService(session, ai_client=fake).chat(
                ctx["user_id"],
                ChatRequest(character_id=character_id, message=question, use_knowledge=True),
            )

        response = db_call(handler)
        prompt_text = "\n".join(item["content"] for item in fake.captured)
        return response, prompt_text

    _, doctor_prompt = ask(ctx["doctor"])
    _, advisor_prompt = ask(ctx["advisor"])

    check("医生只看到医疗库内容", "心肌梗死" in doctor_prompt, doctor_prompt[:120])
    check("医生看不到市场库内容", "商业模式" not in doctor_prompt, doctor_prompt[:120])
    check("顾问只看到市场库内容", "商业模式" in advisor_prompt, advisor_prompt[:120])
    check("顾问看不到医疗库内容", "心肌梗死" not in advisor_prompt, advisor_prompt[:120])

    print("\n== 3. 越权绑定会被过滤（只能绑定自己的库）==")
    async def bind_someone_elses(session):
        service = CharacterService(session)
        return await service.set_knowledge_ids(
            ctx["user_id"], ctx["doctor"], [ctx["medical"], 999999]
        )

    result = db_call(bind_someone_elses)
    check("不存在的知识库被过滤", result == [ctx["medical"]], str(result))

    print("\n== 4. 不绑定则回落到全部知识库 ==")
    async def unbind(session):
        return await CharacterService(session).set_knowledge_ids(ctx["user_id"], ctx["doctor"], [])

    db_call(unbind)
    _, prompt_all = ask(ctx["doctor"])
    check("取消绑定后能检索到全部知识库", "心肌梗死" in prompt_all and "商业模式" in prompt_all, prompt_all[:120])

    print("\n== 5. 删除角色会清理绑定 ==")

    async def cleanup(session):
        service = CharacterService(session)
        before = await service.knowledge_ids(ctx["user_id"], ctx["advisor"])
        await service.delete(ctx["user_id"], ctx["advisor"])
        return before

    before = db_call(cleanup)
    check("删除前确实有绑定", before == [ctx["market"]], str(before))

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("角色知识边界（身份隔离）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
