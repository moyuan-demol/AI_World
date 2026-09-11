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
import sys
import tempfile
import threading
from pathlib import Path

import streamlit as st

# --------------------------------------------------------------------------- #
# 1. 运行环境准备（必须早于 import app.*）
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_streamlit_secrets_into_env() -> None:
    """把 Streamlit secrets 注入环境变量，后端 Settings 会自动读取。"""
    try:
        secrets = dict(st.secrets)
    except Exception:
        return
    for key, value in secrets.items():
        name = str(key).upper()
        if name.isupper() and isinstance(value, (str, int, float)):
            os.environ.setdefault(name, str(value))


def _pick_writable_data_dir() -> Path:
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


_load_streamlit_secrets_into_env()
DATA_DIR = _pick_writable_data_dir()
os.environ.setdefault("DATA_DIR", str(DATA_DIR))
os.environ.setdefault("UPLOAD_DIR", str(DATA_DIR / "uploads"))

from app.config.settings import settings  # noqa: E402
from app.core.files import sanitize_filename, validate_signature  # noqa: E402
from app.database.init_db import init_db  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.schemas.character import CharacterCreate  # noqa: E402
from app.schemas.knowledge import KnowledgeCreate  # noqa: E402
from app.schemas.roundtable import AgentSpec, RoundtableRequest  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402
from app.services.roundtable_service import RoundtableService  # noqa: E402

# --------------------------------------------------------------------------- #
# 2. 异步桥：Streamlit 同步，后端 async；用常驻事件循环线程，避免跨循环报错
# --------------------------------------------------------------------------- #
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
        return _loop


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result()


def db_call(handler):
    async def _wrapper():
        async with SessionLocal() as session:
            return await handler(session)

    return run_async(_wrapper())


@st.cache_resource(show_spinner="正在初始化 AI World ...")
def bootstrap() -> dict:
    """建表 + 准备演示账号 + 预置 3 个 AI 伙伴（幂等）。"""
    run_async(init_db())

    async def handler(session):
        repo = UserRepository(session)
        user = await repo.get_by_username(settings.demo_username)
        if user is None:
            token, _created = await AuthService(session).demo_login()
            user = await repo.get(token.user.id)
        await CharacterService(session).ensure_defaults(user.id)
        return {"user_id": user.id, "username": user.username}

    return db_call(handler)


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


def api_list_characters(user_id: int) -> list[dict]:
    async def handler(session):
        rows = await CharacterService(session).list(user_id)
        return [_character_dict(row) for row in rows]

    return db_call(handler)


def api_create_character(user_id: int, payload: CharacterCreate) -> dict:
    async def handler(session):
        row = await CharacterService(session).create(user_id, payload)
        return _character_dict(row)

    return db_call(handler)


def api_delete_character(user_id: int, character_id: int) -> None:
    async def handler(session):
        await CharacterService(session).delete(user_id, character_id)

    db_call(handler)


def api_ensure_default_characters(user_id: int) -> int:
    """自愈：公网演示站可能被访客把 AI 伙伴全删光。

    每次运行都检查一次，若一个都不剩就重新生成预置角色，
    保证任何人任何时候点开都是完整体验。已存在时开销仅一次 COUNT 查询。
    """
    async def handler(session):
        return await CharacterService(session).ensure_defaults(user_id)

    return db_call(handler)


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


def api_create_knowledge(user_id: int, name: str, description: str) -> dict:
    async def handler(session):
        row = await KnowledgeService(session).create(
            user_id, KnowledgeCreate(name=name, description=description)
        )
        return {"id": row.id, "name": row.name, "document_count": row.document_count}

    return db_call(handler)


def api_delete_knowledge(user_id: int, knowledge_id: int) -> None:
    async def handler(session):
        await KnowledgeService(session).delete(user_id, knowledge_id)

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


def api_upload_document(user_id: int, filename: str, data: bytes, knowledge_id, name) -> dict:
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
        result = await KnowledgeService(session).upload(
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
) -> dict:
    async def handler(session):
        payload = ChatRequest(
            character_id=character_id,
            message=message,
            conversation_id=conversation_id,
            knowledge_id=knowledge_id,
            use_knowledge=use_knowledge,
        )
        result = await ChatService(session).chat(user_id, payload)
        return {
            "answer": result.answer,
            "conversation_id": result.conversation_id,
            "offline": result.offline,
            "model": result.model,
            "sources": [item.filename + " (" + str(item.score) + ")" for item in result.sources],
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


def api_run_roundtable(user_id: int, question: str, agents: list[dict], use_knowledge: bool, knowledge_id) -> dict:
    async def handler(session):
        payload = RoundtableRequest(
            question=question,
            agents=[AgentSpec(**agent) for agent in agents] if agents else None,
            knowledge_id=knowledge_id,
            use_knowledge=use_knowledge,
            include_manager=True,
        )
        result = await RoundtableService(session).run(user_id, payload)
        return {
            "manager_brief": result.manager_brief,
            "summary": result.summary,
            "manager": result.manager,
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


def sidebar(user_id: int, username: str) -> str:
    with st.sidebar:
        st.markdown("### 🌍 AI World")
        st.caption("个人/企业级 AI 智能空间")
        page = st.radio("导航", MODULES, label_visibility="collapsed")
        st.divider()
        st.markdown("**运行状态**")
        st.write("模型：" + (settings.deepseek_model if settings.ai_configured else "离线演示模式"))
        st.write("向量：" + settings.embedding_provider + " / " + str(settings.embedding_dim))
        st.write("账号：" + username)
        if not settings.ai_configured:
            st.info("未配置 DEEPSEEK_API_KEY，聊天与圆桌返回本地占位回答。")
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


def page_characters(user_id: int) -> None:
    st.title("AI 伙伴")
    st.caption("定义身份、人格、专长与说话方式，作为对话和圆桌的 System Prompt。")

    characters = api_list_characters(user_id)

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
            submitted = st.form_submit_button("创建", type="primary")
        if submitted:
            if not name.strip():
                st.error("名称不能为空")
            else:
                api_create_character(
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
                st.success("已创建：" + name)
                st.rerun()

    st.divider()
    if not characters:
        st.info("还没有 AI 伙伴。")
        return

    for item in characters:
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown("#### " + item["name"])
                st.caption(item["role"] or "AI 伙伴")
                st.write("人格：" + (item["personality"] or "—"))
                st.write("擅长：" + (item["expertise"] or "—"))
                st.write("说话方式：" + (item["speaking_style"] or "—"))
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
            if st.form_submit_button("创建", type="primary"):
                if not kb_name.strip():
                    st.error("名称不能为空")
                else:
                    api_create_knowledge(user_id, kb_name.strip(), kb_desc)
                    st.success("已创建知识库")
                    st.rerun()

    with st.expander("⬆️ 上传文件（PDF / DOCX / TXT / MD）", expanded=True):
        options = ["自动创建新知识库"] + [item["name"] + "（#" + str(item["id"]) + "）" for item in bases]
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
                try:
                    with st.spinner("解析、切片、向量化中..."):
                        result = api_upload_document(
                            user_id,
                            uploaded.name,
                            uploaded.getvalue(),
                            knowledge_id,
                            uploaded.name if knowledge_id is None else None,
                        )
                    st.success(
                        "已入库：" + result["filename"] + "，生成 " + str(result["chunk_count"])
                        + " 个切片（" + str(result["char_count"]) + " 字符）"
                    )
                    st.rerun()
                except Exception as error:
                    st.error(str(error))

    st.divider()
    if not bases:
        st.info("还没有知识库，先上传一个文件吧。")
        return

    st.subheader("知识库列表")
    for item in bases:
        with st.container(border=True):
            col1, col2, col3 = st.columns([4, 1, 1])
            col1.markdown("#### " + item["name"])
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

    state_key = "chat_state_" + str(character_id)
    if state_key not in st.session_state:
        loaded = api_load_conversation(user_id, character_id)
        st.session_state[state_key] = loaded["messages"]
        st.session_state["conv_" + str(character_id)] = loaded["conversation_id"]
    if st.button("🗑 清空当前对话显示（服务端记录保留）"):
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
        with st.chat_message("assistant"):
            with st.spinner("检索知识库并生成回答..."):
                try:
                    result = api_chat(
                        user_id,
                        character_id,
                        prompt,
                        st.session_state.get("conv_" + str(character_id)),
                        kb_options[kb_label] if use_knowledge else None,
                        use_knowledge,
                    )
                except Exception as error:
                    st.error("调用失败：" + str(error))
                    result = None
            if result:
                st.markdown(result["answer"])
                if result["sources"]:
                    st.caption("引用来源：" + "；".join(result["sources"]))
                if result["offline"]:
                    st.warning("离线演示模式：未配置 DEEPSEEK_API_KEY，以上为本地占位回答。")
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

        with st.spinner("主持 Agent 拆解议题，各专家 Agent 并行回复中..."):
            try:
                result = api_run_roundtable(
                    user_id, question.strip(), chosen_agents, use_knowledge, knowledge_id
                )
            except Exception as error:
                st.error("讨论失败：" + str(error))
                return

        if result["offline"]:
            st.warning("离线演示模式：未配置 DEEPSEEK_API_KEY，以下为本地占位内容。")
        if result["manager_brief"]:
            st.info("**主持人拆解**\n\n" + result["manager_brief"])
        st.divider()
        columns = st.columns(2)
        for index, item in enumerate(result["results"]):
            with columns[index % 2]:
                with st.container(border=True):
                    st.markdown("#### " + item["agent"])
                    st.caption(item["role"])
                    st.markdown(item["answer"])
        if result["summary"]:
            st.divider()
            st.subheader("🧭 " + (result["manager"] or "主持 Agent") + " · 最终汇总")
            if result["sources"]:
                st.caption("参考：" + "、".join(result["sources"]))
            st.markdown(result["summary"])


# --------------------------------------------------------------------------- #
# 5. 主流程
# --------------------------------------------------------------------------- #
def main() -> None:
    password_gate()
    context = bootstrap()
    user_id = context["user_id"]

    # 自愈：演示数据被删空时自动恢复预置角色
    if api_ensure_default_characters(user_id) > 0:
        st.toast("检测到演示数据被清空，已自动恢复初始角色", icon="♻️")

    page = sidebar(user_id, context["username"])

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


main()
