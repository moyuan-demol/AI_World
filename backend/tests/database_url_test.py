"""DATABASE_URL 规范化测试（接入云 PostgreSQL 的关键一步）。

各平台给的连接串格式不同，直接丢给 asyncpg 会连不上：
- Neon / 通用 libpq 用 ?sslmode=require，asyncpg 只认 ?ssl=require
- 平台默认给 postgres:// 前缀，异步需要 postgresql+asyncpg://
- 有些平台附带 channel_binding 等 libpq 专用参数

用法：
    python tests/database_url_test.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import normalize_database_url  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, actual: str, expected: str) -> None:
    if actual == expected:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + "\n         实际: " + actual + "\n         期望: " + expected)


def main() -> int:
    print("== 1. Neon / Supabase 常见格式 ==")
    check(
        "postgres:// 前缀 -> 异步驱动 + TLS",
        normalize_database_url("postgres://u:p@ep-x.aws.neon.tech/neondb"),
        "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/neondb?ssl=require",
    )
    check(
        "sslmode=require -> ssl=require",
        normalize_database_url("postgresql://u:p@h/db?sslmode=require"),
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )
    check(
        "丢弃 libpq 专用参数",
        normalize_database_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require"),
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )
    check(
        "连接池主机（-pooler）原样保留",
        normalize_database_url("postgresql://u:p@ep-x-pooler.aws.neon.tech/neondb?sslmode=require"),
        "postgresql+asyncpg://u:p@ep-x-pooler.aws.neon.tech/neondb?ssl=require",
    )

    print("\n== 2. 幂等与边界 ==")
    check(
        "已是 asyncpg 格式则补 TLS",
        normalize_database_url("postgresql+asyncpg://u:p@h/db"),
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )
    check(
        "已带 ssl 不重复追加",
        normalize_database_url("postgresql+asyncpg://u:p@h/db?ssl=require"),
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )
    check(
        "两侧引号会被去掉",
        normalize_database_url('"postgresql://u:p@h/db"'),
        "postgresql+asyncpg://u:p@h/db?ssl=require",
    )
    check("空串原样返回", normalize_database_url(""), "")
    check(
        "SQLite URL 不受影响",
        normalize_database_url("sqlite+aiosqlite:///data/database.db"),
        "sqlite+aiosqlite:///data/database.db",
    )
    check(
        "verify-full 保留严格校验",
        normalize_database_url("postgresql://u:p@h/db?sslmode=verify-full"),
        "postgresql+asyncpg://u:p@h/db?ssl=verify-full",
    )

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("DATABASE_URL 规范化验证通过（可直接粘各平台给的连接串）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
