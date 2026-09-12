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

# libpq 专用参数，asyncpg 不认识，直接丢弃
_DROP_QUERY_KEYS = {"channel_binding", "target_session_attrs", "options"}


def normalize_database_url(url: str) -> str:
    """把平台给的 PostgreSQL 连接串规范成 asyncpg 可用的异步 URL。

    处理三个最常见的部署坑：
    1. postgres:// / postgresql://  ->  postgresql+asyncpg://（换异步驱动）
    2. ?sslmode=require            ->  ?ssl=require（asyncpg 用 ssl，不认 sslmode）
    3. 丢弃 libpq 专用参数（channel_binding 等），并默认补上 TLS
    """
    if not url:
        return url

    cleaned = url.strip().strip(chr(34)).strip(chr(39))

    for prefix in ("postgres://", "postgresql://"):
        if cleaned.startswith(prefix):
            cleaned = "postgresql+asyncpg://" + cleaned[len(prefix):]
            break

    is_async_pg = cleaned.startswith("postgresql+asyncpg://")
    has_ssl = False

    if "?" in cleaned:
        base, query = cleaned.split("?", 1)
        kept: list[str] = []
        for item in query.split("&"):
            if not item:
                continue
            key = item.split("=", 1)[0].lower()
            if key in _DROP_QUERY_KEYS:
                continue
            if key in {"sslmode", "ssl"}:
                value = item.split("=", 1)[1] if "=" in item else "require"
                if value.lower().startswith("verify"):
                    value = "verify-full"
                elif value.lower() in {"require", "prefer", "allow"}:
                    value = "require"
                kept.append("ssl=" + value)
                has_ssl = True
                continue
            kept.append(item)
        cleaned = base + ("?" + "&".join(kept) if kept else "")

    if is_async_pg and not has_ssl:
        cleaned += ("&" if "?" in cleaned else "?") + "ssl=require"

    return cleaned


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
    # 注意：deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用。
    # 稳定可选：deepseek-v4-flash（默认）/ deepseek-v4-pro / deepseek-v4-flash-vision-exp
    # 4.1 为限时内测（ID 形如 deepseek-v4.1-flash-expires-on-0910），到期即失效，
    # 官方明确不建议硬编码，故不作为默认值。
    deepseek_model: str = "deepseek-v4-flash"
    ai_timeout_seconds: float = 90.0
    ai_temperature: float = 0.7
    ai_max_tokens: int = 2048

    # ---- 上下文与记忆 ----
    history_limit: int = 12          # 每次带多少条历史消息
    history_char_budget: int = 6000  # 历史消息总字符预算（超出则从最旧的丢弃）
    memory_inject_limit: int = 5     # 注入多少条长期记忆

    # ---- 长记忆三件套 ----
    summary_enabled: bool = True           # 滚动摘要：滑出窗口的旧消息压缩成摘要
    history_retrieval_enabled: bool = True  # 历史向量检索：按相关度召回窗口外的历史
    history_retrieval_top_k: int = 3
    history_retrieval_scan: int = 200
    memory_auto_extract: bool = True        # 自动事实抽取
    memory_extract_every: int = 6           # 每 N 条消息触发一次抽取

    # ---- 外部世界接口（联网检索）----
    # 注意：检索在**应用所在网络**发起（部署在美国服务器 → 维基可达；
    # 部署到国内服务器 → 请改用 searxng / tavily / serper）
    search_enabled: bool = False
    search_providers: str = "wikipedia,duckduckgo"
    search_timeout_seconds: float = 8.0
    search_results_per_provider: int = 3
    search_max_results: int = 6
    search_api_key: str = ""
    search_base_url: str = ""

    # ---- 多 Agent RAG（拆解 / 查找 / 审查 / 整理）----
    multi_agent_enabled: bool = True
    multi_agent_max_rounds: int = 2   # 审查-补检的最大轮次（有界，防费用失控）
    multi_agent_top_k: int = 4        # 每个子问题检索多少条证据

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
    allowed_extensions: str = (
        ".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.pptx,.rtf,.odt,.ods,.odp,.html,.htm"
        ",.json,.xml,.yaml,.yml,.log,.sql,.csv"
    )
    max_upload_mb: int = 20

    # ---- CORS ----
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3080,http://127.0.0.1:3080"

    # ---- Demo account ----
    demo_username: str = "demo"
    demo_password: str = "demo123"

    # ---- 权限（Phase 2）：逗号分隔的管理员用户名 ----
    admin_usernames: str = ""

    # ---- 数据库自动备份（本地文件复制，零成本）----
    backup_enabled: bool = True
    backup_keep: int = 5
    backup_dir: str = ""  # 留空 = data/backups

    # ------------------------------------------------------------------ #
    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy URL. Set DATABASE_URL to migrate to PostgreSQL."""
        if self.database_url:
            return normalize_database_url(self.database_url)
        db_path = Path(self.data_dir) / "database.db"
        return "sqlite+aiosqlite:///" + db_path.as_posix()

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def allowed_extension_list(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_extensions.split(",") if item.strip()]

    @property
    def resolved_backup_dir(self) -> str:
        return self.backup_dir or str(Path(self.data_dir) / "backups")

    @property
    def search_provider_list(self) -> list[str]:
        return [item.strip() for item in self.search_providers.split(",") if item.strip()]

    @property
    def admin_username_list(self) -> list[str]:
        return [item.strip() for item in self.admin_usernames.split(",") if item.strip()]

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
