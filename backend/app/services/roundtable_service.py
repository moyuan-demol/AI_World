"""AI round table: manager agent + expert agents + final summary."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deepseek_client import AIClient, get_ai_client
from app.ai.offline import offline_agent_answer, offline_summary
from app.ai.prompts import (
    DEFAULT_ROUNDTABLE_AGENTS,
    build_agent_prompt,
    build_manager_brief_prompt,
    build_manager_summary_prompt,
    character_to_agent_spec,
)
from app.core.errors import NotFoundError, ValidationError
from app.rag.embedding import EmbeddingConfig
from app.rag.rag_service import RagService
from app.repositories.character_repository import CharacterRepository
from app.schemas.roundtable import (
    AgentSpec,
    RoundtableAgentResult,
    RoundtableRequest,
    RoundtableResponse,
)

logger = logging.getLogger(__name__)

MANAGER_NAME = "主持Agent"
MANAGER_ROLE = "会议主持与方案汇总"


class RoundtableService:
    def __init__(
        self,
        session: AsyncSession,
        ai_client: AIClient | None = None,
        embedding_config: EmbeddingConfig | None = None,
    ) -> None:
        self.session = session
        self.characters = CharacterRepository(session)
        self.rag = RagService(session, embedding_config)
        self.ai = ai_client or get_ai_client()

    # ------------------------------------------------------------------ #
    async def _resolve_agents(self, user_id: int, payload: RoundtableRequest) -> list[AgentSpec]:
        if payload.agents:
            return [AgentSpec(**agent.model_dump()) for agent in payload.agents]

        if payload.character_ids:
            agents: list[AgentSpec] = []
            for character_id in payload.character_ids:
                character = await self.characters.get_for_user(character_id, user_id)
                if character is None:
                    raise NotFoundError("AI 伙伴 " + str(character_id) + " 不存在或无权访问")
                agents.append(AgentSpec(**character_to_agent_spec(character)))
            return agents

        return [AgentSpec(**agent) for agent in DEFAULT_ROUNDTABLE_AGENTS]

    # ------------------------------------------------------------------ #
    async def run(self, user_id: int, payload: RoundtableRequest) -> RoundtableResponse:
        question = payload.question.strip()
        if not question:
            raise ValidationError("议题不能为空")

        agents = await self._resolve_agents(user_id, payload)
        if not agents:
            raise ValidationError("至少需要一个参会 Agent")

        context_block = ""
        source_names: list[str] = []
        if payload.use_knowledge:
            context_block, chunks = await self.rag.build_context(
                user_id=user_id,
                query=question,
                knowledge_id=payload.knowledge_id,
            )
            source_names = sorted({chunk.filename for chunk in chunks})

        manager_brief = ""
        offline = not self.ai.is_configured
        model = "offline-demo"
        if payload.include_manager:
            brief_result = await self.ai.chat(
                [
                    {"role": "system", "content": "你是一名严谨的会议主持人，负责拆解议题。"},
                    {"role": "user", "content": build_manager_brief_prompt(question)},
                ],
                temperature=0.3,
                offline_fallback=lambda: "1. 关键需求与目标用户\n2. 技术与实现路径\n3. 商业价值与风险",
            )
            manager_brief = brief_result.text
            model = brief_result.model
            offline = offline or brief_result.offline

        results = await asyncio.gather(
            *[
                self._run_agent(agent, question=question, brief=manager_brief, context_block=context_block)
                for agent in agents
            ]
        )

        summary = ""
        if payload.include_manager:
            summary_result = await self.ai.chat(
                [
                    {"role": "system", "content": "你是会议主持人，负责汇总专家意见并给出最终方案。"},
                    {
                        "role": "user",
                        "content": build_manager_summary_prompt(
                            question, [(item.agent, item.answer) for item in results]
                        ),
                    },
                ],
                temperature=0.4,
                offline_fallback=lambda: offline_summary(
                    question, [(item.agent, item.answer) for item in results]
                ),
            )
            summary = summary_result.text
            offline = offline or summary_result.offline

        return RoundtableResponse(
            question=question,
            manager_brief=manager_brief,
            manager=MANAGER_NAME if payload.include_manager else "",
            results=list(results),
            summary=summary,
            model=model,
            offline=offline,
            sources=source_names,
        )

    async def _run_agent(
        self,
        agent: AgentSpec,
        *,
        question: str,
        brief: str,
        context_block: str,
    ) -> RoundtableAgentResult:
        system_prompt = build_agent_prompt(agent.agent, agent.role, agent.goal, agent.personality)
        user_parts = ["议题：" + question]
        if brief:
            user_parts.append("主持人拆解：\n" + brief)
        if context_block:
            user_parts.append(context_block)
        user_parts.append("请给出你作为「" + agent.agent + "」的发言。")

        result = await self.ai.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            temperature=0.6,
            offline_fallback=lambda: offline_agent_answer(agent.agent, agent.role, question, brief),
        )
        return RoundtableAgentResult(
            agent=agent.agent,
            role=agent.role,
            answer=result.text,
            model=result.model,
            offline=result.offline,
        )

    # ------------------------------------------------------------------ #
    async def available_agents(self, user_id: int) -> list[dict[str, object]]:
        presets = [
            {"agent": item["agent"], "role": item["role"], "source": "default"} for item in DEFAULT_ROUNDTABLE_AGENTS
        ]
        characters = await self.characters.list_by_user(user_id)
        presets.extend(
            [
                {
                    "agent": character.name,
                    "role": character.role or "AI 专家",
                    "source": "character",
                    "character_id": character.id,
                }
                for character in characters
            ]
        )
        return presets
