"""Application settings.

All configuration comes from environment variables (optionally loaded from a
.env file at the project root). Nothing security sensitive is hard coded:
DEEPSEEK_API_KEY and JWT_SECRET are read from the environment.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# AI_World/  (backend/app/config/settings.py -> parents[3])
BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Typed, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=(str(BASE_DIR / ".env"), str(BASE_DIR / "backend" / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "AI World API"
    app_env: str = "development"
    debug: bool = True

    # ---- Storage ----
    data_dir: str = str(BASE_DIR / "data")
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    # Empty -> SQLite file at data/database.db
    database_url: str = ""
    sql_echo: bool = False

    # ---- DeepSeek / LLM ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # 注意：deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用，
    # 请使用 deepseek-v4-flash（默认）/ deepseek-v4-pro / deepseek-v4-flash-vision-exp
    deepseek_model: str = "deepseek-v4-flash"
    ai_timeout_seconds: float = 90.0
    ai_temperature: float = 0.7
    ai_max_tokens: int = 2048

    # ---- 上下文与记忆 ----
    history_limit: int = 12          # 每次带多少条历史消息
    history_char_budget: int = 6000  # 历史消息总字符预算（超出则从最旧的丢弃）
    memory_inject_limit: int = 5     # 注入多少条长期记忆

    # ---- Embedding (local hashing by default; or any OpenAI compatible API) ----
    embedding_provider: str = "local"  # local | openai
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 512

    # ---- RAG ----
    chunk_size: int = 600
    chunk_overlap: int = 100
    retrieval_top_k: int = 4
    max_context_chars: int = 6000

    # ---- Auth ----
    jwt_secret: str = "ai-world-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    # ---- Uploads ----
    allowed_extensions: str = ".pdf,.txt,.md,.docx"
    max_upload_mb: int = 20

    # ---- CORS ----
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3080,http://127.0.0.1:3080"

    # ---- Demo account ----
    demo_username: str = "demo"
    demo_password: str = "demo123"

    # ------------------------------------------------------------------ #
    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy URL. Set DATABASE_URL to migrate to PostgreSQL."""
        if self.database_url:
            return self.database_url
        db_path = Path(self.data_dir) / "database.db"
        return "sqlite+aiosqlite:///" + db_path.as_posix()

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def allowed_extension_list(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_extensions.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def ai_configured(self) -> bool:
        return bool(self.deepseek_api_key.strip())

    def ensure_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
