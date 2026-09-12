from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class CharacterKnowledge(Base):
    """角色 ↔ 知识库 绑定（多对多）。

    目的：让每个 AI 伙伴拥有**自己的知识边界** —— 医生角色只查医疗库、
    商业顾问只查市场库，避免所有角色共用一个大池子导致身份混淆。
    """

    __tablename__ = "character_knowledge"
    __table_args__ = (
        UniqueConstraint("character_id", "knowledge_id", name="uq_character_knowledge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    knowledge_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
