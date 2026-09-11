"""AI chat with RAG context, conversation persistence and data isolation."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deepseek_client import AIClient, get_ai_client
from app.ai.offline import offline_chat_answer
from app.ai.prompts import build_character_system_prompt, build_rag_user_message
from app.core.errors import NotFoundError
from app.models.chat import Conversation, Message
from app.rag.rag_service import RagService
from app.repositories.character_repository import CharacterRepository
from app.repositories.chat_repository import ConversationRepository, MessageRepository
from app.schemas.chat import ChatRequest, ChatResponse, SourceOut

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 12


class ChatService:
    def __init__(self, session: AsyncSession, ai_client: AIClient | None = None) -> None:
        self.session = session
        self.characters = CharacterRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.rag = RagService(session)
        self.ai = ai_client or get_ai_client()

    # ------------------------------------------------------------------ #
    async def chat(self, user_id: int, payload: ChatRequest) -> ChatResponse:
        character = await self.characters.get_for_user(payload.character_id, user_id)
        if character is None:
            raise NotFoundError("AI 伙伴不存在或无权访问")

        conversation = await self._resolve_conversation(user_id, payload, character.name)

        context_block = ""
        chunks = []
        if payload.use_knowledge:
            context_block, chunks = await self.rag.build_context(
                user_id=user_id,
                query=payload.message,
                knowledge_id=payload.knowledge_id,
            )

        history = await self.messages.list_recent(conversation.id, limit=HISTORY_LIMIT)
        prompt = [{"role": "system", "content": build_character_system_prompt(character)}]
        for message in history:
            prompt.append({"role": message.role, "content": message.content})
        prompt.append({"role": "user", "content": build_rag_user_message(payload.message, context_block)})

        result = await self.ai.chat(
            prompt,
            offline_fallback=lambda: offline_chat_answer(
                character.name,
                character.role,
                payload.message,
                chunks,
            ),
        )

        sources = [
            SourceOut(
                document_id=chunk.document_id,
                knowledge_id=chunk.knowledge_id,
                filename=chunk.filename,
                score=chunk.score,
                snippet=chunk.content[:240],
            )
            for chunk in chunks
        ]

        await self.messages.create(
            conversation_id=conversation.id,
            user_id=user_id,
            role="user",
            content=payload.message,
            sources="[]",
        )
        await self.messages.create(
            conversation_id=conversation.id,
            user_id=user_id,
            role="assistant",
            content=result.text,
            sources=json.dumps([item.model_dump() for item in sources], ensure_ascii=False),
        )
        await self.session.commit()

        return ChatResponse(
            answer=result.text,
            conversation_id=conversation.id,
            character_id=character.id,
            model=result.model,
            offline=result.offline,
            sources=sources,
        )

    async def _resolve_conversation(self, user_id: int, payload: ChatRequest, character_name: str) -> Conversation:
        if payload.conversation_id:
            conversation = await self.conversations.get_for_user(payload.conversation_id, user_id)
            if conversation is None:
                raise NotFoundError("会话不存在或无权访问")
            return conversation
        title = payload.message.strip().replace("\n", " ")[:30] or ("与" + character_name + "的对话")
        return await self.conversations.create(
            user_id=user_id,
            character_id=payload.character_id,
            title=title,
        )

    # ------------------------------------------------------------------ #
    async def history(
        self,
        user_id: int,
        *,
        conversation_id: int | None = None,
        character_id: int | None = None,
        limit: int = 100,
    ) -> list[Message]:
        if conversation_id is not None:
            conversation = await self.conversations.get_for_user(conversation_id, user_id)
            if conversation is None:
                raise NotFoundError("会话不存在或无权访问")
            return await self.messages.list_by_conversation(conversation_id, limit=limit)

        conversations = await self.conversations.list_by_user(user_id, character_id=character_id)
        if not conversations:
            return []
        return await self.messages.list_by_conversation(conversations[0].id, limit=limit)

    async def list_conversations(self, user_id: int, character_id: int | None = None) -> list[Conversation]:
        return await self.conversations.list_by_user(user_id, character_id=character_id)

    async def delete_conversation(self, user_id: int, conversation_id: int) -> None:
        conversation = await self.conversations.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise NotFoundError("会话不存在或无权访问")
        await self.conversations.delete(conversation)
        await self.session.commit()
