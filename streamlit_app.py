"""AI World - Streamlit 演示版（免费公网部署）

部署到 Streamlit Community Cloud 即可获得永久域名：
    https://<你的应用名>.streamlit.app

设计要点：
- 直接复用 backend/app 的 Service 层（AI 伙伴 / 知识库+RAG / 聊天 / AI 圆桌 / 记忆），
  不需要另外启动 FastAPI 服务，一个 Streamlit 应用就是完整产品；
- 数据默认落在 SQLite，云端实例重启会重置（演示用途足够，要持久可换 Postgres）；
- 若在 Streamlit secrets 配置了 APP_PASSWORD，进入前必须输入口令（防止公网被乱刷额度）。
"""

import asyncio
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

import streamlit as st

# --------------------------------------------------------------------------- #
# 1. 运行环境准备（必须早于 import app.*）
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_streamlit_secrets_into_env() -> dict:
    """把 Streamlit secrets 注入环境变量，并返回诊断信息（用于界面自检）。

    注意：Secrets 里只要有一行 TOML 不合法，st.secrets 会**整体读取失败**，
    此时必须把错误暴露出来，而不是静默回落到 SQLite（否则用户完全看不出问题）。
    """
    report: dict = {"ok": False, "keys": [], "error": ""}
    try:
        secrets = dict(st.secrets)
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:220]
        return report

    for key, value in secrets.items():
        name = str(key).upper()
        if name.isupper() and isinstance(value, (str, int, float)):
            # 用直接赋值：secrets 的优先级高于环境里可能存在的同名空值
            os.environ[name] = str(value)
            report["keys"].append(name)
    report["ok"] = True
    return report


@st.cache_resource(show_spinner=False)
def _pick_writable_data_dir() -> Path:
    """只做一次：原来每次脚本重跑都会写一个临时文件再删（无谓的磁盘 IO）。"""
    preferred = ROOT_DIR / "data"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except Exception:
        fallback = Path(tempfile.gettempdir()) / "ai_world_data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


_CREDENTIALS_RE = re.compile(r"://[^:/@\s]+:[^@/\s]+@")


def redact_credentials(text: str) -> str:
    """把错误信息里可能出现的 账号:密码@ 打码，保证可以安全显示在页面上。"""
    return _CREDENTIALS_RE.sub("://***:***@", str(text))[:400]


def api_database_health() -> dict:
    """数据库自检：能否连通、有哪些表、数据量、失败原因（凭据自动打码）。"""

    async def handler(session):
        await session.execute(text("SELECT 1"))

        def _tables(sync_conn):
            return sorted(inspect(sync_conn).get_table_names())

        tables = await session.run_sync(_tables)
        characters = await session.scalar(text("SELECT count(*) FROM characters"))
        users = await session.scalar(text("SELECT count(*) FROM users"))
        return {
            "tables": tables,
            "characters": int(characters or 0),
            "users": int(users or 0),
        }

    backend = "PostgreSQL" if not settings.is_sqlite else "SQLite"
    try:
        return {"ok": True, "backend": backend, **db_call(handler)}
    except Exception as exc:
        return {
            "ok": False,
            "backend": backend,
            "error": redact_credentials(type(exc).__name__ + ": " + str(exc)),
        }


SECRETS_REPORT: dict = _load_streamlit_secrets_into_env()
DATA_DIR = _pick_writable_data_dir()
os.environ.setdefault("DATA_DIR", str(DATA_DIR))
os.environ.setdefault("UPLOAD_DIR", str(DATA_DIR / "uploads"))

from app.ai.deepseek_client import AIClient  # noqa: E402
from app.rag.embedding import EmbeddingConfig  # noqa: E402
from app.tools.web_search import PROVIDER_CLASSES, WebSearchTool  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.core.files import sanitize_filename, validate_signature  # noqa: E402
from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate, KnowledgeOut  # noqa: E402
from app.schemas.roundtable import AgentSpec, RoundtableRequest  # noqa: E402
from app.schemas.user import UserCreate, UserLogin  # noqa: E402
from app.services.admin_service import AdminService  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402
from app.services.roundtable_service import RoundtableService  # noqa: E402
from app.services.usage_service import UsageService  # noqa: E402

# --------------------------------------------------------------------------- #
# 2. 异步桥：Streamlit 同步，后端 async；用常驻事件循环线程，避免跨循环报错
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def _shared_loop() -> asyncio.AbstractEventLoop:
    """进程内共享的事件循环（必须用 cache_resource 缓存！）。

    踩过的坑：Streamlit **每次交互都会重新执行整个脚本**，
    所以脚本级的全局变量（原来这里的 _loop）会在每次交互时被重置，
    导致每次都新建一个事件循环；而 SQLAlchemy 的连接池是随模块
    （sys.modules）缓存的，池里的连接仍然绑定在**旧循环**上 ——
    于是 asyncpg/PostgreSQL 会抛
    "got Future attached to a different loop"。
    SQLite 的驱动恰好容忍这种错配，所以这个 bug 一直没暴露。

    用 cache_resource 把它缓存到 Streamlit 运行时里，才能真正跨重跑存活。
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def _get_loop() -> asyncio.AbstractEventLoop:
    return _shared_loop()


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result()


def db_call(handler):
    async def _wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return run_async(_wrapper())


@st.cache_resource(show_spinner="正在初始化 AI World ...")
def bootstrap() -> bool:
    """建表 + 确保公共体验账号存在（幂等）。当前用户由登录态决定，不再自动登录。"""
    run_async(init_db())
    return True


# --------------------------------------------------------------------------- #
# 3. 数据访问封装：所有 ORM 对象在 session 内转成 dict
# --------------------------------------------------------------------------- #
def _character_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "role": row.role,
        "personality": row.personality,
        "expertise": row.expertise,
        "speaking_style": row.speaking_style,
        "system_prompt": row.system_prompt,
    }


def _session_from_token(token) -> dict:
    return {"uid": token.user.id, "uname": token.user.username}


def api_register(username: str, password: str, email: str | None) -> dict:
    async def handler(session):
        token = await AuthService(session).register(
            UserCreate(username=username, password=password, email=email)
        )
        await CharacterService(session).ensure_defaults(token.user.id)
        return _session_from_token(token)

    return db_call(handler)


def api_login(username: str, password: str) -> dict:
    async def handler(session):
        token = await AuthService(session).login(UserLogin(username=username, password=password))
        return _session_from_token(token)

    return db_call(handler)


def api_demo_login() -> dict:
    async def handler(session):
        token, _created = await AuthService(session).demo_login()
        await CharacterService(session).ensure_defaults(token.user.id)
        return _session_from_token(token)

    return db_call(handler)


def login_gate() -> int | None:
    """返回当前登录用户 id；未登录则渲染登录/注册界面并返回 None。

    改造原因：原来所有人自动以 demo 账号进入 → 共用同一份数据，
    任何人都能删改别人的内容。现在每个访客注册自己的账号，数据完全隔离。
    """
    uid = st.session_state.get("uid")
    if uid:
        return int(uid)

    st.title("🌍 AI World")
    st.caption("AI 世界 · 你的个人 AI 智能空间。**每个账号的数据完全隔离**，别人看不到也改不了。")
    st.caption(
        "第一次使用？直接切到 **「注册新账号」** 标签：**不需要手机号、不需要邮箱**，"
        "起个名字 + 设个密码即可。"
    )

    tab_login, tab_register = st.tabs(["登录", "注册新账号"])
    with tab_login:
        with st.form("login_form"):
            name = st.text_input(
                "用户名",
                key="login_name",
                placeholder="注册时填的那个名字（不是手机号）",
            )
            password = st.text_input(
                "密码",
                type="password",
                key="login_pwd",
                placeholder="注册时设的密码",
            )
            submitted = st.form_submit_button("登录", type="primary")
        if submitted:
            if not name.strip() or not password:
                st.error("请输入用户名和密码")
            else:
                try:
                    st.session_state.update(**api_login(name.strip(), password))
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(redact_credentials(type(exc).__name__ + ": " + str(exc)))
    with tab_register:
        with st.form("register_form"):
            st.caption(
                "✅ **不需要手机号、不需要邮箱验证** —— 起个名字 + 设个密码就能用，约 30 秒。"
            )
            new_name = st.text_input(
                "用户名（2-20 位，中文/英文/数字均可）",
                key="reg_name",
                placeholder="例如：xiaoming / 小明 / user_01",
                help="随便起一个名字即可，**不是手机号、也不是邮箱**。以后登录就用它，请自己记住。",
            )
            new_pwd = st.text_input(
                "密码（至少 6 位）",
                type="password",
                key="reg_pwd",
                placeholder="自己设一个，例如 MyPwd2026",
                help="建议别用 123456 或生日。目前没有找回密码功能，忘了只能重新注册。",
            )
            new_email = st.text_input(
                "邮箱（选填，仅作备注）",
                key="reg_email",
                placeholder="不填也能注册",
                help="当前不会发送验证邮件，填了只是方便你自己记。",
            )
            submitted_new = st.form_submit_button("注册并进入", type="primary")
        if submitted_new:
            if len(new_name.strip()) < 2 or len(new_pwd) < 6:
                st.error("用户名至少 2 位、密码至少 6 位")
            else:
                try:
                    st.session_state.update(
                        **api_register(new_name.strip(), new_pwd, new_email.strip() or None)
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(redact_credentials(type(exc).__name__ + ": " + str(exc)))

    st.divider()
    st.caption(
        "🔒 本应用为**多用户隔离**：请注册你自己的账号 —— "
        "你的知识库、AI 伙伴与对话记录**只有你自己**能看到和修改。"
    )
    st.caption("（公共体验账号已关闭，避免不同访客互相删改数据。）")
    return None


def invalidate_lists() -> None:
    """任何写操作后清一次缓存，保证立刻看到最新数据。"""
    try:
        st.cache_data.clear()
    except Exception:
        pass


@st.cache_data(ttl=5, show_spinner=False)
def api_list_characters(user_id: int) -> list[dict]:
    async def handler(session):
        rows = await CharacterService(session).list(user_id)
        return [_character_dict(row) for row in rows]

    return db_call(handler)


def api_create_character(user_id: int, payload: CharacterCreate) -> dict:
    async def handler(session):
        row = await CharacterService(session).create(user_id, payload)
        return _character_dict(row)
        invalidate_lists()

    return db_call(handler)


def api_delete_character(user_id: int, character_id: int) -> None:
    async def handler(session):
        await CharacterService(session).delete(user_id, character_id)
        invalidate_lists()

    db_call(handler)


def api_ensure_default_characters(user_id: int) -> int:
    """自愈：公网演示站可能被访客把 AI 伙伴全删光。

    每次运行都检查一次，若一个都不剩就重新生成预置角色，
    保证任何人任何时候点开都是完整体验。已存在时开销仅一次 COUNT 查询。
    """
    async def handler(session):
        return await CharacterService(session).ensure_defaults(user_id)

    return db_call(handler)


def api_character_knowledge(user_id: int, character_id: int) -> list[int]:
    async def handler(session):
        return await CharacterService(session).knowledge_ids(user_id, character_id)

    return db_call(handler)


def api_set_character_knowledge(
    user_id: int, character_id: int, knowledge_ids: list[int]
) -> list[int]:
    async def handler(session):
        return await CharacterService(session).set_knowledge_ids(
            user_id, character_id, knowledge_ids
        )

    return db_call(handler)


@st.cache_data(ttl=5, show_spinner=False)
def api_knowledge_map(user_id: int) -> dict:
    async def handler(session):
        return await CharacterService(session).knowledge_map(user_id)

    return db_call(handler)


@st.cache_data(ttl=5, show_spinner=False)
def api_list_knowledge(user_id: int) -> list[dict]:
    async def handler(session):
        rows = await KnowledgeService(session).list(user_id)
        return [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "document_count": row.document_count,
            }
            for row in rows
        ]

    return db_call(handler)


def api_create_knowledge(user_id: int, name: str, description: str, parent_id=None) -> dict:
    async def handler(session):
        row = await KnowledgeService(session).create(
            user_id,
            KnowledgeCreate(name=name, description=description, parent_id=parent_id),
        )
        invalidate_lists()
        return {"id": row.id, "name": row.name, "document_count": row.document_count}

    return db_call(handler)


def api_delete_knowledge(user_id: int, knowledge_id: int) -> None:
    async def handler(session):
        await KnowledgeService(session).delete(user_id, knowledge_id)
        invalidate_lists()

    db_call(handler)


def api_list_documents(user_id: int, knowledge_id: int, limit: int = 50) -> list[dict]:
    async def handler(session):
        rows = await KnowledgeService(session).list_documents(user_id, knowledge_id, limit=limit)
        return [
            {
                "id": row.id,
                "filename": row.filename,
                "chunk_index": row.chunk_index,
                "content": row.content,
            }
            for row in rows
        ]

    return db_call(handler)


def api_upload_document(
    user_id: int, filename: str, data: bytes, knowledge_id, name, embedding_config=None
) -> dict:
    """与 HTTP 接口一致的安全校验：扩展名白名单 + 文件名净化 + 文件签名校验。"""
    safe_name = sanitize_filename(filename or "upload.txt")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in settings.allowed_extension_list:
        raise ValueError("只允许上传：" + ", ".join(settings.allowed_extension_list))
    if len(data) == 0:
        raise ValueError("文件为空")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError("文件超过 " + str(settings.max_upload_mb) + " MB 上限")
    validate_signature(safe_name, data)

    async def handler(session):
        result = await KnowledgeService(session, embedding_config=embedding_config).upload(
            user_id,
            filename=safe_name,
            data=data,
            knowledge_id=knowledge_id,
            name=name,
        )
        return {
            "filename": result.filename,
            "chunk_count": result.chunk_count,
            "char_count": result.char_count,
            "knowledge_id": result.knowledge.id,
            "knowledge_name": result.knowledge.name,
        }

    return db_call(handler)


def api_chat(
    user_id: int,
    character_id: int,
    message: str,
    conversation_id,
    knowledge_id,
    use_knowledge: bool,
    ai_client: AIClient | None = None,
    embedding_config=None,
    rag_mode: str = "single",
    use_web: bool = False,
    search_tool: WebSearchTool | None = None,
) -> dict:
    async def handler(session):
        payload = ChatRequest(
            character_id=character_id,
            message=message,
            conversation_id=conversation_id,
            knowledge_id=knowledge_id,
            use_knowledge=use_knowledge,
            rag_mode=rag_mode,
            use_web=use_web,
        )
        result = await ChatService(
            session,
            ai_client=ai_client,
            embedding_config=embedding_config,
            search_tool=search_tool,
        ).chat(user_id, payload)
        return {
            "answer": result.answer,
            "conversation_id": result.conversation_id,
            "offline": result.offline,
            "model": result.model,
            "sources": [item.filename + " (" + str(item.score) + ")" for item in result.sources],
            "agents": [
                {"agent": item.agent, "role": item.role, "output": item.output}
                for item in result.agents
            ],
            "sub_questions": result.sub_questions,
            "evidence": result.evidence,
            "rounds": result.rounds,
            "web_reports": result.web_reports,
        }

    return db_call(handler)


def api_load_conversation(user_id: int, character_id: int) -> dict:
    async def handler(session):
        service = ChatService(session)
        conversations = await service.list_conversations(user_id, character_id=character_id)
        conversation_id = conversations[0].id if conversations else None
        messages = await service.history(user_id, character_id=character_id, limit=100)
        return {
            "conversation_id": conversation_id,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
        }

    return db_call(handler)


def api_roundtable_agents(user_id: int) -> list[dict]:
    async def handler(session):
        return await RoundtableService(session).available_agents(user_id)

    return db_call(handler)


def api_run_roundtable(
    user_id: int,
    question: str,
    agents: list[dict],
    use_knowledge: bool,
    knowledge_id,
    ai_client: AIClient | None = None,
    embedding_config=None,
) -> dict:
    async def handler(session):
        payload = RoundtableRequest(
            question=question,
            agents=[AgentSpec(**agent) for agent in agents] if agents else None,
            knowledge_id=knowledge_id,
            use_knowledge=use_knowledge,
            include_manager=True,
        )
        result = await RoundtableService(
            session, ai_client=ai_client, embedding_config=embedding_config
        ).run(user_id, payload)
        return {
            "manager_brief": result.manager_brief,
            "summary": result.summary,
            "manager": result.manager,
            "model": result.model,
            "offline": result.offline,
            "sources": result.sources,
            "results": [
                {"agent": item.agent, "role": item.role, "answer": item.answer, "offline": item.offline}
                for item in result.results
            ],
        }

    return db_call(handler)


# --------------------------------------------------------------------------- #
# 4. 页面
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="AI World · AI 世界", page_icon="🌍", layout="wide")

MODULES = ["我的世界", "AI伙伴", "知识世界", "AI聊天", "AI圆桌"]
USAGE_PAGE = "📊 用量统计"

# 页面 <-> URL 参数（只放页面名，**绝不放** user_id / token / 知识库 id）
PAGE_SLUG = {
    "我的世界": "home",
    "AI伙伴": "characters",
    "知识世界": "knowledge",
    "AI聊天": "chat",
    "AI圆桌": "roundtable",
    USAGE_PAGE: "usage",
}
SLUG_PAGE = {slug: label for label, slug in PAGE_SLUG.items()}


def admin_password() -> str:
    """站点管理员口令（在 Secrets 里配 APP_ADMIN_PASSWORD）。

    未配置时，「用量统计」页面对所有访客隐藏。
    """
    try:
        return str(st.secrets.get("APP_ADMIN_PASSWORD", "") or "")
    except Exception:
        return ""


def nav_items() -> list[str]:
    items = list(MODULES)
    if admin_password():
        items.append(USAGE_PAGE)
    return items


def required_password() -> str:
    try:
        value = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        value = ""
    return str(value or "")


def password_gate() -> None:
    needed = required_password()
    if not needed or st.session_state.get("authed"):
        return
    st.title("🔒 AI World")
    st.caption("该演示站点已设置访问口令。")
    entered = st.text_input("访问口令", type="password")
    if st.button("进入", type="primary"):
        if entered == needed:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("口令不正确")
    st.stop()


def session_ai_client() -> AIClient | None:
    """访客自带 Key -> 用他自己的；没填 -> None（走站点配置或离线演示）。"""
    key = (st.session_state.get("ai_key") or "").strip()
    if not key:
        return None
    base = (st.session_state.get("ai_base") or "").strip()
    return AIClient(api_key=key, base_url=base or None, model=active_model_name())


KNOWN_MODELS = [
    "deepseek-v4-flash",                      # 稳定版（默认）
    "deepseek-v4-pro",                        # 稳定版（更强）
    "deepseek-v4-flash-vision-exp",           # 多模态/视觉
    "deepseek-v4.1-flash-expires-on-0910",    # 4.1 限时内测 ID，到期即失效
    "deepseek-flash",                         # 正式标准标识
]

# 服务商预设：{显示名: (API Base, 推荐模型列表)}
# 选服务商会自动填入对应 Base —— 只加模型名而不填 Base 是连不上的。
# 都是"兼容 OpenAI 协议"的地址，任一服务商都能用同一个客户端。
PROVIDER_PRESETS: dict = {
    "DeepSeek（默认）": ("https://api.deepseek.com", KNOWN_MODELS),
    "智谱 GLM（BigModel）": (
        "https://open.bigmodel.cn/api/paas/v4",
        ["glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4-plus", "glm-4-flash"],
    ),
    "OpenAI（GPT）": (
        "https://api.openai.com/v1",
        ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5-pro", "gpt-5.4-mini", "gpt-6-astra", "o3", "o4-mini"],
    ),
    "自定义服务商": ("", []),
}


def active_model_name() -> str:
    pick = (st.session_state.get("ai_model_pick") or "").strip()
    if pick and pick != "自定义":
        return pick
    custom = (st.session_state.get("ai_model") or "").strip()
    return custom or settings.deepseek_model


def session_embedding_config() -> EmbeddingConfig | None:
    """访客自带的向量服务；没填 -> None（走服务端配置或内置离线实现）。"""
    key = (st.session_state.get("emb_key") or "").strip()
    base = (st.session_state.get("emb_base") or "").strip()
    if not key or not base:
        return None
    model = (st.session_state.get("emb_model") or "").strip() or settings.embedding_model
    return EmbeddingConfig(provider="openai", api_base=base, api_key=key, model=model)


def module_staleness() -> list[str]:
    """检测「新脚本 + 旧模块」——Streamlit 只重跑脚本、不重载已导入模块。

    这是本项目反复踩到的坑（事件循环 / AttributeError / KeyError 都源于此）。
    用"能力探测"判断，不需要维护版本号。
    """
    missing: list[str] = []
    for name in (
        "search_provider_list",
        "multi_agent_enabled",
        "memory_inject_limit",
        "backup_enabled",
        "admin_username_list",
        "history_retrieval_enabled",
    ):
        if not hasattr(settings, name):
            missing.append("settings." + name)
    if "parent_id" not in KnowledgeOut.model_fields:
        missing.append("KnowledgeOut.parent_id")
    return missing


def search_label_map() -> dict:
    """{界面显示标签: provider 内部名称}。

    踩过的坑：多选框存的是**显示标签**，而检索工具需要**内部名称**（wikipedia 等）；
    旧代码直接把标签当名称传进去 → 全部来源匹配失败 → 联网静默失效。
    """
    return {
        cls.label + "（" + name + "）": name for name, cls in PROVIDER_CLASSES.items()
    }


def selected_search_providers() -> list[str]:
    """把界面选择安全地翻译成内部名称；识别不出来就回落到默认来源。"""
    labels = search_label_map()
    picked = st.session_state.get("web_providers") or []
    names = [labels[item] for item in picked if item in labels]
    return names or default_search_providers()


def default_search_providers() -> list[str]:
    """防御式读取默认来源：即使云端模块与脚本版本不一致也不会崩。"""
    raw = getattr(settings, "search_providers", "") or "wikipedia"
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def session_search_tool() -> WebSearchTool | None:
    """访客在侧边栏开启联网检索时，返回一个检索工具；否则 None。"""
    if not st.session_state.get("web_enabled"):
        return None
    return WebSearchTool(
        selected_search_providers(),
        api_key=(st.session_state.get("web_key") or settings.search_api_key),
        base_url=(st.session_state.get("web_base") or settings.search_base_url),
    )


def active_embedding_label() -> str:
    return (session_embedding_config() or EmbeddingConfig.from_settings()).label


def session_key() -> str:
    """本浏览器会话的匿名标识，用于区分不同访客（不含任何个人信息）。"""
    if "sid" not in st.session_state:
        st.session_state.sid = uuid4().hex[:12]
    return st.session_state.sid


def client_ip() -> str:
    """尽力获取访客 IP（平台不提供时返回空字符串）。"""
    try:
        context = st.context
        headers = getattr(context, "headers", None)
        forwarded = headers.get("X-Forwarded-For") if headers else None
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return str(getattr(context, "ip_address", "") or "")[:64]
    except Exception:
        return ""


def record_usage(
    user_id: int,
    action: str,
    *,
    model: str = "",
    using_own_key: bool = False,
    success: bool = True,
    latency_ms: int = 0,
    answer_chars: int = 0,
    error_type: str = "",
) -> None:
    """写审计记录：只记元数据，绝不记录 API Key，也不记录对话内容。"""

    async def handler(session):
        await UsageService(session).record(
            user_id=user_id,
            action=action,
            model=model,
            using_own_key=using_own_key,
            success=success,
            latency_ms=latency_ms,
            answer_chars=answer_chars,
            error_type=error_type,
            session_id=session_key(),
            ip=client_ip(),
        )

    try:
        db_call(handler)
    except Exception:
        pass  # 审计失败绝不能影响主流程


def api_usage_recent(user_id: int, limit: int = 50) -> list[dict]:
    async def handler(session):
        return await UsageService(session).recent(limit=limit, user_id=user_id)

    return db_call(handler)


def api_usage_summary(user_id: int, hours: int = 24) -> dict:
    async def handler(session):
        return await UsageService(session).summary(hours=hours, user_id=user_id)

    return db_call(handler)


# --------------------------------------------------------------------------- #
# 站长只读数据查看（方案 B）：走 db_call 直接调用 AdminService（显式越权通道）
# 这些助手只读；全部复用「📊 用量统计」页的管理员口令保护。
# --------------------------------------------------------------------------- #
def api_admin_users_overview() -> list[dict]:
    async def handler(session):
        return await AdminService(session).overview()

    return db_call(handler)


def api_admin_characters(user_id: int) -> list[dict]:
    async def handler(session):
        return await AdminService(session).characters(user_id)

    return db_call(handler)


def api_admin_knowledge(user_id: int) -> list[dict]:
    async def handler(session):
        return await AdminService(session).knowledge(user_id)

    return db_call(handler)


def api_admin_document_chunks(user_id: int, document_id: int, limit: int = 50) -> dict:
    async def handler(session):
        return await AdminService(session).document_chunks(user_id, document_id, limit=limit)

    return db_call(handler)


def api_admin_conversations(user_id: int) -> list[dict]:
    async def handler(session):
        return await AdminService(session).conversations(user_id)

    return db_call(handler)


def api_admin_messages(conversation_id: int, limit: int = 200) -> list[dict]:
    async def handler(session):
        return await AdminService(session).conversation_messages(conversation_id, limit=limit)

    return db_call(handler)


def sidebar(user_id: int, username: str) -> str:
    with st.sidebar:
        st.markdown("### 🌍 AI World")
        st.caption("个人/企业级 AI 智能空间")
        items = nav_items()
        # URL 同步：刷新/分享链接能停在当前页；未知参数一律回落到首页（白名单校验）
        requested = str(st.query_params.get("page", "home") or "home")
        default_label = SLUG_PAGE.get(requested, items[0])
        if default_label not in items:
            default_label = items[0]
        page = st.radio(
            "导航",
            items,
            index=items.index(default_label),
            label_visibility="collapsed",
        )
        target_slug = PAGE_SLUG.get(page, "home")
        if str(st.query_params.get("page", "")) != target_slug:
            st.query_params["page"] = target_slug
        st.divider()

        stale = module_staleness()
        if stale:
            st.error("⚠️ 检测到云端模块版本落后（" + "、".join(stale[:3]) + " 等）")
            st.caption(
                "这是 Streamlit「只重跑脚本、不重载模块」导致的："
                "请点右下角 Manage app → Reboot app 重启应用即可，属于正常操作。"
            )

        st.markdown("**运行状态**")
        using_own_key = bool((st.session_state.get("ai_key") or "").strip())
        if using_own_key:
            st.success("在线：正在使用你自己的 API Key 🔑")
        elif settings.ai_configured:
            st.info("在线：使用站点配置的 DeepSeek Key（所有访客共用）")
        else:
            st.caption("离线演示模式：本地占位回答")
        st.write("模型：" + active_model_name())
        st.write("文本向量：" + active_embedding_label())
        st.write(
            "数据库：" + ("PostgreSQL（云端，长期保留）" if not settings.is_sqlite else "SQLite（本地文件）")
        )
        # 配置自检：让"Secret 没生效"这类问题一眼可见，而不是静默回落
        if not settings.is_sqlite:
            pass
        elif (os.environ.get("DATABASE_URL") or "").strip():
            st.error("检测到 DATABASE_URL 但仍在用 SQLite：连接串格式可能不被识别")
        elif SECRETS_REPORT.get("error"):
            st.error("Secrets 读取失败（整个 secrets 都失效了）")
            st.caption(str(SECRETS_REPORT["error"]))
        else:
            loaded = ", ".join(SECRETS_REPORT.get("keys") or [])
            st.caption("已读取的 Secrets：" + (loaded or "（空）"))
            if "DATABASE_URL" not in (SECRETS_REPORT.get("keys") or []):
                st.caption("未发现 DATABASE_URL —— 请确认 Secrets 里是「DATABASE_URL = 连接串」这种键值格式")

        with st.expander("🔧 数据库自检"):
            st.caption(
                "配置排查用：点了会真的去连一次数据库，显示能否连通、有哪些表、数据量；"
                "失败时给出**不涂黑**的真实原因（连接串里的账号密码会自动打码）。"
            )
            if st.button("测试数据库连接", key="db_health_button"):
                health = api_database_health()
                if health.get("ok"):
                    st.success("连接正常 · " + str(health.get("backend")))
                    st.write("表：" + ", ".join(health.get("tables") or []))
                    st.write(
                        "用户数：" + str(health.get("users"))
                        + " · AI 伙伴数：" + str(health.get("characters"))
                    )
                else:
                    st.error("连接失败 · " + str(health.get("backend")))
                    st.code(str(health.get("error", "未知错误")))

        with st.expander("🧠 使用我自己的向量服务（可选，提升检索质量）"):
            st.caption(
                "默认是内置离线哈希向量：本质是**关键词重合度**，换个说法（如「心梗 / 心肌梗死」）就检索不到。"
                "填入任意兼容 OpenAI 的 /embeddings 服务即可获得真正的语义检索。"
                "Key 只保存在本浏览器会话内存：不入库、不写日志、不与其他访客共享。"
            )
            st.text_input(
                "Embedding API Base",
                key="emb_base",
                placeholder="https://api.siliconflow.cn/v1",
            )
            st.text_input("Embedding API Key", type="password", key="emb_key", placeholder="sk-...")
            st.text_input("Embedding 模型", key="emb_model", placeholder="BAAI/bge-m3")
            if session_embedding_config() is not None:
                st.success("已启用你自己的向量服务：" + active_embedding_label())
            st.caption(
                "⚠️ 不同向量模型的空间不可比：切换后请重新上传文件，否则旧文档检索会失效。"
            )

        with st.expander("🌐 联网检索（外部世界接口）"):
            st.caption(
                "开启后，提问会**同时检索外部资料**（作为补充证据），"
                "与知识库片段一起送进模型；两者都会出现在「引用来源」里。"
                "⚠️ 检索在**应用服务器所在网络**发起（部署在美国 → 维基可达；"
                "部署到国内服务器请改用 SearXNG / Tavily / Serper）。"
            )
            st.checkbox("启用联网检索", key="web_enabled")
            if st.session_state.get("web_enabled"):
                label_to_name = search_label_map()
                default_names = default_search_providers()
                defaults = [
                    label for label, name in label_to_name.items() if name in default_names
                ]
                st.multiselect(
                    "检索来源（按顺序优先，并行执行）",
                    list(label_to_name.keys()),
                    default=defaults or list(label_to_name.keys())[:1],
                    key="web_providers",
                )
                st.text_input("API Key（Tavily / Serper 等需要时填）", type="password", key="web_key")
                st.text_input("SearXNG 实例地址（可选）", key="web_base", placeholder="https://searx.example.com")
                st.caption("单个来源超时会自动跳过，不会阻塞回答；失败原因会显示在回答下方。")

        with st.expander("❓ 什么是「文本向量」"):
            st.caption(
                "把文本转成一串数字（向量），检索时用余弦相似度衡量「哪段资料与问题最接近」。"
                "内置离线实现按字/词哈希到 "
                + str(settings.embedding_dim)
                + " 维，只是关键词重合度的近似；接入外部向量服务后才是语义级检索。"
            )
        st.write("账号：" + username)

        with st.expander("🔑 使用我自己的 API Key（可选）", expanded=not using_own_key and not settings.ai_configured):
            st.caption(
                "填入你自己的 Key 后，对话与圆桌会使用你的额度。"
                "Key 只保存在本浏览器会话内存中：不写入数据库、不写日志、不与其他访客共享。"
                "清空即恢复默认。"
            )
            st.text_input("API Key", type="password", key="ai_key", placeholder="sk-...")
            preset = st.selectbox("服务商", list(PROVIDER_PRESETS.keys()), key="ai_provider")
            preset_base, preset_models = PROVIDER_PRESETS[preset]

            # ⚠️ Streamlit 不允许在控件创建之后再写它的 key，
            # 因此所有 session_state 写入都必须发生在 st.text_input 之前。
            if st.session_state.get("_prev_provider") != preset:
                st.session_state["_prev_provider"] = preset
                st.session_state["ai_base"] = preset_base or ""
            if preset_base and not (st.session_state.get("ai_base") or "").strip():
                st.session_state["ai_base"] = preset_base

            st.text_input(
                "API Base（兼容 OpenAI 协议）",
                key="ai_base",
                placeholder=preset_base or "https://你的服务地址/v1",
                help="选择服务商后会自动填入；也可手动改成任何兼容 OpenAI 协议（以 /v1 结尾或 /v4）的地址。",
            )

            model_options = list(preset_models) + ["自定义"]
            if st.session_state.get("ai_model_pick") not in model_options:
                # 换服务商后，原来选的模型不在新列表里 -> 自动切到该服务商默认模型
                st.session_state.ai_model_pick = model_options[0]
            st.selectbox("模型", model_options, key="ai_model_pick")
            if st.session_state.get("ai_model_pick") == "自定义":
                st.text_input("自定义模型名", key="ai_model", placeholder="例如 your-model-name")
            st.caption(
                "deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用，请使用 v4 系列。"
                "带 expires-on 的 4.1 内测 ID 到期即失效，官方不建议硬编码。"
                "也可填任何兼容 OpenAI 协议的服务地址与模型名。"
            )

        st.caption("所有数据按用户隔离；本演示站点使用共享演示账号。")
    return page


def page_dashboard(user_id: int) -> None:
    characters = api_list_characters(user_id)
    bases = api_list_knowledge(user_id)
    total_chunks = sum(int(item["document_count"]) for item in bases)

    st.title("欢迎进入 AI World")
    st.write("创建你的 AI 伙伴、上传知识、发起对话，或让多个 AI 角色围绕一个议题展开圆桌讨论。")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("AI 伙伴", len(characters))
    col2.metric("知识库", len(bases))
    col3.metric("向量切片", total_chunks)
    col4.metric("运行模式", "在线" if settings.ai_configured else "离线")

    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("🤖 我的 AI 伙伴")
        if characters:
            for item in characters[:6]:
                st.write("**" + item["name"] + "** · " + (item["role"] or "AI 伙伴"))
        else:
            st.caption("还没有 AI 伙伴，去「AI伙伴」页面创建。")
    with right:
        st.subheader("📚 我的知识世界")
        if bases:
            for item in bases[:6]:
                st.write("**" + item["name"] + "** · " + str(item["document_count"]) + " 个切片")
        else:
            st.caption("还没有知识库，去「知识世界」上传文件。")


def kb_tree(user_id: int) -> list[dict]:
    """把扁平的知识库列表整理成树形顺序（用于分级展示）。

    返回 [{item, depth}]，按父→子顺序展开；找不到父节点的（父被删）
    会作为顶层兜底，避免"消失不见"。
    """
    bases = api_list_knowledge(user_id)
    by_parent: dict = {}
    for item in bases:
        by_parent.setdefault(item.get("parent_id"), []).append(item)

    ordered: list[dict] = []
    seen: set[int] = set()

    def walk(parent_id, depth: int) -> None:
        for item in by_parent.get(parent_id, []):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            ordered.append({"item": item, "depth": depth})
            walk(item["id"], depth + 1)

    walk(None, 0)
    for item in bases:  # 兜底：父节点缺失的孤儿节点
        if item.get("id", -1) not in seen:
            ordered.append({"item": item, "depth": 0})
            seen.add(item.get("id", -1))
    return ordered


def kb_label_map(user_id: int) -> dict:
    """{id: 带缩进的显示名}（文件夹用 📁，叶子用 📄）。"""
    tree = kb_tree(user_id)
    # 防御式取值：即使云端模块版本落后（未返回 parent_id）也不会 KeyError
    has_child = {node["item"].get("parent_id") for node in tree}
    return {
        node["item"].get("id", -1): "　" * node["depth"]
        + ("📁 " if node["item"].get("id", -1) in has_child else "📄 ")
        + str(node["item"].get("name", "未命名"))
        + "（#" + str(node["item"].get("id", -1)) + "）"
        for node in tree
    }


def kb_choices(user_id: int) -> dict:
    """知识库选择项：{带缩进显示名: id}（分级）。"""
    labels = kb_label_map(user_id)
    return {label: kid for kid, label in labels.items()}


def page_characters(user_id: int) -> None:
    st.title("AI 伙伴")
    st.caption(
        "定义身份、人格、专长与说话方式，并可为每个角色划定**专属知识边界**，"
        "避免不同身份共用同一知识池。"
    )

    characters = api_list_characters(user_id)
    options = kb_choices(user_id)

    with st.expander("➕ 新建 AI 伙伴", expanded=not characters):
        with st.form("create_character", clear_on_submit=True):
            col1, col2 = st.columns(2)
            name = col1.text_input("名称 *", placeholder="例如：张医生")
            role = col2.text_input("身份 / 职业", placeholder="例如：医学专家")
            col3, col4 = st.columns(2)
            personality = col3.text_input("人格特征", placeholder="例如：严谨、耐心")
            style = col4.text_input("说话方式", placeholder="例如：专业、先结论后依据")
            expertise = st.text_area("擅长领域", placeholder="例如：医疗 AI、临床决策支持", height=70)
            system_prompt = st.text_area("补充 System Prompt", placeholder="约束回答边界，例如：不替代执业医师诊断", height=70)
            chosen_kbs = st.multiselect(
                "知识边界（只检索这些知识库；不选 = 检索你的全部知识库）",
                list(options.keys()),
                help="给每个角色划定专属知识范围，避免不同身份互相串味。",
            )
            submitted = st.form_submit_button("创建", type="primary")
        if submitted:
            if not name.strip():
                st.error("名称不能为空")
            else:
                created = api_create_character(
                    user_id,
                    CharacterCreate(
                        name=name.strip(),
                        role=role,
                        personality=personality,
                        expertise=expertise,
                        speaking_style=style,
                        system_prompt=system_prompt,
                    ),
                )
                if chosen_kbs:
                    api_set_character_knowledge(
                        user_id, created["id"], [options[item] for item in chosen_kbs]
                    )
                st.success("已创建：" + name)
                st.rerun()

    st.divider()
    if not characters:
        st.info("还没有 AI 伙伴。")
        return

    bound_map = api_knowledge_map(user_id)
    for item in characters:
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown("#### " + item["name"])
                st.caption(item["role"] or "AI 伙伴")
                st.write("人格：" + (item["personality"] or "—"))
                st.write("擅长：" + (item["expertise"] or "—"))
                st.write("说话方式：" + (item["speaking_style"] or "—"))

                bound = bound_map.get(item["id"], [])
                labels = [label for label, kid in options.items() if kid in bound]
                picked = st.multiselect(
                    "知识边界（只检索这些库）",
                    list(options.keys()),
                    default=labels,
                    key="kb_of_" + str(item["id"]),
                )
                if st.button("保存知识边界", key="save_kb_" + str(item["id"])):
                    api_set_character_knowledge(
                        user_id, item["id"], [options[item2] for item2 in picked]
                    )
                    st.success("已保存知识边界")
                    st.rerun()
                st.caption(
                    "当前：" + ("；".join(labels) if labels else "未绑定 → 检索你的全部知识库")
                )
            with col2:
                if st.button("删除", key="del_char_" + str(item["id"])):
                    api_delete_character(user_id, item["id"])
                    st.rerun()


def page_knowledge(user_id: int) -> None:
    st.title("知识世界")
    st.caption("上传资料后自动解析、切片、向量化，AI 回答时按相似度检索并标注来源。")

    bases = api_list_knowledge(user_id)

    with st.expander("➕ 新建知识库"):
        with st.form("create_kb", clear_on_submit=True):
            kb_name = st.text_input("名称 *", placeholder="例如：医疗 AI 行业资料")
            kb_desc = st.text_area("描述", height=70)
            parent_labels = ["（顶层，作为文件夹或独立库）"] + list(kb_label_map(user_id).values())
            parent_pick = st.selectbox("放在哪里（分级）", parent_labels)
            if st.form_submit_button("创建", type="primary"):
                if not kb_name.strip():
                    st.error("名称不能为空")
                else:
                    label_to_id = {v: k for k, v in kb_label_map(user_id).items()}
                    api_create_knowledge(
                        user_id,
                        kb_name.strip(),
                        kb_desc,
                        label_to_id.get(parent_pick),
                    )
                    st.success("已创建知识库")
                    st.rerun()

    supported = " / ".join(
        item.lstrip(".").upper() for item in settings.allowed_extension_list
    )
    with st.expander("⬆️ 上传文件（" + supported + "）", expanded=True):
        options = ["自动创建新知识库"] + list(kb_label_map(user_id).values())
        target = st.selectbox("上传到", options)
        uploaded = st.file_uploader(
            "选择文件",
            type=[item.lstrip(".") for item in settings.allowed_extension_list],
        )
        if st.button("开始解析并向量化", type="primary", disabled=uploaded is None):
            if uploaded is None:
                st.warning("请先选择文件")
            else:
                knowledge_id = None
                if target != options[0]:
                    knowledge_id = bases[options.index(target) - 1]["id"]
                started = time.perf_counter()
                try:
                    with st.spinner("解析、切片、向量化中..."):
                        result = api_upload_document(
                            user_id,
                            uploaded.name,
                            uploaded.getvalue(),
                            knowledge_id,
                            uploaded.name if knowledge_id is None else None,
                            session_embedding_config(),
                        )
                    record_usage(
                        user_id,
                        "upload",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        answer_chars=int(result["char_count"]),
                    )
                    st.success(
                        "已入库：" + result["filename"] + "，生成 " + str(result["chunk_count"])
                        + " 个切片（" + str(result["char_count"]) + " 字符）"
                    )
                    st.rerun()
                except Exception as error:
                    record_usage(
                        user_id,
                        "upload",
                        success=False,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        error_type=type(error).__name__,
                    )
                    st.error(str(error))

    st.divider()
    if not bases:
        st.info("还没有知识库，先上传一个文件吧。")
        return

    st.subheader("知识库列表（分级）")
    tree = kb_tree(user_id)
    # 防御式取值：模块版本落后（未返回 parent_id）时也不崩
    folder_ids = {node["item"].get("parent_id") for node in tree}
    for node in tree:
        item = node["item"]
        with st.container(border=True):
            col1, col2, col3 = st.columns([4, 1, 1])
            col1.markdown(
                "#### "
                + "　" * node["depth"]
                + ("📁 " if item["id"] in folder_ids else "📄 ")
                + item["name"]
            )
            col1.caption(item["description"] or "暂无描述")
            col2.metric("切片", item["document_count"])
            if col3.button("删除", key="del_kb_" + str(item["id"])):
                api_delete_knowledge(user_id, item["id"])
                st.rerun()
            with st.expander("查看切片内容"):
                documents = api_list_documents(user_id, item["id"], limit=50)
                if not documents:
                    st.caption("没有切片")
                for document in documents:
                    st.caption(document["filename"] + " · #" + str(document["chunk_index"]))
                    st.text(document["content"][:800])


def page_chat(user_id: int) -> None:
    st.title("AI 聊天")
    st.caption("提问后先检索知识库，再把相关片段与问题一起交给模型。")

    characters = api_list_characters(user_id)
    bases = api_list_knowledge(user_id)
    if not characters:
        st.warning("请先在「AI伙伴」页面创建一个 AI 伙伴。")
        return

    name_to_id = {item["name"] + " · " + (item["role"] or "AI 伙伴"): item["id"] for item in characters}
    col1, col2, col3 = st.columns([2, 2, 1])
    chosen_label = col1.selectbox("AI 伙伴", list(name_to_id.keys()))
    character_id = name_to_id[chosen_label]

    kb_options = {"全部知识库": None}
    for item in bases:
        kb_options[item["name"] + "（#" + str(item["id"]) + "）"] = item["id"]
    kb_label = col2.selectbox("检索范围", list(kb_options.keys()))
    use_knowledge = col3.checkbox("启用知识库", value=True)
    multi_agent = st.checkbox(
        "🔬 多 Agent 深度检索（拆解 → 查找 → 审查 → 整理）",
        value=False,
        help=(
            "开启后由 4 个 Agent 协作：把问题拆成子问题、分别检索、"
            "审查证据是否充分（不足会自动补检一轮）、最后综合成稿。"
            "更严谨但更慢、更耗 token；关闭则使用原有单轮 RAG。"
        ),
    )

    state_key = "chat_state_" + str(character_id)
    if state_key not in st.session_state:
        loaded = api_load_conversation(user_id, character_id)
        st.session_state[state_key] = loaded["messages"]
        st.session_state["conv_" + str(character_id)] = loaded["conversation_id"]
    if st.button("🗑 清空当前对话显示"):
        st.session_state[state_key] = []
        st.rerun()

    messages = st.session_state[state_key]
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sources"):
                st.caption("引用来源：" + "；".join(message["sources"]))

    prompt = st.chat_input("输入你的问题，例如：医疗 AI 的技术架构和合规风险是什么？")
    if prompt:
        messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        own_key = session_ai_client() is not None
        with st.chat_message("assistant"):
            with st.spinner("检索知识库并生成回答..."):
                started = time.perf_counter()
                try:
                    result = api_chat(
                        user_id,
                        character_id,
                        prompt,
                        st.session_state.get("conv_" + str(character_id)),
                        kb_options[kb_label] if use_knowledge else None,
                        use_knowledge,
                        session_ai_client(),
                        session_embedding_config(),
                        "multi" if multi_agent else "single",
                        bool(st.session_state.get("web_enabled")),
                        session_search_tool(),
                    )
                except Exception as error:
                    record_usage(
                        user_id,
                        "chat",
                        using_own_key=own_key,
                        success=False,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        error_type=type(error).__name__,
                    )
                    st.error("调用失败：" + str(error))
                    result = None
                else:
                    record_usage(
                        user_id,
                        "chat",
                        model=result.get("model", ""),
                        using_own_key=own_key,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        answer_chars=len(result["answer"] or ""),
                    )
            if result:
                # 角色视角标注：明确是谁、以什么身份/风格在回答
                speaker = next(
                    (item for item in characters if str(item["id"]) == str(character_id)), {}
                )
                viewpoint = (
                    "🎭 由「"
                    + str(speaker.get("name", ""))
                    + "（"
                    + str(speaker.get("role") or "AI 伙伴")
                    + "）」"
                    + (
                        "以「" + str(speaker.get("speaking_style")) + "」的方式"
                        if speaker.get("speaking_style")
                        else ""
                    )
                    + "回答"
                )
                st.caption(viewpoint)
                st.markdown(result["answer"])
                if result.get("agents"):
                    with st.expander(
                        "🔬 多 Agent 协作过程（" + str(result.get("rounds", 1)) + " 轮）",
                        expanded=False,
                    ):
                        for step in result["agents"]:
                            st.markdown("**" + step["agent"] + "** · " + step["role"])
                            st.caption(step["output"][:800])
                        if result.get("sub_questions"):
                            st.caption("拆解出的子问题：" + "；".join(result["sub_questions"]))
                        if result.get("evidence"):
                            st.caption("证据审查结论：" + result["evidence"])
                if result.get("web_reports"):
                    detail = "｜".join(
                        (
                            "✅ " + str(item.get("provider")) + " " + str(item.get("count")) + " 条"
                            if item.get("ok")
                            else "❌ " + str(item.get("provider")) + " " + str(item.get("error"))[:40]
                        )
                        for item in result["web_reports"]
                    )
                    st.caption("🌐 联网检索：" + detail)
                if result["sources"]:
                    st.caption("引用来源：" + "；".join(result["sources"]))
                if result["offline"]:
                    st.caption("离线模式：以上为检索到的原始资料（未经模型加工）")
                st.session_state["conv_" + str(character_id)] = result["conversation_id"]
                messages.append(
                    {
                        "role": "assistant",
                        "content": result["answer"],
                        "sources": result["sources"],
                    }
                )


def page_roundtable(user_id: int) -> None:
    st.title("AI 圆桌")
    st.caption("主持 Agent 拆解议题 → 多个专家 Agent 并行发言 → 主持 Agent 汇总最终方案。")

    agents = api_roundtable_agents(user_id)
    bases = api_list_knowledge(user_id)

    with st.form("roundtable_form"):
        question = st.text_area(
            "讨论议题 *",
            placeholder="例如：如何开发一款医疗 AI 产品？",
            height=100,
        )
        labels = [item["agent"] + " · " + (item["role"] or "AI 专家") for item in agents]
        default_labels = [
            label for label, item in zip(labels, agents) if item.get("source") == "default"
        ]
        chosen = st.multiselect("与会 Agent", labels, default=default_labels)
        col1, col2 = st.columns(2)
        use_knowledge = col1.checkbox("让 Agent 参考知识库", value=False)
        kb_label = col2.selectbox("知识库", ["全部知识库"] + [item["name"] for item in bases])
        submitted = st.form_submit_button("发起圆桌讨论", type="primary")

    if submitted:
        if not question.strip():
            st.error("议题不能为空")
            return
        chosen_agents = []
        for label in chosen:
            index = labels.index(label)
            item = agents[index]
            chosen_agents.append(
                {
                    "agent": item["agent"],
                    "role": item.get("role", ""),
                    "goal": item.get("goal", ""),
                    "personality": item.get("personality", ""),
                }
            )
        knowledge_id = None
        if use_knowledge and kb_label != "全部知识库":
            match = [item for item in bases if item["name"] == kb_label]
            knowledge_id = match[0]["id"] if match else None

        own_key = session_ai_client() is not None
        with st.spinner("主持 Agent 拆解议题，各专家 Agent 并行回复中..."):
            started = time.perf_counter()
            try:
                result = api_run_roundtable(
                    user_id,
                    question.strip(),
                    chosen_agents,
                    use_knowledge,
                    knowledge_id,
                    session_ai_client(),
                    session_embedding_config(),
                )
            except Exception as error:
                record_usage(
                    user_id,
                    "roundtable",
                    using_own_key=own_key,
                    success=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_type=type(error).__name__,
                )
                st.error("讨论失败：" + str(error))
                return
            else:
                record_usage(
                    user_id,
                    "roundtable",
                    model=result.get("model", ""),
                    using_own_key=own_key,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    answer_chars=len(result["summary"] or ""),
                )

        if result["offline"]:
            st.caption("离线模式：以下为检索到的原始资料（未经模型加工）")
        if result["manager_brief"]:
            st.info("**主持人拆解**\n\n" + result["manager_brief"])
        st.divider()
        columns = st.columns(2)
        for index, item in enumerate(result["results"]):
            with columns[index % 2]:
                with st.container(border=True):
                    st.markdown("#### " + item["agent"])
                    st.caption("🎭 视角：" + (item["role"] or "AI 专家") + "（仅被召唤的角色参与发言）")
                    st.markdown(item["answer"])
        if result["summary"]:
            st.divider()
            st.subheader("🧭 " + (result["manager"] or "主持 Agent") + " · 最终汇总")
            if result["sources"]:
                st.caption("参考：" + "、".join(result["sources"]))
            st.markdown(result["summary"])


def admin_kb_tree(bases: list[dict]) -> list[dict]:
    """把 AdminService 返回的扁平知识库列表整理成树形顺序（与 kb_tree 同构）。

    为什么单独写一份：普通页面走的是"当前登录用户"的 api_list_knowledge，
    这里的数据来自站长的越权只读通道，字段来源不同，不能直接复用。
    """
    by_parent: dict = {}
    for item in bases:
        by_parent.setdefault(item.get("parent_id"), []).append(item)

    ordered: list[dict] = []
    seen: set[int] = set()

    def walk(parent_id, depth: int) -> None:
        for item in by_parent.get(parent_id, []):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            ordered.append({"item": item, "depth": depth})
            walk(item["id"], depth + 1)

    walk(None, 0)
    for item in bases:  # 兜底：父节点缺失的孤儿节点，保证不会"消失不见"
        if item.get("id", -1) not in seen:
            ordered.append({"item": item, "depth": 0})
            seen.add(item.get("id", -1))
    return ordered


def page_admin_data() -> None:
    """站长只读数据查看（方案 B）。

    为什么放在「📊 用量统计」页的下半部分：
    复用同一口令体系（admin_password() + st.session_state["admin_ok"]），
    不额外增加导航项，现有 AppTest 的页面列表因此完全不受影响。
    本区块**全部只读**：只提供查询与展示，不提供任何写/删操作。
    """
    st.subheader("🛠 站长数据")
    st.caption("仅站长可见，只读")

    overview = api_admin_users_overview()
    if not overview:
        st.info("暂无用户。")
        return

    st.markdown("**用户总览**")
    st.dataframe(
        [
            {
                "用户": row["username"],
                "角色": row["role"],
                "注册时间": row["created_time"] or "-",
                "AI伙伴数": row["character_count"],
                "知识库数": row["knowledge_count"],
                "文档切片数": row["document_count"],
                "消息数": row["message_count"],
                "最后活跃": row["last_active"] or "从未使用",
            }
            for row in overview
        ]
    )

    labels = {
        row["username"] + "（#" + str(row["user_id"]) + "）": row["user_id"] for row in overview
    }
    picked = st.selectbox("选择用户", list(labels.keys()), key="admin_user_pick")
    target = labels[picked]

    tab_char, tab_kb, tab_chat = st.tabs(["AI 伙伴", "知识库与切片", "对话记录"])

    with tab_char:
        characters = api_admin_characters(target)
        if not characters:
            st.caption("该用户没有 AI 伙伴。")
        for item in characters:
            with st.container(border=True):
                st.markdown("#### " + item["name"])
                st.caption(item["role"] or "AI 伙伴")
                st.write("人格：" + (item["personality"] or "—"))
                st.write("专长：" + (item["expertise"] or "—"))
                st.write("说话方式：" + (item["speaking_style"] or "—"))
                if item["knowledge_ids"]:
                    st.write("知识边界：#" + "、#".join(str(kid) for kid in item["knowledge_ids"]))
                else:
                    st.caption("知识边界：未绑定（检索该用户全部知识库）")

    with tab_kb:
        bases = api_admin_knowledge(target)
        if not bases:
            st.caption("该用户没有知识库。")
        else:
            tree = admin_kb_tree(bases)
            folder_ids = {node["item"].get("parent_id") for node in tree}
            doc_options: dict = {}
            for node in tree:
                item = node["item"]
                icon = "📁" if item["id"] in folder_ids else "📄"
                st.markdown(
                    "#### "
                    + "　" * node["depth"]
                    + icon
                    + " "
                    + item["name"]
                    + "（切片 "
                    + str(item["document_count"])
                    + "）"
                )
                if item["description"]:
                    st.caption(item["description"])
                documents = item.get("documents") or []
                if not documents:
                    st.caption("没有文档。")
                for document in documents:
                    doc_options[
                        "　" * node["depth"]
                        + item["name"]
                        + " / "
                        + document["filename"]
                        + "（"
                        + str(document["chunk_count"])
                        + " 切片）"
                    ] = document["document_id"]
                    st.caption(
                        document["filename"]
                        + " · "
                        + str(document["chunk_count"])
                        + " 切片 · 上传 "
                        + (document["created_time"] or "-")
                    )
            if doc_options:
                st.divider()
                st.markdown("**查看切片正文**")
                chunk_limit = st.slider(
                    "最多显示切片数", min_value=1, max_value=100, value=20, key="admin_chunk_limit"
                )
                chosen = st.selectbox("选择文档", list(doc_options.keys()), key="admin_doc_pick")
                try:
                    result = api_admin_document_chunks(
                        target, doc_options[chosen], limit=int(chunk_limit)
                    )
                except Exception as error:  # noqa: BLE001
                    st.error(redact_credentials(type(error).__name__ + ": " + str(error)))
                else:
                    st.caption(
                        "共 "
                        + str(result["total"])
                        + " 条切片，显示前 "
                        + str(len(result["chunks"]))
                        + " 条。"
                    )
                    for chunk in result["chunks"]:
                        st.caption("#" + str(chunk["chunk_index"]))
                        st.text(chunk["content"][:800])

    with tab_chat:
        conversations = api_admin_conversations(target)
        if not conversations:
            st.caption("该用户没有对话。")
        else:
            st.dataframe(
                [
                    {
                        "对话": row["title"],
                        "AI伙伴": row["character_name"] or "-",
                        "消息数": row["message_count"],
                        "创建时间": row["created_time"] or "-",
                    }
                    for row in conversations
                ]
            )
            conv_labels = {
                "#"
                + str(row["id"])
                + " "
                + row["title"]
                + "（"
                + str(row["message_count"])
                + " 条）": row["id"]
                for row in conversations
            }
            chosen_conv = st.selectbox(
                "选择对话", list(conv_labels.keys()), key="admin_conv_pick"
            )
            for message in api_admin_messages(conv_labels[chosen_conv]):
                role = "🧑 用户" if message["role"] == "user" else "🤖 助手"
                st.markdown("**" + role + "** · " + (message["created_time"] or "-"))
                st.text(message["content"][:2000])


def page_usage(user_id: int) -> None:
    st.title("📊 用量统计")
    st.caption(
        "记录「谁在什么时候用了什么功能」。只记录元数据："
        "**不记录 API Key，也不记录对话内容**。"
    )

    needed = admin_password()
    if not needed:
        st.warning("未配置 APP_ADMIN_PASSWORD（Secrets），该页面不可用。")
        return
    if not st.session_state.get("admin_ok"):
        st.info("该页面仅站点管理员可见。")
        entered = st.text_input("管理口令", type="password")
        if st.button("查看", type="primary"):
            if entered == needed:
                st.session_state.admin_ok = True
                st.rerun()
            else:
                st.error("口令不正确")
        return

    hours = st.selectbox("统计窗口", [24, 72, 168, 720], index=0, format_func=lambda value: str(value) + " 小时")
    summary = api_usage_summary(user_id, hours=int(hours))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("调用次数", summary["total"])
    col2.metric("独立访客（会话）", summary["sessions"])
    col3.metric("自带 Key 次数", summary["own_key"])
    col4.metric("失败次数", summary["failures"])
    st.caption(
        "平均耗时 " + str(summary["avg_latency_ms"]) + " ms · 累计回答 "
        + str(summary["answer_chars"]) + " 字符"
    )
    if summary["by_action"]:
        st.write(
            "按功能：" + "、".join(str(key) + " " + str(value) for key, value in summary["by_action"].items())
        )

    st.divider()
    st.subheader("最近 50 条记录")
    rows = api_usage_recent(user_id, limit=50)
    if not rows:
        st.info("暂无记录。")
    else:
        st.dataframe(rows)
    st.caption(
        "说明：Streamlit Cloud 免费实例的文件系统是临时的，重启/重新部署后本表会清空（演示用途足够）。"
    )

    # 同一口令下的站长只读数据查看（方案 B）
    st.divider()
    page_admin_data()


# --------------------------------------------------------------------------- #
# 5. 主流程
# --------------------------------------------------------------------------- #
def render_startup_failure(exc: Exception) -> None:
    """数据库连不上时给出明确指引（Streamlit 默认会把错误正文涂黑，无法排查）。"""
    st.title("⚠️ 数据库连接失败")
    st.error(redact_credentials(type(exc).__name__ + ": " + str(exc))[:600])
    st.markdown(
        "**常见原因与处理**\n\n"
        "1. **刚在 Neon 重置过密码**：旧密码立即失效。请复制新的连接串，"
        "更新到 Secrets（务必保持「DATABASE_URL = 英文双引号包裹连接串」这种键值格式），"
        "然后点「Reboot app」。\n"
        "2. **Secrets 提示 Invalid format**：TOML 不合法，整份 Secrets 都会读不到 —— "
        "检查是否缺了「DATABASE_URL = 」前缀、引号是否英文半角、是否残留旧行。\n"
        "3. **想先恢复可用**：把 DATABASE_URL 那一行删掉 → Save changes → Reboot app，"
        "应用会回到本地 SQLite（功能照常）。\n"
    )
    st.caption(
        "当前配置的数据库类型："
        + ("PostgreSQL" if not settings.is_sqlite else "SQLite")
        + "｜已读取的 Secrets："
        + (", ".join(SECRETS_REPORT.get("keys") or []) or "（无）")
    )


def main() -> None:
    password_gate()
    try:
        bootstrap()
        user_id = login_gate()
    except Exception as exc:  # noqa: BLE001
        render_startup_failure(exc)
        return
    if user_id is None:
        return

    # 自愈：该账号预置角色缺失时自动补齐
    if api_ensure_default_characters(user_id) > 0:
        st.toast("检测到预置角色缺失，已自动补齐", icon="♻️")

    page = sidebar(user_id, st.session_state.get("uname", "用户"))

    if page == "我的世界":
        page_dashboard(user_id)
    elif page == "AI伙伴":
        page_characters(user_id)
    elif page == "知识世界":
        page_knowledge(user_id)
    elif page == "AI聊天":
        page_chat(user_id)
    elif page == "AI圆桌":
        page_roundtable(user_id)
    elif page == USAGE_PAGE:
        page_usage(user_id)


main()
