"""Chat endpoints: send a message (with RAG) and read history."""

from fastapi import APIRouter, Query, status

from app.api.deps import AiLimit, CurrentUser, SessionDep
from app.schemas.chat import ChatRequest, ChatResponse, ConversationOut, MessageOut
from app.tools.web_search import WebSearchTool
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse, summary="与 AI 伙伴对话（自动检索知识库）")
async def chat(payload: ChatRequest, session: SessionDep, user: CurrentUser, _: AiLimit) -> ChatResponse:
    return await ChatService(session, search_tool=WebSearchTool()).chat(user.id, payload)


@router.get("/conversations", response_model=list[ConversationOut], summary="会话列表")
async def list_conversations(
    session: SessionDep,
    user: CurrentUser,
    character_id: int | None = Query(default=None),
) -> list[ConversationOut]:
    conversations = await ChatService(session).list_conversations(user.id, character_id=character_id)
    return [ConversationOut.model_validate(item) for item in conversations]


@router.get("/history", response_model=list[MessageOut], summary="聊天记录")
async def history(
    session: SessionDep,
    user: CurrentUser,
    conversation_id: int | None = Query(default=None),
    character_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
) -> list[MessageOut]:
    messages = await ChatService(session).history(
        user.id,
        conversation_id=conversation_id,
        character_id=character_id,
        limit=limit,
    )
    return [MessageOut.model_validate(item) for item in messages]


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除会话",
)
async def delete_conversation(conversation_id: int, session: SessionDep, user: CurrentUser) -> None:
    await ChatService(session).delete_conversation(user.id, conversation_id)
