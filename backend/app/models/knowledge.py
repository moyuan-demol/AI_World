from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class KnowledgeBase(Base):
    """A user owned knowledge world (a container of documents)."""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 分级：父节点（为空表示顶层）。"文件夹"就是有子节点的知识库
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # 公共知识库：所有人可检索（user_id 归属站长账号）。
    # 为什么用一列而不是"约定某个 user_id 是公共"：检索范围与权限判定都要读它，
    # 约定式实现一旦账号变更就会静默失效。
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false(), index=True
    )
    # 回收站（软删除）：非空 = 已删除。所有列表 / 检索 / 计数都必须排除它，
    # 否则等于"没删"（内容仍会被检索出来）。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
