"""管理员接口（文档 Phase 2「权限」要求）。

访问条件：users.role == "admin"，或用户名出现在环境变量 ADMIN_USERNAMES 中。
默认没有任何管理员（ADMIN_USERNAMES 为空），需要你显式配置。
"""

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.repositories.user_repository import UserRepository
from app.services.usage_service import UsageService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usage", summary="全站用量统计（管理员）")
async def usage_summary(
    session: SessionDep,
    admin: AdminUser,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict:
    stats = await UsageService(session).summary(hours=hours, user_id=None)
    stats["admin"] = admin.username
    return stats


@router.get("/usage/recent", summary="最近调用明细（管理员）")
async def usage_recent(
    session: SessionDep,
    admin: AdminUser,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    return await UsageService(session).recent(limit=limit, user_id=None)


@router.get("/users", summary="用户列表（管理员）")
async def list_users(session: SessionDep, admin: AdminUser) -> list[dict]:
    rows = await UserRepository(session).list_all()
    return [
        {
            "id": item.id,
            "username": item.username,
            "email": item.email,
            "role": item.role,
            "created_time": item.created_time.isoformat() if item.created_time else None,
        }
        for item in rows
    ]
