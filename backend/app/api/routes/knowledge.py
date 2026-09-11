"""Knowledge base endpoints: create, list, upload, inspect, delete."""

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUser, SessionDep, UploadLimit, WriteLimit
from app.core.files import sanitize_filename, validate_signature
from app.schemas.knowledge import DocumentOut, KnowledgeCreate, KnowledgeOut, UploadResult
from app.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeOut], summary="我的知识库列表")
async def list_knowledge(session: SessionDep, user: CurrentUser) -> list[KnowledgeOut]:
    return await KnowledgeService(session).list(user.id)


@router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED, summary="创建知识库")
async def create_knowledge(
    payload: KnowledgeCreate,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> KnowledgeOut:
    return await KnowledgeService(session).create(user.id, payload)


@router.post("/upload", response_model=UploadResult, summary="上传文件并做向量化 (RAG 入库)")
async def upload_document(
    session: SessionDep,
    user: CurrentUser,
    _: UploadLimit,
    file: UploadFile = File(..., description="pdf / docx / txt / md"),
    knowledge_id: int | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
) -> UploadResult:
    data = await file.read()
    filename = sanitize_filename(file.filename or "upload.txt")
    validate_signature(filename, data)
    try:
        return await KnowledgeService(session).upload(
            user.id,
            filename=filename,
            data=data,
            knowledge_id=knowledge_id,
            name=name,
            description=description,
        )
    finally:
        await file.close()


@router.get("/{knowledge_id}", response_model=KnowledgeOut, summary="知识库详情")
async def get_knowledge(knowledge_id: int, session: SessionDep, user: CurrentUser) -> KnowledgeOut:
    return await KnowledgeService(session).get_out(user.id, knowledge_id)


@router.get(
    "/{knowledge_id}/documents",
    response_model=list[DocumentOut],
    summary="知识库切片列表",
)
async def list_documents(
    knowledge_id: int,
    session: SessionDep,
    user: CurrentUser,
    limit: int = 50,
) -> list[DocumentOut]:
    safe_limit = max(1, min(limit, 200))
    documents = await KnowledgeService(session).list_documents(user.id, knowledge_id, limit=safe_limit)
    return [DocumentOut.model_validate(item) for item in documents]


@router.delete("/{knowledge_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除知识库及其文档")
async def delete_knowledge(
    knowledge_id: int,
    session: SessionDep,
    user: CurrentUser,
    _: WriteLimit,
) -> None:
    await KnowledgeService(session).delete(user.id, knowledge_id)
