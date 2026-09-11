"""Memory endpoints (phase 2 interface, usable from day one)."""

from datetime import datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, SessionDep, WriteLimit
from app.schemas.common import ORMModel
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memories", tags=["memory"])


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "fact"
    character_id: int | None = None


class MemoryOut(ORMModel):
    id: int
    character_id: int | None = None
    memory_type: str
    content: str
    created_time: datetime | None = None


@router.get("", response_model=list[MemoryOut], summary="记忆列表")
async def list_memories(
    session: SessionDep,
    user: CurrentUser,
    character_id: int | None = Query(default=None),
) -> list[MemoryOut]:
    memories = await MemoryService(session).list(user.id, character_id=character_id)
    return [MemoryOut.model_validate(item) for item in memories]


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED, summary="写入一条记忆")
async def create_memory(
    payload: MemoryCreate,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> MemoryOut:
    memory = await MemoryService(session).create(
        user.id,
        content=payload.content,
        memory_type=payload.memory_type,
        character_id=payload.character_id,
    )
    return MemoryOut.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除记忆")
async def delete_memory(
    memory_id: int,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> None:
    await MemoryService(session).delete(user.id, memory_id)
