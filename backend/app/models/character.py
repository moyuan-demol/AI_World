from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Character(Base):
    """An AI companion created by a user."""

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expertise: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speaking_style: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
