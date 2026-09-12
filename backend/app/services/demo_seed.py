"""预置公共示例库（幂等种子数据）。

为什么要有它：
- 新用户第一次进来往往没有资料，检索链路"看起来没效果"；公共示例库让任何人都能
  立刻验证「检索是否命中」；
- 文档里写死了若干**唯一、可对照**的事实点（0.18 / RRF k=60 / 2 条切片 …），
  既可以人工提问验证，也被自动化测试当成断言目标。

幂等：按"公共库名字"与"文档文件名"两级判重，连续调用两次不会重复插入。
调用位置：后端启动（app.main.lifespan）与 Streamlit bootstrap（首次访问）。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.files import sanitize_filename
from app.core.security import hash_password
from app.models.user import User
from app.rag.embedding import EmbeddingConfig
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.user_repository import UserRepository
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

PUBLIC_KB_NAME = "🌍 公共示例库（所有人可查）"
PUBLIC_KB_DESCRIPTION = "站长维护的公共示例知识库：所有人可检索，用于演示与验证检索准确性。"
PUBLIC_DOC_FILENAME = "AI 世界 · 使用手册与检索测试题.md"
# 上传链路会做文件名净化（空格 / "·" 等不安全字符 -> "_"），真正入库的是净化后的名字。
# 幂等判重必须用这个名字，否则每次启动都会把同一篇文档再插一遍。
PUBLIC_DOC_STORED_FILENAME = sanitize_filename(PUBLIC_DOC_FILENAME)

# 文档正文：约 700 字，包含 5 个模块、8 条可验证事实、3 道测试题与 1 段干扰内容。
PUBLIC_DOC_CONTENT = """# AI 世界 · 使用手册与检索测试题

> 本文档是**公共示例文档**，用于验证知识库检索的准确性，任何人都可以检索到它。
> 你可以直接提问本文中的任意事实点，检查系统是否正确命中下面的「核心参数」。
> 文档不含任何隐私数据，可放心引用与转述。

## 一、系统概览

AI World 由 5 个模块组成，各自解决一类问题：

1. **我的世界**：首页总览，展示 AI 伙伴、知识库、向量切片与运行模式的实时统计。
2. **AI伙伴**：定义角色的身份、人格、专长与说话方式，并可为角色划定专属知识边界。
3. **知识世界**：上传资料后自动解析、切片、向量化，检索时按相似度召回并标注来源。
4. **AI聊天**：先检索知识库（可选联网检索），再把相关片段与问题一起交给模型作答。
5. **AI圆桌**：主持 Agent 拆解议题，多个专家 Agent 并行发言，最后由主持人汇总成稿。

## 二、核心参数（可验证事实点）

下面每一条都是唯一、可对照的事实，适合用来测试检索是否命中：

- 检索最低相关性阈值 = 0.18（低于该分数的向量切片不会进入上下文）
- 支持的文件格式数量 = 40 种
- 检索方式 = BM25 稀疏检索 + 向量语义检索，用 RRF 融合（k=60）
- 同一文档在结果中最多保留 2 条切片（保证多篇文档都能进入上下文）
- 后端自动化测试套件数量 = 16 个
- 长记忆三件套 = 滚动摘要 / 历史向量检索 / 自动事实抽取
- 联网检索支持 5 个来源：维基百科、DuckDuckGo、SearXNG、Tavily、Serper
- 元信息类问题（作者 / 页数 / 时间）会跳过联网检索，只在知识库内求证

## 三、3 道检索测试题（含标准答案）

**问题 1：检索最低相关性阈值是多少？**
答案：0.18。

**问题 2：两路检索结果用什么算法融合？**
答案：RRF（Reciprocal Rank Fusion，k=60）。

**问题 3：同一文档最多保留几条切片？**
答案：2 条。

## 四、干扰段落（用于验证阈值与 Rerank）

这一段与上面的任何事实都无关，用来验证检索阈值与精排能否把它挡在上下文之外：
据某地气象台发布，未来三天以多云为主，午后有分散性阵雨，气温 18 至 26 摄氏度；
某公司第二季度财报显示营业收入同比增长 7.3%，主要来自订阅制业务，管理层预计
下半年毛利率保持稳定。天气与财报信息都与 AI 世界系统参数无关，若它出现在回答
依据里，说明检索相关性控制需要复核。
"""


async def _resolve_owner(session: AsyncSession) -> User:
    """确定公共库的归属账号：站长账号 > demo 账号 > 第一个用户 > 新建 demo。

    为什么按这个顺序：公共库归属站长最合理（站长才有彻底删除权限），
    但全新库 / 未配置 ADMIN_USERNAMES 时必须能稳妥兜底，绝不能让种子数据失败。
    """
    users = UserRepository(session)
    for name in settings.admin_username_list:
        user = await users.get_by_username(name)
        if user is not None:
            return user
    user = await users.get_by_username(settings.demo_username)
    if user is not None:
        return user
    existing = list(await users.list_all())
    if existing:
        return existing[0]
    created = await users.create(
        username=settings.demo_username,
        password_hash=hash_password(settings.demo_password),
        email="demo@ai-world.local",
        role="admin" if settings.demo_username in settings.admin_username_list else "user",
    )
    await session.commit()
    return created


async def ensure_public_demo(
    session: AsyncSession, embedding_config: EmbeddingConfig | None = None
) -> dict:
    """幂等创建「公共示例库 + 使用手册」。已存在则跳过，绝不重复插入。"""
    knowledge = KnowledgeRepository(session)
    documents = DocumentRepository(session)

    base = await knowledge.get_public_by_name(PUBLIC_KB_NAME)
    created_knowledge = False
    if base is None:
        owner = await _resolve_owner(session)
        base = await knowledge.create(
            user_id=owner.id,
            name=PUBLIC_KB_NAME,
            description=PUBLIC_KB_DESCRIPTION,
            is_public=True,
        )
        await session.commit()
        created_knowledge = True
        logger.info("公共示例库已创建: #%s (owner=%s)", base.id, owner.id)
    else:
        owner = await UserRepository(session).get(base.user_id)
        if owner is None:
            owner = await _resolve_owner(session)

    # 文档幂等：同名文档存在（含已删进回收站的）就跳过，避免重复入库
    if await documents.exists_filename(base.id, PUBLIC_DOC_STORED_FILENAME):
        return {
            "knowledge_id": base.id,
            "owner_id": owner.id,
            "created_knowledge": created_knowledge,
            "created_document": False,
        }

    # 知识库已在回收站：不在这里"复活"它（管理员应从回收站恢复），直接跳过文档
    if base.deleted_at is not None:
        return {
            "knowledge_id": base.id,
            "owner_id": owner.id,
            "created_knowledge": created_knowledge,
            "created_document": False,
        }

    await KnowledgeService(session, embedding_config=embedding_config).upload(
        owner.id,
        filename=PUBLIC_DOC_FILENAME,
        data=PUBLIC_DOC_CONTENT.encode("utf-8"),
        knowledge_id=base.id,
    )
    logger.info("公共示例文档已入库: %s (knowledge=%s)", PUBLIC_DOC_FILENAME, base.id)
    return {
        "knowledge_id": base.id,
        "owner_id": owner.id,
        "created_knowledge": created_knowledge,
        "created_document": True,
    }
