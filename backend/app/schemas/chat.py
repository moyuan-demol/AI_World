from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ChatRequest(BaseModel):
    character_id: int
    message: str = Field(min_length=1)
    conversation_id: int | None = None
    knowledge_id: int | None = None
    use_knowledge: bool = True
    # single = 原有单轮 RAG；multi = 多 Agent 协作（拆解→查找→审查→整理）
    rag_mode: str = "single"


class SourceOut(BaseModel):
    document_id: int
    knowledge_id: int
    filename: str
    score: float
    snippet: str


class AgentStepOut(BaseModel):
    agent: str
    role: str
    output: str


class ChatResponse(BaseModel):
    answer: str
    conversation_id: int
    character_id: int
    model: str
    offline: bool = False
    sources: list[SourceOut] = []
    # 多 Agent 模式下的协作过程（single 模式为空）
    agents: list[AgentStepOut] = []
    sub_questions: list[str] = []
    evidence: str = ""
    rounds: int = 1


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
