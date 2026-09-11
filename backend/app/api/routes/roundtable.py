"""AI round table endpoint: multi-agent discussion then manager summary."""

from fastapi import APIRouter

from app.api.deps import AiLimit, CurrentUser, SessionDep
from app.schemas.roundtable import RoundtableRequest, RoundtableResponse
from app.services.roundtable_service import RoundtableService

router = APIRouter(prefix="/roundtable", tags=["roundtable"])


@router.get("/agents", summary="可用与会 Agent 列表")
async def agents(session: SessionDep, user: CurrentUser) -> list[dict]:
    return await RoundtableService(session).available_agents(user.id)


@router.post("", response_model=RoundtableResponse, summary="发起 AI 圆桌讨论")
async def run_roundtable(
    payload: RoundtableRequest,
    session: SessionDep,
    user: CurrentUser,
    _: AiLimit,
) -> RoundtableResponse:
    return await RoundtableService(session).run(user.id, payload)
