"""用量审计服务。

用途：站点管理员能看到"有没有人在用、用了什么、成功率和耗时如何"。
安全红线：**只记录元数据，绝不记录 API Key，也不记录对话内容**。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_log import UsageLog
from app.repositories.usage_repository import UsageRepository

logger = logging.getLogger(__name__)

MAX_ACTION = 32
MAX_MODEL = 64
MAX_SESSION = 32
MAX_IP = 64
MAX_ERROR = 64


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.logs = UsageRepository(session)

    async def record(
        self,
        *,
        user_id: int,
        action: str,
        model: str = "",
        using_own_key: bool = False,
        success: bool = True,
        latency_ms: int = 0,
        answer_chars: int = 0,
        error_type: str = "",
        session_id: str = "",
        ip: str = "",
    ) -> None:
        """写一条审计记录。任何异常都被吞掉，绝不影响主流程。"""
        try:
            await self.logs.create(
                user_id=user_id,
                action=(action or "unknown")[:MAX_ACTION],
                model=(model or "")[:MAX_MODEL],
                using_own_key=bool(using_own_key),
                success=bool(success),
                latency_ms=max(0, int(latency_ms or 0)),
                answer_chars=max(0, int(answer_chars or 0)),
                error_type=(error_type or "")[:MAX_ERROR],
                session_id=(session_id or "")[:MAX_SESSION],
                ip=(ip or "")[:MAX_IP],
            )
            await self.session.commit()
        except Exception:
            logger.exception("usage log write failed")
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def recent(self, limit: int = 50, user_id: int | None = None) -> list[dict]:
        rows: list[UsageLog] = await self.logs.list_recent(limit=limit, user_id=user_id)
        return [
            {
                "时间": row.created_time.strftime("%Y-%m-%d %H:%M:%S") if row.created_time else "",
                "功能": row.action,
                "模型": row.model or "-",
                "自带Key": "是" if row.using_own_key else "否",
                "结果": "成功" if row.success else "失败",
                "耗时(ms)": row.latency_ms,
                "回答字数": row.answer_chars,
                "会话": row.session_id or "-",
                "IP": row.ip or "-",
                "错误": row.error_type or "-",
            }
            for row in rows
        ]

    async def summary(self, hours: int = 24, user_id: int | None = None) -> dict:
        hours = max(1, min(int(hours or 24), 24 * 30))
        since: datetime = datetime.utcnow() - timedelta(hours=hours)
        stats = await self.logs.stats_since(since, user_id)
        stats["hours"] = hours
        return stats
