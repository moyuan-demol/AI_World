"""Async database engine / session factory.

The whole data layer is async so the API never blocks on I/O. Swapping SQLite
for PostgreSQL only requires a different DATABASE_URL (see settings.py).
"""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings

connect_args: dict = {}
engine_kwargs: dict = {
    "echo": settings.sql_echo,
    "future": True,
    "pool_pre_ping": True,
}
if settings.is_sqlite:
    connect_args = {"check_same_thread": False}
else:
    # 云 Postgres（Neon / Supabase）：
    # 1) 用 NullPool：连接不跨请求复用，从根本上杜绝
    #    "Future attached to a different loop"（连接绑在旧事件循环上）；
    #    服务端有连接池（Neon -pooler）负责复用，开销可接受。
    # 2) pool_recycle 仅对复用型池有意义，这里保留以防将来改回队列池。
    engine_kwargs["poolclass"] = NullPool
    engine_kwargs["pool_recycle"] = 300

engine = create_async_engine(
    settings.sqlalchemy_url,
    connect_args=connect_args,
    **engine_kwargs,
)

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        """SQLite disables FK enforcement by default; turn it on per connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request scoped session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
