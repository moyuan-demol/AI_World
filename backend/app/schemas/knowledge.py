from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class KnowledgeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    # 为空 = 顶层节点；指定则为该节点的子节点（分级）
    parent_id: int | None = None
    # 公共库（所有人可查）：只有站长可以创建，Service 层再做一次权限校验
    is_public: bool = False


class KnowledgeOut(ORMModel):
    id: int
    name: str
    description: str
    parent_id: int | None = None
    is_public: bool = False
    created_time: datetime | None = None
    document_count: int = 0


class DocumentOut(ORMModel):
    id: int
    knowledge_id: int
    filename: str
    chunk_index: int
    content: str
    created_time: datetime | None = None


class UploadResult(BaseModel):
    knowledge: KnowledgeOut
    filename: str
    chunk_count: int
    char_count: int
    message: str = "上传并向量化成功"
