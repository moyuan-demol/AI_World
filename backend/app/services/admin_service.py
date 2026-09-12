"""站长（管理员）只读数据查看服务。

为什么单独建这个 Service：
- 普通业务链路（CharacterService / KnowledgeService / ChatService）**强制按
  user_id 过滤**，这是多用户数据隔离的红线，绝不能为了"站长看全站"而在普通
  Repository 上开一个"无过滤"的口子 —— 那等于把越权能力暴露给所有调用方。
- 因此把"管理员显式越权读取"收敛到唯一一个模块：AdminService。
  它只被 API 层的 AdminUser 依赖（app/api/routes/admin.py）与 Streamlit 的
  站长口令页面调用，且**对外一律只读，不提供任何写/删方法**。

安全边界（重要）：
- 本模块的查询**故意不按 user_id 过滤**，因为调用方在调用前已被证明是管理员。
- 请勿在普通用户路径中导入或调用本模块；普通路径必须继续走带 user_id 过滤的
  Service/Repository。
- 所有方法都接受 user_id 参数，该参数只用于"要查看哪个用户的数据"，
  绝不作为隔离条件之外的授权手段。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.character import Character
from app.models.character_knowledge import CharacterKnowledge
from app.models.chat import Conversation, Message
from app.models.document import Document
from app.models.knowledge import KnowledgeBase
from app.models.usage_log import UsageLog
from app.models.user import User


def _iso(value: datetime | None) -> str | None:
    """统一把 datetime 转成 ISO 字符串，方便直接塞进 JSON / dataframe。"""
    return value.isoformat() if value else None


class AdminService:
    """管理员显式越权只读通道（仅供站长使用，见模块 docstring）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # 用户总览
    # ------------------------------------------------------------------ #
    async def overview(self) -> list[dict]:
        """用户总览：每个用户的伙伴/知识库/切片/消息计数与最后活跃时间。

        为什么用关联子查询而不是"N 个用户跑 N 次查询"：
        每个计数都是一条相关子查询，整个总览只有**一条 SQL**，
        用户再多也不会出现 N+1（这是站点后台最容易被拖垮的地方）。
        """
        character_count = (
            select(func.count(Character.id))
            .where(Character.user_id == User.id)
            .scalar_subquery()
        )
        knowledge_count = (
            select(func.count(KnowledgeBase.id))
            .where(KnowledgeBase.user_id == User.id, KnowledgeBase.deleted_at.is_(None))
            .scalar_subquery()
        )
        # 回收站里的内容不算"有效数据"：站长总览与用户自己看到的列表必须一致
        document_count = (
            select(func.count(Document.id))
            .where(Document.user_id == User.id, Document.deleted_at.is_(None))
            .scalar_subquery()
        )
        message_count = (
            select(func.count(Message.id))
            .where(Message.user_id == User.id)
            .scalar_subquery()
        )
        # 最后活跃时间：取该用户 usage_logs 里最大的一条 created_time
        last_active = (
            select(func.max(UsageLog.created_time))
            .where(UsageLog.user_id == User.id)
            .scalar_subquery()
        )

        statement = (
            select(
                User.id,
                User.username,
                User.role,
                User.created_time,
                character_count.label("character_count"),
                knowledge_count.label("knowledge_count"),
                document_count.label("document_count"),
                message_count.label("message_count"),
                last_active.label("last_active"),
            )
            .order_by(User.id.asc())
        )
        result = await self.session.execute(statement)
        return [
            {
                "user_id": int(row["id"]),
                "username": row["username"],
                "role": row["role"],
                "created_time": _iso(row["created_time"]),
                "character_count": int(row["character_count"] or 0),
                "knowledge_count": int(row["knowledge_count"] or 0),
                "document_count": int(row["document_count"] or 0),
                "message_count": int(row["message_count"] or 0),
                "last_active": _iso(row["last_active"]),
            }
            for row in result.mappings().all()
        ]

    # ------------------------------------------------------------------ #
    # 指定用户的 AI 伙伴
    # ------------------------------------------------------------------ #
    async def characters(self, user_id: int) -> list[dict]:
        """查看指定用户的 AI 伙伴（含知识边界）。

        用户不存在时自然返回空列表，不抛异常（列表类查询的约定）。
        """
        rows = (
            (
                await self.session.execute(
                    select(Character)
                    .where(Character.user_id == user_id)
                    .order_by(Character.id.asc())
                )
            )
            .scalars()
            .all()
        )
        character_ids = [item.id for item in rows]
        # 知识边界一次查回（避免每个角色查一次 -> N+1）
        bound: dict[int, list[int]] = {}
        if character_ids:
            binding_rows = await self.session.execute(
                select(CharacterKnowledge.character_id, CharacterKnowledge.knowledge_id).where(
                    CharacterKnowledge.character_id.in_(character_ids)
                )
            )
            for character_id, knowledge_id in binding_rows.all():
                bound.setdefault(int(character_id), []).append(int(knowledge_id))
        return [
            {
                "id": item.id,
                "name": item.name,
                "role": item.role,
                "personality": item.personality,
                "expertise": item.expertise,
                "speaking_style": item.speaking_style,
                "system_prompt": item.system_prompt,
                "knowledge_ids": bound.get(item.id, []),
                "created_time": _iso(item.created_time),
            }
            for item in rows
        ]

    # ------------------------------------------------------------------ #
    # 指定用户的知识库树 + 文档
    # ------------------------------------------------------------------ #
    async def knowledge(self, user_id: int) -> list[dict]:
        """知识库树（含 parent_id 层级）+ 每个库的文档列表（文件名/切片数/上传时间）。

        用户不存在时返回空列表。所有聚合都是一次查询，避免 N+1。
        """
        bases = (
            (
                await self.session.execute(
                    select(KnowledgeBase)
                    .where(
                        KnowledgeBase.user_id == user_id,
                        KnowledgeBase.deleted_at.is_(None),
                    )
                    .order_by(KnowledgeBase.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if not bases:
            return []
        knowledge_ids = [base.id for base in bases]

        # 每个知识库的切片总数（一次聚合）
        count_rows = await self.session.execute(
            select(Document.knowledge_id, func.count(Document.id))
            .where(
                Document.knowledge_id.in_(knowledge_ids),
                Document.deleted_at.is_(None),
            )
            .group_by(Document.knowledge_id)
        )
        counts = {int(knowledge_id): int(count) for knowledge_id, count in count_rows.all()}

        # 文档元信息：同一文件的切片聚合成一条，并带出首块 id（供查看切片正文）
        document_rows = await self.session.execute(
            select(
                Document.knowledge_id,
                Document.filename,
                func.count(Document.id).label("chunk_count"),
                func.min(Document.id).label("document_id"),
                func.min(Document.created_time).label("created_time"),
            )
            .where(
                Document.knowledge_id.in_(knowledge_ids),
                Document.deleted_at.is_(None),
            )
            .group_by(Document.knowledge_id, Document.filename)
            .order_by(Document.knowledge_id.asc(), func.min(Document.id).asc())
        )
        documents: dict[int, list[dict]] = {}
        for row in document_rows.mappings().all():
            documents.setdefault(int(row["knowledge_id"]), []).append(
                {
                    "document_id": int(row["document_id"]),
                    "filename": row["filename"],
                    "chunk_count": int(row["chunk_count"]),
                    "created_time": _iso(row["created_time"]),
                }
            )

        return [
            {
                "id": base.id,
                "name": base.name,
                "description": base.description,
                "parent_id": base.parent_id,
                "created_time": _iso(base.created_time),
                "document_count": counts.get(base.id, 0),
                "documents": documents.get(base.id, []),
            }
            for base in bases
        ]

    # ------------------------------------------------------------------ #
    # 指定文档的切片正文
    # ------------------------------------------------------------------ #
    async def document_chunks(
        self, user_id: int, document_id: int, *, limit: int = 50, offset: int = 0
    ) -> dict:
        """查看某篇文档的切片正文（分页，默认最多 limit 条）。

        这里的 document_id 是列表里给出的"首块 id"：切片正文按
        (知识库, 文件名) 聚合返回，同一文件的全部切片按 chunk_index 顺序排列。
        文档不存在或不属于该 user_id 时抛明确错误（而非未处理异常）。
        """
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))
        anchor = await self.session.get(Document, document_id)
        if anchor is None or anchor.user_id != user_id or anchor.deleted_at is not None:
            raise NotFoundError("文档不存在或不属于该用户")

        filters = (
            Document.user_id == user_id,
            Document.knowledge_id == anchor.knowledge_id,
            Document.filename == anchor.filename,
            Document.deleted_at.is_(None),
        )
        total = await self.session.scalar(select(func.count(Document.id)).where(*filters))
        rows = (
            (
                await self.session.execute(
                    select(Document)
                    .where(*filters)
                    .order_by(Document.chunk_index.asc(), Document.id.asc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {
            "document_id": document_id,
            "knowledge_id": anchor.knowledge_id,
            "filename": anchor.filename,
            "total": int(total or 0),
            "offset": offset,
            "limit": limit,
            "chunks": [
                {
                    "id": item.id,
                    "chunk_index": item.chunk_index,
                    "content": item.content,
                    "created_time": _iso(item.created_time),
                }
                for item in rows
            ],
        }

    # ------------------------------------------------------------------ #
    # 指定用户的对话列表
    # ------------------------------------------------------------------ #
    async def conversations(self, user_id: int) -> list[dict]:
        """查看指定用户的对话列表（含伙伴名与消息数），用户不存在时返回空列表。"""
        message_count = (
            select(func.count(Message.id))
            .where(Message.conversation_id == Conversation.id)
            .scalar_subquery()
        )
        statement = (
            select(
                Conversation.id,
                Conversation.title,
                Conversation.character_id,
                Character.name.label("character_name"),
                Conversation.created_time,
                message_count.label("message_count"),
            )
            .join(Character, Character.id == Conversation.character_id, isouter=True)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.id.desc())
        )
        result = await self.session.execute(statement)
        return [
            {
                "id": int(row["id"]),
                "title": row["title"],
                "character_id": int(row["character_id"]),
                "character_name": row["character_name"] or "",
                "message_count": int(row["message_count"] or 0),
                "created_time": _iso(row["created_time"]),
            }
            for row in result.mappings().all()
        ]

    # ------------------------------------------------------------------ #
    # 指定对话的消息记录
    # ------------------------------------------------------------------ #
    async def conversation_messages(self, conversation_id: int, *, limit: int = 200) -> list[dict]:
        """查看指定对话的消息记录（角色/内容/时间）；对话不存在时返回空列表。"""
        limit = max(1, min(int(limit or 200), 1000))
        rows = (
            (
                await self.session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": item.id,
                "conversation_id": item.conversation_id,
                "role": item.role,
                "content": item.content,
                "created_time": _iso(item.created_time),
            }
            for item in rows
        ]
