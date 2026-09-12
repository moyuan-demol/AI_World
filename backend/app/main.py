"""AI World FastAPI application entry point.

Layering: API (routes) -> Service -> Repository -> Database.
Routes never touch SQLAlchemy queries; services never import FastAPI.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.config.settings import settings
from app.core.errors import ServiceError
from app.database.init_db import init_db
from app.database.session import SessionLocal
from app.services.demo_seed import ensure_public_demo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ai_world")

DEFAULT_JWT_SECRET = "ai-world-dev-secret-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 公共示例库：幂等种子数据（已存在则跳过），失败也绝不影响服务启动
    async with SessionLocal() as session:
        try:
            await ensure_public_demo(session)
        except Exception:  # noqa: BLE001
            logger.exception("公共示例库初始化失败（不影响服务启动）")
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        logger.warning("安全提示：JWT_SECRET 仍为默认值，正式部署前请在 .env 中更换为随机字符串。")
    if not settings.ai_configured:
        logger.warning("未配置 DEEPSEEK_API_KEY：/api/chat 与 /api/roundtable 将返回离线演示回答。")
    logger.info("AI World API ready. Database: %s", settings.sqlalchemy_url)
    yield


app = FastAPI(
    title=settings.app_name,
    description="AI World - 个人/企业级 AI 智能空间系统 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ServiceError)
async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "请求参数校验失败",
            "code": "validation_error",
            "errors": jsonable_encoder(exc.errors()[:5]),
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to the client."""
    logger.exception("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "code": "internal_error"},
    )


app.include_router(api_router, prefix="/api")


@app.get("/api/health", tags=["system"], summary="健康检查")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": "1.0.0"}


@app.get("/api/meta", tags=["system"], summary="运行环境信息")
async def meta() -> dict:
    return {
        "app": settings.app_name,
        "version": "1.0.0",
        "ai_configured": settings.ai_configured,
        "ai_model": settings.deepseek_model if settings.ai_configured else "offline-demo",
        "embedding_provider": settings.embedding_provider,
        "embedding_dim": settings.embedding_dim,
        "database": "sqlite" if settings.is_sqlite else "external",
        "allowed_extensions": settings.allowed_extension_list,
        "max_upload_mb": settings.max_upload_mb,
    }


@app.get("/", tags=["system"], summary="服务入口")
async def root() -> dict:
    return {"name": "AI World API", "docs": "/docs", "health": "/api/health"}
