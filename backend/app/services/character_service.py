"""AI companion (character) business logic."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.character import Character
from app.repositories.character_knowledge_repository import CharacterKnowledgeRepository
from app.repositories.character_repository import CharacterRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.schemas.character import CharacterCreate, CharacterUpdate

DEFAULT_CHARACTERS: list[dict[str, str]] = [
    {
        "name": "张医生",
        "role": "医学专家",
        "personality": "严谨、耐心、循证",
        "expertise": "医疗 AI、临床决策支持、医疗合规",
        "speaking_style": "专业、克制，先给结论再给依据",
        "system_prompt": "回答医疗相关问题时必须提示不能替代执业医师的诊断，涉及用药请给出循证依据。",
    },
    {
        "name": "李工",
        "role": "技术架构师",
        "personality": "直接、结构化、重视工程细节",
        "expertise": "系统架构、大模型应用、性能与成本优化",
        "speaking_style": "先给方案对比表，再给推荐方案与实施步骤",
        "system_prompt": "优先考虑可落地性与成本，明确指出技术风险与验证方式。",
    },
    {
        "name": "王顾问",
        "role": "商业顾问",
        "personality": "数据驱动、关注回报",
        "expertise": "市场规模测算、竞品分析、商业模式与 ROI",
        "speaking_style": "用量化数据说话，善用分层结论",
        "system_prompt": "无法获取真实数据时请说明假设前提，不要编造具体数字。",
    },
]


class CharacterService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.characters = CharacterRepository(session)
        self.bindings = CharacterKnowledgeRepository(session)
        self.knowledge = KnowledgeRepository(session)

    async def create(self, user_id: int, payload: CharacterCreate) -> Character:
        character = await self.characters.create(
            user_id=user_id,
            name=payload.name,
            role=payload.role,
            personality=payload.personality,
            expertise=payload.expertise,
            speaking_style=payload.speaking_style,
            system_prompt=payload.system_prompt,
        )
        await self.session.commit()
        return character

    async def list(self, user_id: int) -> list[Character]:
        return await self.characters.list_by_user(user_id)

    async def get(self, user_id: int, character_id: int) -> Character:
        character = await self.characters.get_for_user(character_id, user_id)
        if character is None:
            raise NotFoundError("AI 伙伴不存在或无权访问")
        return character

    async def update(self, user_id: int, character_id: int, payload: CharacterUpdate) -> Character:
        character = await self.get(user_id, character_id)
        values = payload.model_dump(exclude_unset=True, exclude_none=True)
        if values:
            character = await self.characters.update(character, **values)
        await self.session.commit()
        return character

    async def delete(self, user_id: int, character_id: int) -> None:
        character = await self.get(user_id, character_id)
        await self.bindings.delete_by_character(character.id)
        await self.characters.delete(character)
        await self.session.commit()

    # ---- 知识边界：角色 ↔ 知识库 绑定 ---------------------------------- #
    async def knowledge_ids(self, user_id: int, character_id: int) -> list[int]:
        character = await self.get(user_id, character_id)
        return await self.bindings.list_knowledge_ids(character.id)

    async def set_knowledge_ids(
        self, user_id: int, character_id: int, knowledge_ids: list[int]
    ) -> list[int]:
        """整体替换角色绑定的知识库。只会绑定属于该用户自己的知识库。"""
        character = await self.get(user_id, character_id)
        owned = {base.id for base in await self.knowledge.list_by_user(user_id)}
        safe_ids = [item for item in knowledge_ids if item in owned]
        result = await self.bindings.replace(character.id, safe_ids)
        await self.session.commit()
        return result

    async def knowledge_map(self, user_id: int) -> dict[int, list[int]]:
        """一次性取出该用户所有角色的绑定关系（给列表页用，避免 N+1 查询）。"""
        characters = await self.characters.list_by_user(user_id)
        return {
            character.id: await self.bindings.list_knowledge_ids(character.id)
            for character in characters
        }

    async def count(self, user_id: int) -> int:
        return await self.characters.count_by_user(user_id)

    async def ensure_defaults(self, user_id: int) -> int:
        """补齐预置角色：**按名字判重，缺哪个补哪个**。

        修正历史漏洞：旧实现只在「一个都不剩」（count == 0）时才恢复，
        导致只删掉其中一两个时永远补不回来（公网演示站会一直缺角色）。

        只补预置角色，绝不碰用户自己创建的角色。
        """
        characters = await self.characters.list_by_user(user_id)
        existing_names = {item.name for item in characters}
        created = 0
        for preset in DEFAULT_CHARACTERS:
            if preset["name"] in existing_names:
                continue
            await self.characters.create(user_id=user_id, **preset)
            created += 1
        if created:
            await self.session.commit()
        return created
