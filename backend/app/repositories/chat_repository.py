from sqlalchemy import select

from app.models.chat import Conversation, Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def list_by_user(self, user_id: int, character_id: int | None = None) -> list[Conversation]:
        statement = select(Conversation).where(Conversation.user_id == user_id)
        if character_id is not None:
            statement = statement.where(Conversation.character_id == character_id)
        statement = statement.order_by(Conversation.id.desc())
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_for_user(self, conversation_id: int, user_id: int) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        return result.scalars().first()


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_by_conversation(self, conversation_id: int, limit: int = 200) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent(self, conversation_id: int, limit: int = 20) -> list[Message]:
        """Newest N messages, returned oldest first (for prompt history)."""
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(list(result.scalars().all())))

    async def list_by_user(self, user_id: int, limit: int = 100) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
