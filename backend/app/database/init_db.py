"""Database bootstrap: create tables and seed the demo account."""

import logging

from sqlalchemy import inspect, text

from app import models  # noqa: F401  (imports every model so metadata is complete)
from app.config.settings import settings
from app.core.security import hash_password
from app.database.backup import backup_sqlite_database
from app.database.base import Base
from app.database.session import SessionLocal, engine
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

# 轻量迁移：只允许 ADD COLUMN（新增列），绝不删列、删表或改类型。
# 这是为了保护已有数据 —— create_all 不会给已存在的表补列，
# 而直接删库重建会丢数据（本项目已发生过一次数据事故，绝不再犯）。
MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {"role": "VARCHAR(16) NOT NULL DEFAULT 'user'"},
    "conversations": {
        "summary": "TEXT NOT NULL DEFAULT ''",
        "summary_upto_id": "INTEGER NOT NULL DEFAULT 0",
    },
    "messages": {"embedding": "TEXT NOT NULL DEFAULT '[]'"},
    "knowledge_bases": {"parent_id": "INTEGER"},
}


def _sync_apply_migrations(sync_conn) -> list[str]:  # noqa: ANN001
    inspector = inspect(sync_conn)
    applied: list[str] = []
    for table, columns in MIGRATIONS.items():
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in existing:
                continue
            sync_conn.execute(text("ALTER TABLE " + table + " ADD COLUMN " + name + " " + ddl))
            applied.append(table + "." + name)
    return applied


async def init_db() -> None:
    # 先备份，再做任何结构变更 —— 迁移/测试出事都能回滚
    backup_sqlite_database()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        applied = await conn.run_sync(_sync_apply_migrations)
    if applied:
        logger.info("数据库结构已升级（仅新增列，不动已有数据）: %s", ", ".join(applied))
    await ensure_demo_user()


async def ensure_demo_user() -> None:
    async with SessionLocal() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_username(settings.demo_username)
        if existing is None:
            await repo.create(
                username=settings.demo_username,
                password_hash=hash_password(settings.demo_password),
                email="demo@ai-world.local",
            )
            await session.commit()
            logger.info("Demo user '%s' created", settings.demo_username)
