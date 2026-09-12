"""管理员接口（文档 Phase 2「权限」要求）。

访问条件：users.role == "admin"，或用户名出现在环境变量 ADMIN_USERNAMES 中。
默认没有任何管理员（ADMIN_USERNAMES 为空），需要你显式配置。
"""

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.repositories.user_repository import UserRepository
from app.services.admin_service import AdminService
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


# --------------------------------------------------------------------------- #
# 站长只读数据查看（方案 B）
#
# 说明：以下接口全部**只读**，不提供任何写/删能力；鉴权由 AdminUser 依赖统一
# 保证（role == admin 或用户名在 ADMIN_USERNAMES 中），无需再额外加鉴权。
# 越权读取逻辑收敛在 AdminService 内部，普通路径仍强制按 user_id 过滤。
# --------------------------------------------------------------------------- #
@router.get("/users/overview", summary="用户总览（站长只读）")
async def users_overview(session: SessionDep, admin: AdminUser) -> list[dict]:
    return await AdminService(session).overview()


@router.get("/users/{user_id}/characters", summary="指定用户的 AI 伙伴（站长只读）")
async def user_characters(user_id: int, session: SessionDep, admin: AdminUser) -> list[dict]:
    return await AdminService(session).characters(user_id)


@router.get("/users/{user_id}/knowledge", summary="指定用户的知识库树与文档（站长只读）")
async def user_knowledge(user_id: int, session: SessionDep, admin: AdminUser) -> list[dict]:
    return await AdminService(session).knowledge(user_id)


@router.get(
    "/users/{user_id}/documents/{document_id}/chunks",
    summary="指定文档的切片正文（站长只读）",
)
async def user_document_chunks(
    user_id: int,
    document_id: int,
    session: SessionDep,
    admin: AdminUser,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return await AdminService(session).document_chunks(
        user_id, document_id, limit=limit, offset=offset
    )


@router.get("/users/{user_id}/conversations", summary="指定用户的对话列表（站长只读）")
async def user_conversations(user_id: int, session: SessionDep, admin: AdminUser) -> list[dict]:
    return await AdminService(session).conversations(user_id)


@router.get("/conversations/{conversation_id}/messages", summary="指定对话的消息记录（站长只读）")
async def conversation_messages(
    conversation_id: int,
    session: SessionDep,
    admin: AdminUser,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    return await AdminService(session).conversation_messages(conversation_id, limit=limit)
