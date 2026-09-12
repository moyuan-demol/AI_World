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
from app.rag.embedding import EmbeddingConfig, embed_query
from app.rag.multi_agent import MultiAgentRag
from app.rag.rag_service import RagService
from app.rag.retriever import cosine_similarity
from app.repositories.character_repository import CharacterRepository
from app.repositories.chat_repository import ConversationRepository, MessageRepository
from app.repositories.memory_repository import MemoryRepository
from app.schemas.chat import AgentStepOut, ChatRequest, ChatResponse, SourceOut

logger = logging.getLogger(__name__)

# 历史窗口、字符预算、记忆条数均可在 .env 配置
# （HISTORY_LIMIT / HISTORY_CHAR_BUDGET / MEMORY_INJECT_LIMIT / SUMMARY_ENABLED ...）


def parse_memory_items(text: str) -> list[dict]:
    """从模型输出里稳健地取出 JSON 数组（自动抽取记忆用）。"""
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        ai_client: AIClient | None = None,
        embedding_config: EmbeddingConfig | None = None,
    ) -> None:
        self.session = session
        self.characters = CharacterRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.memories = MemoryRepository(session)
        self.rag = RagService(session, embedding_config)
        self.ai = ai_client or get_ai_client()
        self.embedding_config = embedding_config

    # ------------------------------------------------------------------ #
    async def chat(self, user_id: int, payload: ChatRequest) -> ChatResponse:
        character = await self.characters.get_for_user(payload.character_id, user_id)
        if character is None:
            raise NotFoundError("AI 伙伴不存在或无权访问")

        conversation = await self._resolve_conversation(user_id, payload, character.name)

        # 多 Agent 协作模式：与单轮模式完全隔离，不改变原有行为
        if payload.rag_mode == "multi" and settings.multi_agent_enabled:
            return await self._chat_with_agents(user_id, payload, character, conversation)

        context_block = ""
        chunks = []
        if payload.use_knowledge:
            context_block, chunks = await self.rag.build_context(
                user_id=user_id,
                query=payload.message,
                knowledge_id=payload.knowledge_id,
            )

        system_prompt = build_character_system_prompt(character)

        # 长记忆第 3 类：长期事实记忆注入
        memory_block = await self._memory_block(user_id, character.id)
        if memory_block:
            system_prompt = system_prompt + "\n\n" + memory_block

        # 长记忆第 1 类：滚动摘要（把滑出窗口的旧消息压成摘要）
        summary = await self._refresh_summary(conversation, user_id)
        if summary:
            system_prompt = system_prompt + "\n\n[此前对话摘要]\n" + summary

        # 近端窗口（条数 + 字符预算）
        history = await self._trimmed_history(conversation.id)

        # 长记忆第 2 类：历史向量检索（召回窗口之外的相关片段）
        recalled = await self._relevant_history(
            conversation.id, payload.message, {message.id for message in history}
        )

        prompt = [{"role": "system", "content": system_prompt}]
        for message in history:
            prompt.append({"role": message.role, "content": message.content})

        user_content = build_rag_user_message(payload.message, context_block)
        if recalled:
            lines = [
                "- " + ("用户" if item.role == "user" else "助手") + "：" + (item.content or "")[:300]
                for item in recalled
            ]
            user_content = (
                "[历史上与本次提问相关的对话片段]\n" + "\n".join(lines) + "\n\n" + user_content
            )
        prompt.append({"role": "user", "content": user_content})

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

        # 长记忆第 3 类：自动事实抽取（每 N 条消息触发；无 Key 时自动跳过）
        try:
            await self._maybe_extract_memories(
                user_id, character.id, conversation.id, payload.message, result.text
            )
        except Exception:
            logger.exception("auto memory extraction failed")

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

    async def _chat_with_agents(
        self,
        user_id: int,
        payload: ChatRequest,
        character,
        conversation: Conversation,
    ) -> ChatResponse:
        """多 Agent RAG：拆解 → 查找 → 审查（有界补检）→ 整理。"""
        system_prompt = build_character_system_prompt(character)
        memory_block = await self._memory_block(user_id, character.id)
        if memory_block:
            system_prompt = system_prompt + "\n\n" + memory_block
        summary = await self._refresh_summary(conversation, user_id)
        if summary:
            system_prompt = system_prompt + "\n\n[此前对话摘要]\n" + summary

        async def retrieve(query: str):
            if not payload.use_knowledge:
                return []
            return await self.rag.retrieve(
                user_id=user_id,
                query=query,
                knowledge_id=payload.knowledge_id,
                top_k=settings.multi_agent_top_k,
            )

        pipeline = MultiAgentRag(
            self.ai,
            retrieve,
            system_prompt=system_prompt,
            max_rounds=settings.multi_agent_max_rounds,
        )
        result = await pipeline.run(payload.message)

        sources = [
            SourceOut(
                document_id=chunk.document_id,
                knowledge_id=chunk.knowledge_id,
                filename=chunk.filename,
                score=chunk.score,
                snippet=chunk.content[:240],
            )
            for chunk in result.sources
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
            content=result.answer,
            sources=json.dumps([item.model_dump() for item in sources], ensure_ascii=False),
        )
        await self.session.commit()

        try:
            await self._maybe_extract_memories(
                user_id, character.id, conversation.id, payload.message, result.answer
            )
        except Exception:
            logger.exception("auto memory extraction failed")

        return ChatResponse(
            answer=result.answer,
            conversation_id=conversation.id,
            character_id=character.id,
            model=result.model,
            offline=result.offline,
            sources=sources,
            agents=[
                AgentStepOut(agent=step.agent, role=step.role, output=step.output)
                for step in result.steps
            ],
            sub_questions=result.sub_questions,
            evidence=result.review_reason,
            rounds=result.rounds,
        )

    async def _refresh_summary(self, conversation: Conversation, user_id: int) -> str:
        """滚动摘要：把滑出窗口的旧消息并入会话摘要（长记忆第 1 件）。"""
        if not settings.summary_enabled:
            return conversation.summary or ""
        window = await self._trimmed_history(conversation.id)
        window_ids = {message.id for message in window}
        all_messages = await self.messages.list_by_conversation(conversation.id, limit=2000)
        upto = conversation.summary_upto_id or 0
        dropped = [
            message
            for message in all_messages
            if message.id not in window_ids and message.id > upto and (message.content or "").strip()
        ]
        if not dropped:
            return conversation.summary or ""

        transcript = "\n".join(
            ("用户：" if message.role == "user" else "助手：") + (message.content or "")[:400]
            for message in dropped
        )
        previous = conversation.summary or "（无）"
        result = await self.ai.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是对话摘要器。把旧摘要与新增对话合并成一段紧凑摘要，"
                        "必须保留：用户的身份/偏好/目标、已确认结论、未完成待办。只输出摘要正文。"
                    ),
                },
                {
                    "role": "user",
                    "content": "旧摘要：\n" + previous + "\n\n新增对话：\n" + transcript,
                },
            ],
            temperature=0.2,
            offline_fallback=lambda: self._offline_summary(previous, dropped),
        )
        conversation.summary = (result.text or "").strip()[:4000]
        conversation.summary_upto_id = max(message.id for message in dropped)
        await self.session.commit()
        return conversation.summary

    @staticmethod
    def _offline_summary(previous: str, dropped: list[Message]) -> str:
        head = previous if previous and previous != "（无）" else ""
        lines = [m.content.strip().replace("\n", " ")[:80] for m in dropped if m.content.strip()]
        merged = " / ".join(lines[-6:])
        return ((head + " | ") if head else "") + "（离线摘要）" + merged

    async def _relevant_history(
        self,
        conversation_id: int,
        query: str,
        exclude_ids: set[int],
        top_k: int | None = None,
    ) -> list[Message]:
        """历史向量检索：从窗口之外的历史里召回相关片段（长记忆第 2 件）。"""
        if not settings.history_retrieval_enabled or not query.strip():
            return []
        scanned = await self.messages.list_recent(
            conversation_id, limit=max(10, settings.history_retrieval_scan)
        )
        candidates = [
            message
            for message in scanned
            if message.id not in exclude_ids and (message.content or "").strip()
        ]
        if len(candidates) < 3:
            return []
        query_vector = await embed_query(query, self.embedding_config)
        if not query_vector:
            return []

        scored: list[tuple[float, Message]] = []
        dirty = False
        for message in candidates:
            try:
                vector = json.loads(message.embedding or "[]")
            except json.JSONDecodeError:
                vector = []
            if not vector or len(vector) != len(query_vector):
                vector = await embed_query((message.content or "")[:1000], self.embedding_config)
                message.embedding = json.dumps(vector)
                dirty = True
            score = cosine_similarity(query_vector, vector)
            if score > 0:
                scored.append((score, message))
        if dirty:
            await self.session.commit()
        scored.sort(key=lambda item: item[0], reverse=True)
        return [message for _score, message in scored[: (top_k or settings.history_retrieval_top_k)]]

    async def _maybe_extract_memories(
        self, user_id: int, character_id: int, conversation_id: int, question: str, answer: str
    ) -> None:
        """自动事实抽取：每 N 条消息抽 0-3 条长期事实写入 memories（长记忆第 3 件）。"""
        if not settings.memory_auto_extract or not self.ai.is_configured:
            return
        every = settings.memory_extract_every
        if every <= 0:
            return
        total = await self.messages.count_by_conversation(conversation_id)
        if total % every != 0:
            return
        try:
            result = await self.ai.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是长期记忆抽取器。从这轮对话中提取值得长期记住的用户事实或偏好"
                            "（身份、目标、偏好、项目信息）。没有就返回空数组。"
                            "只输出 JSON 数组，元素形如 "
                            '{"memory_type":"fact|preference|project|history","content":"..."}，'
                            "最多 3 条，每条不超过 60 字，不要任何解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": "用户：" + question[:500] + "\n助手：" + answer[:500],
                    },
                ],
                temperature=0.1,
                max_tokens=300,
            )
        except Exception:
            logger.exception("memory extraction failed")
            return

        items = parse_memory_items(result.text)
        if not items:
            return
        existing = {memory.content for memory in await self.memories.list_by_user(user_id)}
        created = 0
        for item in items:
            content = str(item.get("content", "")).strip()
            if not content or content in existing:
                continue
            await self.memories.create(
                user_id=user_id,
                character_id=character_id,
                memory_type=str(item.get("memory_type") or "fact")[:32],
                content=content[:500],
            )
            created += 1
        if created:
            await self.session.commit()
            logger.info("auto extracted %s memories", created)

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
