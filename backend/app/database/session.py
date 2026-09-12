"""Async database engine / session factory.

The whole data layer is async so the API never blocks on I/O. Swapping SQLite
for PostgreSQL only requires a different DATABASE_URL (see settings.py).
"""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    # 云 Postgres（Neon / Supabase）空闲后会挂起计算实例，
    # 定期回收连接 + pre_ping 可以避免拿到失效连接。
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
