"""Database bootstrap: create tables and seed the demo account."""

import logging

from sqlalchemy import inspect, text

# 显式导入每个模型子模块，确保 Base.metadata 上注册了全部表（create_all 需要）。
# 注意：app/models/__init__.py 已改为惰性导出（打破包 __init__ 的循环导入死锁），
# 所以不能再依赖 `from app import models` 的隐式副作用来注册表 —— 那会漏表。
from app.models import (  # noqa: F401
    character,
    character_knowledge,
    chat,
    document,
    knowledge,
    memory,
    usage_log,
    user,
)
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
#
# ⚠️ 类型必须跨方言安全：同一套 DDL 会同时跑在 SQLite 与 PostgreSQL 上。
# 线上事故：PostgreSQL 执行 "ADD COLUMN deleted_at DATETIME" 直接报
#   UndefinedObjectError: type "datetime" does not exist
# 因为 PG 根本没有 DATETIME 这个类型。SQLite 则把类型名当"亲和性"看，
# 几乎任意名字都能建列，所以本地测试全绿、上云才炸。
# 修复：时间列统一写 TIMESTAMP —— PostgreSQL 原生支持，SQLite 也接受。
# 其余类型逐个确认过，两种方言都合法：
#   VARCHAR(n) / TEXT / INTEGER 都是标准类型；
#   BOOLEAN 两者都认；默认值用 FALSE 而不是 0，
#   因为 PG 不接受布尔列 DEFAULT 0（SQLite 两者皆可）。
MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {"role": "VARCHAR(16) NOT NULL DEFAULT 'user'"},
    "conversations": {
        "summary": "TEXT NOT NULL DEFAULT ''",
        "summary_upto_id": "INTEGER NOT NULL DEFAULT 0",
    },
    "messages": {"embedding": "TEXT NOT NULL DEFAULT '[]'"},
    "knowledge_bases": {
        "parent_id": "INTEGER",
        # 公共库开关 + 回收站（软删除）时间。默认值用 FALSE 而不是 0：
        # SQLite 与 PostgreSQL 都认 FALSE，而 PG 不接受布尔列的 DEFAULT 0。
        "is_public": "BOOLEAN NOT NULL DEFAULT FALSE",
        # 时间列用 TIMESTAMP（原因见上方注释）：PG 不认 DATETIME。
        "deleted_at": "TIMESTAMP",
    },
    "documents": {"deleted_at": "TIMESTAMP"},
}


def _add_column_sql(table: str, column: str, ddl: str) -> str:
    """集中生成 ADD COLUMN 语句。

    单独抽出来是为了让测试能对"最终会执行的 SQL"做方言安全断言，
    而不是去猜字符串拼接的结果。
    """
    return "ALTER TABLE " + table + " ADD COLUMN " + column + " " + ddl


def _existing_columns_sqlite(sync_conn, table: str) -> set[str]:  # noqa: ANN001
    """SQLite：PRAGMA table_info 返回 (cid, name, type, notnull, dflt, pk)，取第 2 列。

    表名来自本模块的常量 MIGRATIONS，不存在注入风险；仍加引号以防未来出现
    需要转义的列名/表名。
    """
    rows = sync_conn.execute(text('PRAGMA table_info("' + table + '")')).fetchall()
    return {row[1] for row in rows}


def _existing_columns_postgresql(sync_conn, table: str) -> set[str]:  # noqa: ANN001
    """PostgreSQL：没有 PRAGMA，必须查 information_schema.columns。

    为什么不用 SQLAlchemy Inspector 兜底就够：显式查询更可控 ——
    它不受 reflection 缓存影响，且只认"当前 schema"（默认 public）。
    同名表若存在于其它 schema，不会让我们误判这一列已存在而跳过 ADD COLUMN。
    表名用绑定参数传入（PG 里标识符不能当参数，值是纯字符串，无注入风险）。
    """
    rows = sync_conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table_name AND table_schema = current_schema()"
        ),
        {"table_name": table},
    ).fetchall()
    return {row[0] for row in rows}


def _existing_columns(sync_conn, table: str) -> set[str]:  # noqa: ANN001
    """按方言选择"列是否已存在"的判断方式。

    方言分支的意义：线上很可能已经成功执行了前一条 ALTER（例如
    knowledge_bases.is_public），只是紧随其后的 deleted_at 因 DATETIME 失败。
    修复后重跑必须跳过已存在的列，否则会因 "column already exists" 二次爆炸。
    """
    dialect = sync_conn.dialect.name
    if dialect == "postgresql":
        return _existing_columns_postgresql(sync_conn, table)
    if dialect == "sqlite":
        return _existing_columns_sqlite(sync_conn, table)
    # 未知方言（将来若接入 MySQL 等）退回 SQLAlchemy Inspector，保证迁移不会因
    # 无法识别方言而中断启动。
    inspector = inspect(sync_conn)
    return {column["name"] for column in inspector.get_columns(table)}


def _sync_apply_migrations(sync_conn) -> list[str]:  # noqa: ANN001
    inspector = inspect(sync_conn)
    applied: list[str] = []
    for table, columns in MIGRATIONS.items():
        if not inspector.has_table(table):
            continue
        existing = _existing_columns(sync_conn, table)
        for name, ddl in columns.items():
            # 幂等关键：列已存在就跳过，绝不重复 ADD COLUMN。
            if name in existing:
                continue
            sync_conn.execute(text(_add_column_sql(table, name, ddl)))
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
