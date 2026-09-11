"""AI companion endpoints. Every query is scoped to the current user."""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep, WriteLimit
from app.schemas.character import CharacterCreate, CharacterOut, CharacterUpdate
from app.services.character_service import CharacterService

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("", response_model=list[CharacterOut], summary="我的 AI 伙伴列表")
async def list_characters(session: SessionDep, user: CurrentUser) -> list[CharacterOut]:
    characters = await CharacterService(session).list(user.id)
    return [CharacterOut.model_validate(item) for item in characters]


@router.post("", response_model=CharacterOut, status_code=status.HTTP_201_CREATED, summary="创建 AI 伙伴")
async def create_character(
    payload: CharacterCreate,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> CharacterOut:
    character = await CharacterService(session).create(user.id, payload)
    return CharacterOut.model_validate(character)


@router.get("/{character_id}", response_model=CharacterOut, summary="AI 伙伴详情")
async def get_character(character_id: int, session: SessionDep, user: CurrentUser) -> CharacterOut:
    character = await CharacterService(session).get(user.id, character_id)
    return CharacterOut.model_validate(character)


@router.put("/{character_id}", response_model=CharacterOut, summary="更新 AI 伙伴")
async def update_character(
    character_id: int,
    payload: CharacterUpdate,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> CharacterOut:
    character = await CharacterService(session).update(user.id, character_id, payload)
    return CharacterOut.model_validate(character)


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除 AI 伙伴")
async def delete_character(
    character_id: int,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> None:
    await CharacterService(session).delete(user.id, character_id)
