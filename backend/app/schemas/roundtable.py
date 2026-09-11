from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class AgentSpec(BaseModel):
    """Definition of one round table participant."""

    agent: str = Field(min_length=1, max_length=64)
    role: str = ""
    goal: str = ""
    personality: str = ""


class RoundtableRequest(BaseModel):
    question: str = Field(min_length=1)
    character_ids: list[int] | None = None
    agents: list[AgentSpec] | None = None
    knowledge_id: int | None = None
    use_knowledge: bool = False
    include_manager: bool = True


class RoundtableAgentResult(ORMModel):
    agent: str
    role: str
    answer: str
    model: str = ""
    offline: bool = False


class RoundtableResponse(BaseModel):
    question: str
    manager_brief: str = ""
    manager: str = ""
    results: list[RoundtableAgentResult] = []
    summary: str = ""
    model: str = ""
    offline: bool = False
    sources: list[str] = []
