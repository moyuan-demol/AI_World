"""对话性问题（你是谁 / 自我介绍）识别 + 相关性门槛集成测试。

背景（用户实测问题）：
    问「你是做什么的，介绍一下你自己」时，离线检索把公共库里的
    「问题 2：两路检索结果用什么算法融合?」当成相关片段列了出来 —— 二者只共享
    "是 / 什 / 么"这类常见字，却刚好越过了 0.18 的最低相关性阈值，形成"假命中"。

本测试锁定两件事：
1. is_self_intro_question 的正例 / 反例边界（领域问题不能被误伤）；
2. ChatService 在对话性问题上改用更高的 self_intro_min_score：
   候选片段虽然被检索到了，但没有一条达到该门槛时，送入模型的上下文里
   **不包含**该无关文档，且离线兜底给出角色自我介绍（含角色名与身份）。

离线、临时 SQLite、确定性；不调用真实模型、不联网。
用法：
    python tests/self_intro_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：独立临时数据库 + 独立上传目录，绝不触碰真实 data/database.db。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_self_intro_test_"))
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
from app.rag.self_intro import is_self_intro_question, normalize_question  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

# 无关文档的内容：故意包含"介绍"这一与寒暄问句共享的双字词，
# 保证在 retrieval_min_score=0 时它**一定**能通过 BM25 进入候选 ——
# 这样"上下文里没有它"才真正证明了 self_intro_min_score 过滤在起作用。
UNRELATED_CONTENT = (
    "这篇文档介绍两路检索结果的融合方式：RRF（Reciprocal Rank Fusion，k=60），"
    "同一文档最多保留 2 条切片。"
)
# 只出现在文档正文、不出现在问题里的标记，用来断言"文档是否进入上下文"。
UNRELATED_MARKER = "Reciprocal Rank Fusion"
INTRO_QUESTION = "你是做什么的，介绍一下你自己"
CONTROL_QUESTION = "两路检索结果用什么算法融合"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class FakeAI:
    """已配置的假模型：捕获送进来的 messages 与离线兜底，返回固定文本。"""

    def __init__(self) -> None:
        self.captured: list[dict] = []
        self.offline_fallback = None
        self.model = "fake-model"

    @property
    def is_configured(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.captured = list(messages)
        self.offline_fallback = kwargs.get("offline_fallback")
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


async def prepare(session):
    user = await UserRepository(session).create(
        username="self_intro_user", password_hash="x", email=None, role="user"
    )
    await session.commit()
    character = await CharacterService(session).create(
        user.id, CharacterCreate(name="自介助手", role="测试角色")
    )
    base = await KnowledgeService(session).create(
        user.id, KnowledgeCreate(name="无关资料库", description="")
    )
    vector = (await embed_texts([UNRELATED_CONTENT]))[0]
    await DocumentRepository(session).create(
        knowledge_id=base.id,
        user_id=user.id,
        filename="无关文档.txt",
        chunk_index=0,
        content=UNRELATED_CONTENT,
        embedding=json.dumps(vector),
    )
    await session.commit()
    return user.id, character.id


def section_pure() -> None:
    print("== 1. is_self_intro_question：正例（典型句式都要命中） ==")
    positives = [
        ("你是谁", "你是谁"),
        ("你是做什么的", "你是做什么的"),
        ("你是干什么的", "你是干什么的"),
        ("介绍一下你自己", "介绍一下你自己"),
        ("请做个自我介绍", "请做个自我介绍"),
        ("你能做什么？", "你能做什么？"),
        ("你会什么", "你会什么"),
        ("你能帮我做什么", "你能帮我做什么"),
        ("你叫什么名字", "你叫什么名字"),
        ("你好", "你好"),
        ("在吗？", "在吗？"),
        ("你是做什么的，介绍一下你自己", "你是做什么的，介绍一下你自己"),
    ]
    for name, text in positives:
        check("正例：" + name, is_self_intro_question(text), text)

    print("\n== 1b. 反例（领域问题 / 问候+领域问题不能被误伤） ==")
    negatives = [
        ("某某药物的作用是什么", "某某药物的作用是什么"),
        ("这个系统的检索阈值是多少", "这个系统的检索阈值是多少"),
        ("你好，请问阿司匹林怎么吃", "你好，请问阿司匹林怎么吃"),
        ("你能帮我查一下这篇论文的作者吗", "你能帮我查一下这篇论文的作者吗"),
        ("什么是 RRF 融合", "什么是 RRF 融合"),
        ("这个AI助手是做什么的", "这个AI助手是做什么的"),
        ("空字符串", ""),
        ("纯标点", "？。"),
    ]
    for name, text in negatives:
        check("反例：" + name, not is_self_intro_question(text), text)

    check(
        "归一化会去掉空白与标点",
        normalize_question(" 你 是 谁 ？ ") == "你是谁",
        normalize_question(" 你 是 谁 ？ "),
    )


def section_integration() -> None:
    print("\n== 2. 集成：对话性问题不把无关片段带进上下文 ==")
    run_async(init_db())
    user_id, character_id = db_call(prepare)

    saved = (
        settings.retrieval_min_score,
        settings.self_intro_min_score,
        settings.query_rewrite_enabled,
        settings.memory_auto_extract,
    )
    # 把通用阈值降到 0，保证无关文档一定进入候选；把 self_intro 门槛抬到 0.99，
    # 保证它一定达不到。若没有本次新增的过滤，它会原样出现在上下文里（见控制组与候选断言）。
    settings.retrieval_min_score = 0.0
    settings.self_intro_min_score = 0.99
    settings.query_rewrite_enabled = False  # 保证捕获到的就是最终 prompt，不被改写调用覆盖
    settings.memory_auto_extract = False
    try:
        # 候选断言：无关文档对同一个寒暄问题确实是"被检索到"的（否则过滤无从谈起）
        async def retrieve_intro(session):
            return await RagService(session).retrieve(user_id=user_id, query=INTRO_QUESTION)

        candidates = db_call(retrieve_intro)
        check(
            "前提：该无关文档对同一问题确实被检索成候选（证明过滤真的在起作用）",
            any(UNRELATED_MARKER in chunk.content for chunk in candidates),
            str([chunk.content[:40] for chunk in candidates]),
        )

        # 控制组：普通问题走完整链路（不过滤），文档会进入上下文
        control = FakeAI()
        db_call(
            lambda session: ChatService(session, ai_client=control).chat(
                user_id,
                ChatRequest(
                    character_id=character_id,
                    message=CONTROL_QUESTION,
                    use_knowledge=True,
                ),
            )
        )
        control_prompt = "\n".join(item["content"] for item in control.captured)
        check(
            "控制组：普通问题会把该文档放进上下文",
            UNRELATED_MARKER in control_prompt,
            control_prompt[-200:],
        )

        # 实验组：对话性问题 -> 高门槛把无关文档挡在上下文之外
        fake = FakeAI()
        response = db_call(
            lambda session: ChatService(session, ai_client=fake).chat(
                user_id,
                ChatRequest(
                    character_id=character_id,
                    message=INTRO_QUESTION,
                    use_knowledge=True,
                ),
            )
        )
        prompt = "\n".join(item["content"] for item in fake.captured)
        check("实验组：上下文里不包含该无关文档", UNRELATED_MARKER not in prompt, prompt[-200:])
        check("实验组：回答确实调用了模型", response.answer == "FAKE-ANSWER", response.answer)
        check("实验组：sources 为空，不列出任何无关片段", response.sources == [], str(response.sources))

        offline = fake.offline_fallback() if fake.offline_fallback else ""
        check(
            "离线兜底包含角色自我介绍（角色名 + 身份）",
            "我是 自介助手" in offline and "测试角色" in offline,
            offline[:200],
        )
        check("离线兜底同样不含无关文档内容", UNRELATED_MARKER not in offline, offline[:200])
    finally:
        (
            settings.retrieval_min_score,
            settings.self_intro_min_score,
            settings.query_rewrite_enabled,
            settings.memory_auto_extract,
        ) = saved

    check(
        "self_intro_min_score 默认值为 0.30",
        abs(settings.self_intro_min_score - 0.30) < 1e-9,
        str(settings.self_intro_min_score),
    )


def main() -> int:
    section_pure()
    section_integration()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("对话性问题（自我介绍）识别与相关性门槛验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
