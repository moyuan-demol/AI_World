from datetime import datetime

from sqlalchemy import func, select

from app.models.usage_log import UsageLog
from app.repositories.base import BaseRepository


class UsageRepository(BaseRepository[UsageLog]):
    model = UsageLog

    def _filters(self, since: datetime, user_id: int | None) -> list:
        filters = [UsageLog.created_time >= since]
        if user_id is not None:
            filters.append(UsageLog.user_id == user_id)
        return filters

    async def list_recent(self, limit: int = 50, user_id: int | None = None) -> list[UsageLog]:
        statement = select(UsageLog).order_by(UsageLog.id.desc()).limit(max(1, min(limit, 500)))
        if user_id is not None:
            statement = (
                select(UsageLog)
                .where(UsageLog.user_id == user_id)
                .order_by(UsageLog.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def stats_since(self, since: datetime, user_id: int | None = None) -> dict:
        filters = self._filters(since, user_id)

        async def scalar(statement):
            result = await self.session.execute(statement)
            return result.scalar()

        total = await scalar(select(func.count(UsageLog.id)).where(*filters))
        failures = await scalar(
            select(func.count(UsageLog.id)).where(*filters, UsageLog.success.is_(False))
        )
        own_key = await scalar(
            select(func.count(UsageLog.id)).where(*filters, UsageLog.using_own_key.is_(True))
        )
        sessions = await scalar(
            select(func.count(func.distinct(UsageLog.session_id))).where(*filters)
        )
        avg_latency = await scalar(select(func.avg(UsageLog.latency_ms)).where(*filters))
        answer_chars = await scalar(select(func.sum(UsageLog.answer_chars)).where(*filters))

        grouped = await self.session.execute(
            select(UsageLog.action, func.count(UsageLog.id)).where(*filters).group_by(UsageLog.action)
        )
        by_action = {str(action): int(count) for action, count in grouped.all()}

        return {
            "total": int(total or 0),
            "failures": int(failures or 0),
            "own_key": int(own_key or 0),
            "sessions": int(sessions or 0),
            "avg_latency_ms": int(avg_latency or 0),
            "answer_chars": int(answer_chars or 0),
            "by_action": by_action,
        }
