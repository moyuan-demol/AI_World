"""AI chat with RAG context, conversation persistence and data isolation."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deepseek_client import AIClient, get_ai_client
from app.ai.offline import offline_chat_answer
from app.ai.prompts import build_character_system_prompt, build_context_block, build_rag_user_message
from app.config.settings import settings
from app.core.errors import NotFoundError
from app.models.chat import Conversation, Message
from app.rag.bm25 import reciprocal_rank_fusion
from app.rag.embedding import EmbeddingConfig, embed_query
from app.rag.meta_query import build_meta_chunks, is_meta_question
from app.rag.multi_agent import MultiAgentRag
from app.rag.query_rewrite import build_rewrite_messages, needs_rewrite, parse_rewritten_queries
from app.rag.rag_service import RagService
from app.rag.rerank import rerank
from app.rag.retriever import RetrievedChunk, cosine_similarity
from app.tools.web_search import WebSearchTool
from app.repositories.character_repository import CharacterRepository
from app.repositories.character_knowledge_repository import CharacterKnowledgeRepository
from app.repositories.chat_repository import ConversationRepository, MessageRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.knowledge_repository import KnowledgeRepository
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
        search_tool: WebSearchTool | None = None,
    ) -> None:
        self.session = session
        self.search_tool = search_tool
        self.characters = CharacterRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.memories = MemoryRepository(session)
        self.bindings = CharacterKnowledgeRepository(session)
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

        # 角色知识边界：绑定了知识库就只检索绑定的那些（身份隔离）
        scoped_ids = await self.bindings.list_knowledge_ids(character.id)

        context_block = ""
        chunks = []
        if payload.use_knowledge:
            if is_meta_question(payload.message):
                # 元信息类问题（作者/页数/上传时间/文件名…）：跳过向量检索，
                # 直接用文档元数据组织上下文，既准确又省 token
                context_block, chunks = await self._meta_context(
                    user_id, payload.knowledge_id, scoped_ids
                )
            else:
                # 普通模式：可选的查询改写（需要模型，失败自动降级）+ 多路 RRF 融合 + 精排
                context_block, chunks = await self._knowledge_context(
                    user_id, payload.message, payload.knowledge_id, scoped_ids
                )

        # 外部世界接口：把网页资料并入上下文（失败会自动降级，不阻塞回答）
        web_chunks, web_reports = await self._fetch_web(payload.message, payload.use_web)
        if web_chunks:
            chunks = list(chunks) + web_chunks
            context_block = build_context_block(chunks)[: settings.max_context_chars]

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
            web_reports=web_reports,
        )

    async def _fetch_web(
        self, query: str, enabled: bool
    ) -> tuple[list[RetrievedChunk], list[dict]]:
        """调用外部检索；任何失败都降级为空结果，并如实报告状态。

        返回的网页资料被包装成伪 RetrievedChunk（document_id 为负数），
        这样能和知识库片段一起进入上下文与"引用来源"。
        """
        if not enabled or self.search_tool is None or not self.search_tool.enabled:
            return [], []
        outcome = await self.search_tool.search(query)
        chunks = [
            RetrievedChunk(
                document_id=-(index + 1),
                knowledge_id=0,
                filename="🌐 " + item.provider + " · " + (item.title or item.url)[:60],
                chunk_index=0,
                content=(item.snippet or item.title) + "\n链接：" + item.url,
                score=0.0,
            )
            for index, item in enumerate(outcome.results)
        ]
        reports = [
            {
                "provider": report.provider,
                "ok": report.ok,
                "count": report.count,
                "error": report.error,
                "elapsed_ms": report.elapsed_ms,
            }
            for report in outcome.reports
        ]
        return chunks, reports

    # ------------------------------------------------------------------ #
    async def _knowledge_context(
        self,
        user_id: int,
        question: str,
        knowledge_id: int | None,
        scoped_ids: list[int],
    ) -> tuple[str, list[RetrievedChunk]]:
        """普通模式上下文：可选查询改写 -> 多路检索 -> RRF 融合 -> Rerank 精排。"""
        queries = await self._rewrite_queries(question)
        if len(queries) <= 1:
            # 未启用/未改写/只得到一条查询：保持原有单路检索行为
            return await self.rag.build_context(
                user_id=user_id,
                query=queries[0] if queries else question,
                knowledge_id=knowledge_id,
                knowledge_ids=scoped_ids or None,
            )

        # 多路检索：每路先独立跑完整的"混合检索 + 合并 + 多样性 + 精排"，
        # 再用 RRF 按排名融合（两路量纲不同也能安全合并）。
        rankings: list[list[int]] = []
        collected: dict[int, RetrievedChunk] = {}
        for query in queries:
            results = await self.rag.retrieve(
                user_id=user_id,
                query=query,
                knowledge_id=knowledge_id,
                knowledge_ids=scoped_ids or None,
            )
            rankings.append([chunk.document_id for chunk in results])
            for chunk in results:
                collected.setdefault(chunk.document_id, chunk)

        fused = reciprocal_rank_fusion(*rankings)
        merged = [collected[doc_id] for doc_id, _score in fused if doc_id in collected]
        # 融合后用"用户原问题"再做一次精排，保证最终仍以原始意图为准
        reranked = rerank(question, merged, settings.retrieval_top_k)
        context = build_context_block(reranked)
        if len(context) > settings.max_context_chars:
            context = context[: settings.max_context_chars]
        return context, reranked

    async def _rewrite_queries(self, question: str) -> list[str]:
        """查询改写（需要模型）：返回 1..4 条检索查询。

        优雅降级是硬要求：未配置模型 / 未达到触发条件 / 调用异常 / 输出为空，
        一律返回 [原问题]，绝不因为改写失败而影响正常回答。
        """
        fallback = [question]
        if not settings.query_rewrite_enabled:
            return fallback
        # FakeAI 与真实客户端都有 is_configured；缺失时按"未配置"处理
        if not getattr(self.ai, "is_configured", False):
            return fallback
        if not needs_rewrite(question, settings.query_rewrite_min_chars):
            return fallback
        try:
            result = await self.ai.chat(
                build_rewrite_messages(question),
                temperature=0.1,
                max_tokens=200,
            )
        except Exception:
            logger.warning("查询改写调用失败，回退为原问题检索", exc_info=True)
            return fallback

        rewrites = parse_rewritten_queries(getattr(result, "text", "") or "")
        if not rewrites:
            return fallback
        # 始终保留原问题作为一路查询：这是召回下限，改写失手时也不至于更差
        queries = list(rewrites)
        if question.strip() and question.strip() not in queries:
            queries.append(question)
        return queries

    async def _meta_context(
        self,
        user_id: int,
        knowledge_id: int | None,
        scoped_ids: list[int],
    ) -> tuple[str, list[RetrievedChunk]]:
        """元信息直答：用文档元数据组织上下文，不走向量检索。"""
        documents = await self._meta_documents(user_id, knowledge_id, scoped_ids)
        chunks = build_meta_chunks(documents)
        context = build_context_block(chunks)
        if len(context) > settings.max_context_chars:
            context = context[: settings.max_context_chars]
        return context, chunks

    async def _meta_documents(
        self,
        user_id: int,
        knowledge_id: int | None,
        scoped_ids: list[int],
    ) -> list[dict]:
        """按与检索一致的"知识边界"收集文档元数据（同一文件聚合为一条）。"""
        knowledge_repo = KnowledgeRepository(self.session)
        bases = await knowledge_repo.list_by_user(user_id)
        owned = {base.id for base in bases}
        if knowledge_id is not None:
            if knowledge_id not in owned:
                return []
            scope = await knowledge_repo.list_descendant_ids(user_id, [knowledge_id])
        elif scoped_ids:
            scope = await knowledge_repo.list_descendant_ids(
                user_id, [item for item in scoped_ids if item in owned]
            )
        else:
            scope = [base.id for base in bases]
        if not scope:
            return []

        records = await DocumentRepository(self.session).list_by_knowledge_ids(scope)
        grouped: dict[tuple[int, str], dict] = {}
        for record in records:
            key = (record.knowledge_id, record.filename)
            item = grouped.get(key)
            if item is None:
                item = {
                    "knowledge_id": record.knowledge_id,
                    "filename": record.filename,
                    "created_time": record.created_time,
                    "chunk_count": 0,
                    "contents": [],
                }
                grouped[key] = item
            item["chunk_count"] += 1
            item["contents"].append(record.content or "")
            # 同一文件的切片同一批写入，取第一个非空上传时间即可
            if item.get("created_time") is None:
                item["created_time"] = record.created_time
        return list(grouped.values())

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

        scoped_ids = await self.bindings.list_knowledge_ids(character.id)

        async def retrieve(query: str):
            if not payload.use_knowledge:
                return []
            return await self.rag.retrieve(
                user_id=user_id,
                query=query,
                knowledge_id=payload.knowledge_id,
                knowledge_ids=scoped_ids or None,
                top_k=settings.multi_agent_top_k,
            )

        pipeline = MultiAgentRag(
            self.ai,
            retrieve,
            system_prompt=system_prompt,
            max_rounds=settings.multi_agent_max_rounds,
        )
        result = await pipeline.run(payload.message)

        web_chunks, web_reports = await self._fetch_web(payload.message, payload.use_web)
        if web_chunks:
            result.sources = list(result.sources) + web_chunks

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
            web_reports=web_reports,
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
