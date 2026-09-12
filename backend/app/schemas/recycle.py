from datetime import datetime

from pydantic import BaseModel


class RecycleItemOut(BaseModel):
    """回收站里的一条记录（知识库或文档）。"""

    kind: str  # knowledge | document
    id: int
    name: str
    deleted_at: datetime | None = None
    owner_id: int = 0
    is_public: bool = False
    # 文档专属：它属于哪个知识库、该知识库是否也在回收站
    knowledge_id: int | None = None
    knowledge_name: str | None = None
    parent_deleted: bool = False
    # 知识库 = 子树切片总数；文档 = 切片数
    document_count: int = 0
    chunk_count: int = 0


class RecycleActionOut(BaseModel):
    kind: str
    id: int
    affected: int = 0
    message: str = ""
