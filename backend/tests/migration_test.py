"""数据库迁移测试：跨方言类型安全 + 幂等（列已存在则跳过）。

背景（真实线上事故）：
    PostgreSQL 启动时执行
        ALTER TABLE knowledge_bases ADD COLUMN deleted_at DATETIME
    直接抛 asyncpg.exceptions.UndefinedObjectError: type "datetime" does not exist
    —— PG 根本没有 DATETIME 类型，SQLite 却把类型名当"亲和性"看所以本地全绿。
    修复：MIGRATIONS 里的时间列统一改用 TIMESTAMP。

本测试离线、确定性，绝不连接真实数据库（尤其不连 Neon），也不使用任何凭据：
1. 纯文本断言：所有"生成出来的迁移 SQL"不含 DATETIME，时间列必须是 TIMESTAMP，
   且每一列的类型都在 SQLite / PostgreSQL 都合法的白名单内；
2. PostgreSQL 分支：用一个假连接断言它查询 information_schema.columns，
   并且表名通过绑定参数传入、按 current_schema() 过滤；
3. SQLite 真库：造一个"线上半迁移状态"的旧库（is_public 已存在、deleted_at 缺失），
   真实跑迁移 + init_db()，验证只补缺失列、且连续第二次执行幂等不报错。

用法：
    python tests/migration_test.py
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

# 数据安全：独立临时数据库 + 独立上传目录，绝不触碰真实 data/database.db。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_migration_test_"))
_DB_PATH = _TEST_DIR / "legacy.db"
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB_PATH.as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.database.init_db import (  # noqa: E402
    MIGRATIONS,
    _add_column_sql,
    _existing_columns,
    _existing_columns_postgresql,
    _existing_columns_sqlite,
    _sync_apply_migrations,
    init_db,
)
from app.database.session import engine  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

# 所有列类型都必须同时被 SQLite 与 PostgreSQL 接受。
ALLOWED_TYPES = {"VARCHAR", "TEXT", "INTEGER", "BOOLEAN", "TIMESTAMP"}

# 逐列确认类型：既防止将来有人再塞回 DATETIME，也防止新增列用错类型。
EXPECTED_TYPES = {
    ("users", "role"): "VARCHAR",
    ("conversations", "summary"): "TEXT",
    ("conversations", "summary_upto_id"): "INTEGER",
    ("messages", "embedding"): "TEXT",
    ("knowledge_bases", "parent_id"): "INTEGER",
    ("knowledge_bases", "is_public"): "BOOLEAN",
    ("knowledge_bases", "deleted_at"): "TIMESTAMP",
    ("documents", "deleted_at"): "TIMESTAMP",
}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


# 固定在一个后台事件循环上执行异步代码：
# async 引擎的连接池不能跨事件循环复用（否则报 Future attached to a different loop）。
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def run_async(coro):  # noqa: ANN001
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


async def _init_db() -> None:
    await init_db()


async def _apply_migrations_only() -> list[str]:
    """只跑迁移、不建表：用于捕获"这次到底 ADD 了哪些列"。"""
    async with engine.begin() as conn:
        return await conn.run_sync(_sync_apply_migrations)


def _sync_columns(table: str) -> set[str]:
    """用真实同步 SQLite 连接走一遍线上同一套列存在性判断函数。"""
    sync_engine = create_engine("sqlite:///" + _DB_PATH.as_posix())
    try:
        with sync_engine.connect() as conn:
            return _existing_columns(conn, table)
    finally:
        sync_engine.dispose()


def _pragma(table: str) -> dict[str, str]:
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("' + table + '")').fetchall()
        return {row[1]: (row[2] or "") for row in rows}
    finally:
        conn.close()


def _create_legacy_db() -> None:
    """造一个"线上半迁移状态"的旧库。

    关键点：knowledge_bases.is_public **已经存在**（线上第一条 ALTER 执行成功），
    但 deleted_at（以及其它列）缺失（紧随其后的 DATETIME 语句失败）。
    只有这种状态才能真实检验"修复后重跑必须跳过已存在列"。
    """
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username VARCHAR(64),
                password_hash VARCHAR(255),
                email VARCHAR(255),
                created_time TIMESTAMP
            );
            CREATE TABLE knowledge_bases (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                name VARCHAR(128),
                description TEXT,
                is_public BOOLEAN NOT NULL DEFAULT FALSE,
                created_time TIMESTAMP
            );
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                knowledge_id INTEGER,
                user_id INTEGER,
                filename VARCHAR(255),
                chunk_index INTEGER,
                content TEXT,
                embedding TEXT,
                created_time TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _type_token(statement: str) -> str:
    """从 'ALTER TABLE t ADD COLUMN c TYPE ...' 里取出 TYPE。"""
    return statement.split()[6].split("(")[0].upper()


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchall(self) -> list:
        return self._rows


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeConnection:
    """只记录 SQL 的假连接：让 PostgreSQL 分支可以在离线环境被真实调用。"""

    def __init__(self, dialect_name: str, rows: list) -> None:
        self.dialect = _FakeDialect(dialect_name)
        self._rows = rows
        self.statements: list[str] = []
        self.parameters: list = []

    def execute(self, statement, parameters=None):  # noqa: ANN001
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return _FakeResult(self._rows)


def section_one_sql_text() -> None:
    print("== 1. 迁移 SQL 跨方言类型安全（纯文本断言）==")
    statements = {
        (table, name): _add_column_sql(table, name, ddl)
        for table, columns in MIGRATIONS.items()
        for name, ddl in columns.items()
    }
    joined = " ".join(statements.values()).upper()

    check("所有迁移 SQL 都不出现 DATETIME", "DATETIME" not in joined, joined)
    check("迁移 SQL 至少有一处使用 TIMESTAMP", "TIMESTAMP" in joined, joined)

    missing = {key for key in EXPECTED_TYPES if key not in statements}
    check("没有未登记的迁移列（新增列必须显式确认类型）", not missing, str(missing))

    for key, expected in EXPECTED_TYPES.items():
        if key not in statements:
            continue
        actual = _type_token(statements[key])
        check(
            "{} 列 {} 类型为 {}".format(key[0], key[1], expected),
            actual == expected,
            "实际 " + actual + " / " + statements[key],
        )

    unknown = [
        _type_token(stmt) for stmt in statements.values() if _type_token(stmt) not in ALLOWED_TYPES
    ]
    check(
        "所有类型都在跨方言白名单 VARCHAR/TEXT/INTEGER/BOOLEAN/TIMESTAMP 内",
        not unknown,
        str(unknown),
    )

    # 关键的两条时间列（线上炸的就是它们）
    for table in ("knowledge_bases", "documents"):
        check(
            table + ".deleted_at 必须是 TIMESTAMP（PG 不认 DATETIME）",
            statements[(table, "deleted_at")].upper().endswith("TIMESTAMP"),
            statements[(table, "deleted_at")],
        )

    # BOOLEAN 默认值用 FALSE：PG 不接受布尔列 DEFAULT 0
    check(
        "is_public 用 BOOLEAN NOT NULL DEFAULT FALSE（PG/SQLite 都合法）",
        statements[("knowledge_bases", "is_public")].upper().endswith(
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
        statements[("knowledge_bases", "is_public")],
    )


def section_two_postgres_branch() -> None:
    print("\n== 2. PostgreSQL 列存在性检查走 information_schema.columns（假连接）==")

    conn = _FakeConnection("postgresql", [("id",), ("is_public",)])
    columns = _existing_columns(conn, "knowledge_bases")
    sql = " ".join(conn.statements)

    check("PostgreSQL 分支确实查询 information_schema.columns", "information_schema.columns" in sql, sql)
    check("PostgreSQL 分支按当前 schema 过滤", "table_schema" in sql and "current_schema()" in sql, sql)
    check(
        "PostgreSQL 分支用绑定参数传表名（不拼接标识符）",
        conn.parameters and conn.parameters[0] == {"table_name": "knowledge_bases"},
        str(conn.parameters),
    )
    check(
        "PostgreSQL 分支返回的列名集合正确",
        columns == {"id", "is_public"},
        str(columns),
    )

    direct = _FakeConnection("postgresql", [("deleted_at",)])
    direct_columns = _existing_columns_postgresql(direct, "knowledge_bases")
    check(
        "直接调用 PG 检查函数同样命中 information_schema",
        "information_schema.columns" in " ".join(direct.statements)
        and direct_columns == {"deleted_at"},
        str(direct_columns),
    )

    # 方言派发：postgresql -> information_schema；sqlite -> PRAGMA
    pg = _FakeConnection("postgresql", [("id",)])
    _existing_columns(pg, "users")
    sqlite = _FakeConnection("sqlite", [(0, "id", "INTEGER", 0, None, 1)])
    _existing_columns(sqlite, "users")
    check(
        "方言派发正确：PG 查 information_schema / SQLite 查 PRAGMA",
        "information_schema.columns" in " ".join(pg.statements)
        and "PRAGMA TABLE_INFO" in " ".join(sqlite.statements).upper(),
        "pg=" + str(pg.statements) + " sqlite=" + str(sqlite.statements),
    )


def section_three_sqlite_real() -> None:
    print("\n== 3. SQLite 真库：半迁移状态补列 + 连续两次 init_db() 幂等 ==")

    legacy_kb = _sync_columns("knowledge_bases")
    check(
        "旧库处于半迁移状态：is_public 已存在、deleted_at 缺失",
        "is_public" in legacy_kb and "deleted_at" not in legacy_kb,
        str(sorted(legacy_kb)),
    )
    check("旧库 users 还没有 role 列", "role" not in _sync_columns("users"), str(_sync_columns("users")))

    pragma_engine = create_engine("sqlite:///" + _DB_PATH.as_posix())
    try:
        with pragma_engine.connect() as conn:
            pragma_columns = _existing_columns_sqlite(conn, "knowledge_bases")
    finally:
        pragma_engine.dispose()
    check(
        "SQLite 分支（PRAGMA）能读到真实列",
        pragma_columns >= {"id", "is_public"},
        str(pragma_columns),
    )

    # 第一次：只跑迁移，捕获实际 ADD 了哪些列
    applied = run_async(_apply_migrations_only())
    check(
        "只补缺失列，已存在的 is_public 被跳过",
        set(applied)
        == {
            "users.role",
            "knowledge_bases.parent_id",
            "knowledge_bases.deleted_at",
            "documents.deleted_at",
        },
        str(applied),
    )
    check("applied 列表里没有 is_public（幂等跳过）", "knowledge_bases.is_public" not in applied, str(applied))

    # 第二次：完整 init_db()（建缺失表 + 迁移 + 演示账号）
    run_async(_init_db())

    kb = _pragma("knowledge_bases")
    doc = _pragma("documents")
    check("knowledge_bases.is_public 仍在", "is_public" in kb, str(sorted(kb)))
    check("knowledge_bases.deleted_at 已补上", "deleted_at" in kb, str(sorted(kb)))
    check("documents.deleted_at 已补上", "deleted_at" in doc, str(sorted(doc)))
    check("users.role 已补上", "role" in _pragma("users"), str(sorted(_pragma("users"))))
    check(
        "补上的 deleted_at 列类型是 TIMESTAMP（不是 DATETIME）",
        kb.get("deleted_at", "").upper().startswith("TIMESTAMP")
        and doc.get("deleted_at", "").upper().startswith("TIMESTAMP"),
        "kb=" + kb.get("deleted_at", "") + " doc=" + doc.get("deleted_at", ""),
    )

    # 幂等：再跑一次迁移必须什么都不做
    second = run_async(_apply_migrations_only())
    check("第二次迁移不追加任何列（全部跳过）", second == [], str(second))

    # 幂等：连续第二次 init_db() 不能抛异常
    error = ""
    try:
        run_async(_init_db())
    except Exception as exc:  # noqa: BLE001
        error = type(exc).__name__ + ": " + str(exc)
    check("连续第二次 init_db() 不抛异常（幂等）", not error, error)

    # 演示账号只创建一次（ensure_demo_user 的幂等）
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE username = 'demo'").fetchone()[0]
    finally:
        conn.close()
    check("演示账号只创建一次（仍为 1 个）", count == 1, str(count))


def main() -> int:
    _create_legacy_db()
    section_one_sql_text()
    section_two_postgres_branch()
    section_three_sqlite_real()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("迁移 SQL 跨方言安全 + 幂等（列已存在则跳过）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
