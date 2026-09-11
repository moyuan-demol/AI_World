from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ChatRequest(BaseModel):
    character_id: int
    message: str = Field(min_length=1)
    conversation_id: int | None = None
    knowledge_id: int | None = None
    use_knowledge: bool = True


class SourceOut(BaseModel):
    document_id: int
    knowledge_id: int
    filename: str
    score: float
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    conversation_id: int
    character_id: int
    model: str
    offline: bool = False
    sources: list[SourceOut] = []


class MessageOut(ORMModel):
    id: int
    conversation_id: int
    role: str
    content: str
    created_time: datetime | None = None


class ConversationOut(ORMModel):
    id: int
    character_id: int
    title: str
    created_time: datetime | None = None
