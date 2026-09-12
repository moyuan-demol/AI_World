from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UsageLog(Base):
    """一次操作的用量记录（审计用）。

    安全红线：本表**绝不记录 API Key**，也**不记录对话内容**
    （对话内容本身已存在于 conversations / messages 表）。
    只记录：谁（匿名会话标识 + IP）、什么时候、用了哪个功能、
    用了哪个模型、是否自带 Key、成功与否、耗时、回答长度。
    """

    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    using_own_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answer_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
