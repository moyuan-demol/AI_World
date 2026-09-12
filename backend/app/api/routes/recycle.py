"""回收站接口：列出 / 恢复 / 彻底删除（沿用现有鉴权依赖与写限流）。"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep, WriteLimit
from app.schemas.recycle import RecycleActionOut, RecycleItemOut
from app.services.recycle_service import RecycleService

router = APIRouter(prefix="/recycle", tags=["recycle"])


@router.get("", response_model=list[RecycleItemOut], summary="我的回收站（知识库与文档）")
async def list_recycle(session: SessionDep, user: CurrentUser) -> list[RecycleItemOut]:
    items = await RecycleService(session).list_deleted(user.id)
    return [RecycleItemOut(**item) for item in items]


@router.post(
    "/{kind}/{item_id}/restore",
    response_model=RecycleActionOut,
    summary="恢复回收站条目",
)
async def restore_item(
    kind: str,
    item_id: int,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> RecycleActionOut:
    result = await RecycleService(session).restore(user.id, kind, item_id)
    return RecycleActionOut(**result)


@router.delete(
    "/{kind}/{item_id}",
    response_model=RecycleActionOut,
    summary="彻底删除（物理删除，公共库内容仅站长可执行）",
)
async def purge_item(
    kind: str,
    item_id: int,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> RecycleActionOut:
    result = await RecycleService(session).purge(user.id, kind, item_id)
    return RecycleActionOut(**result)
