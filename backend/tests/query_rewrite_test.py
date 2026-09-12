"""普通模式查询改写测试（FakeAI，离线、确定性，不调用真实模型）。

覆盖：
1. 纯函数 needs_rewrite / parse_rewritten_queries
2. (a) 模型已配置且问题较长 -> 发生改写，最终上下文包含"改写查询"检索到的文档
3. (b) 模型未配置 -> 不调用改写、正常检索、不报错
4. (c) 模型抛异常 -> 回退原问题检索、不报错（优雅降级）

用法：
    python tests/query_rewrite_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：独立临时数据库，绝不触碰真实 data/database.db
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_rewrite_test_"))
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
from app.rag.query_rewrite import needs_rewrite, parse_rewritten_queries  # noqa: E402
from app.repositories.character_repository import CharacterRepository  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.knowledge_repository import KnowledgeRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

REWRITE_MARK = "检索查询改写器"   # 改写 system prompt 的特征词

# 长问题（> 20 字，且不含目标文档的术语），用于证明"改写带来的召回增量"
LONG_QUESTION = (
    "我最近在做一份很长的行业调研，想请你帮我系统地梳理一下这个领域的关键进展与主要挑战，"
    "最好能结合我们的项目给出一些具体建议。"
)
# 长问题（含"合规风险"与"以及"），用于验证未配置 / 异常时的正常检索
PLAIN_QUESTION = "能否详细说明我们这个医疗项目的合规风险主要有哪些方面以及我们应该如何应对？"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeAI:
    """模仿项目现有测试里的 FakeAI：is_configured + async chat -> 带 .text 的对象。"""

    def __init__(self, rewrite_text: str = "猎鹰七号", configured: bool = True, raise_on_rewrite: bool = False) -> None:
        self.rewrite_text = rewrite_text
        self.configured = configured
        self.raise_on_rewrite = raise_on_rewrite
        self.prompts: list[list[dict]] = []

    @property
    def is_configured(self) -> bool:
        return self.configured

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.prompts.append(list(messages))
        system = next((item["content"] for item in messages if item["role"] == "system"), "")
        if REWRITE_MARK in system:
            if self.raise_on_rewrite:
                raise RuntimeError("模拟模型调用异常")
            return AIResult(text=self.rewrite_text, model="fake-model")
        return AIResult(text="FAKE-ANSWER", model="fake-model")

    def rewrite_calls(self) -> list[list[dict]]:
        return [
            prompt
            for prompt in self.prompts
            if any(REWRITE_MARK in (item.get("content") or "") for item in prompt)
        ]

    def final_user_content(self) -> str:
        if not self.prompts:
            return ""
        return next(
            (item["content"] for item in reversed(self.prompts[-1]) if item["role"] == "user"),
            "",
        )


_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def db_call(handler):
    async def wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return asyncio.run_coroutine_threadsafe(wrapper(), _loop).result()


async def seed(session) -> dict:
    user = await UserRepository(session).get_by_username(settings.demo_username)
    if user is None:
        from app.services.auth_service import AuthService

        token, _created = await AuthService(session).demo_login()
        user = token.user
    await CharacterService(session).ensure_defaults(user.id)
    character = (await CharacterRepository(session).list_by_user(user.id))[0]

    knowledge = await KnowledgeRepository(session).create(
        user_id=user.id, name="项目资料库", description=""
    )
    await session.commit()

    documents = DocumentRepository(session)
    texts = {
        "猎鹰七号.md": "猎鹰七号是我们项目的内部代号，目标是用 RAG 构建医疗检索系统，覆盖影像与病历场景。",
        "合规.md": "合规风险清单：数据脱敏、审计留痕与医疗器械注册要求，必须提示人工复核。",
        "通用.md": "该系统的部署说明与运维手册，包含数据库备份与监控告警配置。",
    }
    for filename, content in texts.items():
        vector = (await embed_texts([content]))[0]
        await documents.create(
            knowledge_id=knowledge.id,
            user_id=user.id,
            filename=filename,
            chunk_index=0,
            content=content,
            embedding=json.dumps(vector),
        )
    await session.commit()
    return {"user_id": user.id, "character_id": character.id, "knowledge_id": knowledge.id}


def ask(ctx, fake, message):
    async def handler(session):
        return await ChatService(session, ai_client=fake).chat(
            ctx["user_id"],
            ChatRequest(
                character_id=ctx["character_id"],
                message=message,
                knowledge_id=ctx["knowledge_id"],
                use_knowledge=True,
            ),
        )

    return db_call(handler)


def run() -> int:
    print("== 1. 纯函数：触发条件与解析 ==")
    check("长问题触发改写", needs_rewrite("这是一个超过二十个字的比较长的用户问题用于检索改写", 20) is True)
    check("短问题不触发改写", needs_rewrite("心肌梗死？", 20) is False)
    check("多个问句触发改写", needs_rewrite("这是什么？为什么？", 20) is True)
    check("并列连接词触发改写", needs_rewrite("请说明架构并且给出风险", 20) is True)
    check("空问题不触发改写", needs_rewrite("", 20) is False)

    parsed = parse_rewritten_queries("猎鹰七号\n医疗检索系统\n合规风险\n第四条应被截断")
    check("最多取 3 条改写查询", parsed == ["猎鹰七号", "医疗检索系统", "合规风险"], str(parsed))
    check("去掉编号与项目符号", parse_rewritten_queries("1. 查询一\n- 查询二") == ["查询一", "查询二"], str(parse_rewritten_queries("1. 查询一\n- 查询二")))
    check(
        "查询自身的数字前缀被保留",
        parse_rewritten_queries("2026版指南要点") == ["2026版指南要点"],
        str(parse_rewritten_queries("2026版指南要点")),
    )
    check("空输出解析为空列表", parse_rewritten_queries("") == [])
    check("过长输出视为不可用", parse_rewritten_queries("x" * 100) == [])

    print("\n== 2. 准备数据 ==")
    asyncio.run_coroutine_threadsafe(init_db(), _loop).result()
    ctx = db_call(seed)
    print("  已写入 3 篇文档，knowledge_id=" + str(ctx["knowledge_id"]))

    original = (
        settings.summary_enabled,
        settings.history_retrieval_enabled,
        settings.memory_auto_extract,
        settings.query_rewrite_enabled,
        settings.query_rewrite_min_chars,
    )
    settings.summary_enabled = False           # 隔离：本节只关心查询改写
    settings.history_retrieval_enabled = False
    settings.memory_auto_extract = False
    settings.query_rewrite_enabled = True
    settings.query_rewrite_min_chars = 20
    try:
        print("\n== 3. (a) 已配置 + 长问题 -> 发生改写并检索到改写目标的文档 ==")
        fake_a = FakeAI(rewrite_text="猎鹰七号", configured=True)
        response_a = ask(ctx, fake_a, LONG_QUESTION)
        check("改写被调用一次", len(fake_a.rewrite_calls()) == 1, str(len(fake_a.rewrite_calls())))
        check("原问题本身检索不到该文档（保证证明确有意义）", "猎鹰七号" not in LONG_QUESTION)
        check(
            "最终上下文包含改写检索到的文档内容",
            "猎鹰七号" in fake_a.final_user_content(),
            fake_a.final_user_content()[:200],
        )
        check("聊天正常返回", response_a.answer == "FAKE-ANSWER", response_a.answer)
        check("引用来源非空", len(response_a.sources) >= 1, str(len(response_a.sources)))

        print("\n== 4. (b) 模型未配置 -> 不改写、正常检索、不报错 ==")
        fake_b = FakeAI(configured=False)
        response_b = ask(ctx, fake_b, PLAIN_QUESTION)
        check("未配置时不调用改写", len(fake_b.rewrite_calls()) == 0, str(len(fake_b.rewrite_calls())))
        check(
            "仍按原问题正常检索到文档",
            "数据脱敏" in fake_b.final_user_content(),
            fake_b.final_user_content()[:200],
        )
        check("聊天正常返回", response_b.answer == "FAKE-ANSWER", response_b.answer)

        print("\n== 5. (c) 模型抛异常 -> 回退原问题检索、不报错 ==")
        fake_c = FakeAI(configured=True, raise_on_rewrite=True)
        response_c = ask(ctx, fake_c, PLAIN_QUESTION)
        check("确实尝试过改写", len(fake_c.rewrite_calls()) == 1, str(len(fake_c.rewrite_calls())))
        check(
            "异常后回退原问题检索仍拿到文档",
            "数据脱敏" in fake_c.final_user_content(),
            fake_c.final_user_content()[:200],
        )
        check("聊天正常返回（未抛出异常）", response_c.answer == "FAKE-ANSWER", response_c.answer)

        print("\n== 6. 关闭开关时不改写 ==")
        settings.query_rewrite_enabled = False
        fake_d = FakeAI(configured=True)
        ask(ctx, fake_d, LONG_QUESTION)
        check("query_rewrite_enabled=False 时不调用改写", len(fake_d.rewrite_calls()) == 0, str(len(fake_d.rewrite_calls())))
    finally:
        (
            settings.summary_enabled,
            settings.history_retrieval_enabled,
            settings.memory_auto_extract,
            settings.query_rewrite_enabled,
            settings.query_rewrite_min_chars,
        ) = original

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("普通模式查询改写（含优雅降级）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
