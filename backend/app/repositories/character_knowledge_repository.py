from sqlalchemy import delete, select

from app.models.character_knowledge import CharacterKnowledge
from app.repositories.base import BaseRepository


class CharacterKnowledgeRepository(BaseRepository[CharacterKnowledge]):
    model = CharacterKnowledge

    async def list_knowledge_ids(self, character_id: int) -> list[int]:
        result = await self.session.execute(
            select(CharacterKnowledge.knowledge_id).where(
                CharacterKnowledge.character_id == character_id
            )
        )
        return [int(item) for item in result.scalars().all()]

    async def map_by_characters(self, character_ids: list[int]) -> dict[int, list[int]]:
        """一次查询取回多个角色的绑定关系（替代逐个角色查询）。"""
        if not character_ids:
            return {}
        result = await self.session.execute(
            select(CharacterKnowledge.character_id, CharacterKnowledge.knowledge_id).where(
                CharacterKnowledge.character_id.in_(character_ids)
            )
        )
        mapping: dict[int, list[int]] = {}
        for character_id, knowledge_id in result.all():
            mapping.setdefault(int(character_id), []).append(int(knowledge_id))
        return mapping

    async def replace(self, character_id: int, knowledge_ids: list[int]) -> list[int]:
        """整体替换绑定关系（幂等）。"""
        await self.session.execute(
            delete(CharacterKnowledge).where(CharacterKnowledge.character_id == character_id)
        )
        unique_ids = []
        for item in knowledge_ids:
            if item not in unique_ids:
                unique_ids.append(item)
        for knowledge_id in unique_ids:
            await self.create(character_id=character_id, knowledge_id=knowledge_id)
        return unique_ids

    async def delete_by_character(self, character_id: int) -> None:
        await self.session.execute(
            delete(CharacterKnowledge).where(CharacterKnowledge.character_id == character_id)
        )
