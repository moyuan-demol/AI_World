"""AI chat with RAG context, conversation persistence and data isolation."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deepseek_client import AIClient, get_ai_client
from app.ai.offline import offline_chat_answer
from app.ai.prompts import build_character_system_prompt, build_rag_user_message
from app.config.settings import settings
from app.core.errors import NotFoundError
from app.models.chat import Conversation, Message
from app.rag.rag_service import RagService
from app.repositories.character_repository import CharacterRepository
from app.repositories.chat_repository import ConversationRepository, MessageRepository
from app.repositories.memory_repository import MemoryRepository
from app.schemas.chat import ChatRequest, ChatResponse, SourceOut

logger = logging.getLogger(__name__)

# 历史窗口、字符预算、记忆条数均可在 .env 配置（HISTORY_LIMIT / HISTORY_CHAR_BUDGET / MEMORY_INJECT_LIMIT）


class ChatService:
    def __init__(self, session: AsyncSession, ai_client: AIClient | None = None) -> None:
        self.session = session
        self.characters = CharacterRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.memories = MemoryRepository(session)
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

        system_prompt = build_character_system_prompt(character)
        memory_block = await self._memory_block(user_id, character.id)
        if memory_block:
            system_prompt = system_prompt + "\n\n" + memory_block

        history = await self._trimmed_history(conversation.id)
        prompt = [{"role": "system", "content": system_prompt}]
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

    async def _memory_block(self, user_id: int, character_id: int) -> str:
        """把长期记忆注入 system prompt（角色专属 + 全局，按最近写入取有限条）。"""
        try:
            memories = await self.memories.list_for_context(
                user_id, character_id=character_id, limit=settings.memory_inject_limit
            )
        except Exception:
            logger.exception("memory lookup failed")
            return ""
        if not memories:
            return ""
        lines = ["以下是关于该用户的长期记忆，请在合适的时候自然使用，不要机械复述："]
        for memory in memories:
            lines.append("- [" + memory.memory_type + "] " + memory.content)
        return "\n".join(lines)

    async def _trimmed_history(self, conversation_id: int) -> list[Message]:
        """按「条数 + 字符预算」双重裁剪历史。

        注意：这是固定窗口 + 字符预算，不是无限记忆 ——
        超出预算的最旧消息会被丢弃；要长期记住事实请写入 Memory 表。
        """
        messages = await self.messages.list_recent(conversation_id, limit=settings.history_limit)
        budget = max(500, settings.history_char_budget)
        selected: list[Message] = []
        used = 0
        for message in reversed(messages):
            cost = len(message.content or "")
            if selected and used + cost > budget:
                break
            selected.append(message)
            used += cost
        return list(reversed(selected))

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
