"""启动时的数据库自动备份（本地文件复制，零成本、无需任何外部服务）。

目的：任何破坏性动作（结构迁移、误删、自动化测试）之前，
都先留一份可回滚的快照，并自动轮转保留最近 N 份。

说明：Streamlit Cloud 免费实例的文件系统是临时的，备份也会随实例重置；
      要跨实例长期保留数据，请把 DATABASE_URL 指向云数据库（Neon / Supabase 免费档）。
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)

PREFIX = "database-"
SUFFIX = ".db"


def backup_sqlite_database() -> Path | None:
    """把 SQLite 数据库复制到 backup 目录。失败只记录日志，绝不影响启动。"""
    if not settings.backup_enabled or not settings.is_sqlite:
        return None

    source = Path(settings.data_dir) / "database.db"
    if not source.exists() or source.stat().st_size == 0:
        return None

    target_dir = Path(settings.resolved_backup_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / (PREFIX + stamp + SUFFIX)
        if target.exists():
            target = target_dir / (
                PREFIX + stamp + "-" + str(datetime.now().microsecond).zfill(6) + SUFFIX
            )
        shutil.copy2(source, target)
        removed = _prune(target_dir, settings.backup_keep)
        logger.info(
            "数据库已自动备份: %s（保留最近 %s 份，清理 %s 份）",
            target.name,
            settings.backup_keep,
            removed,
        )
        return target
    except Exception:
        logger.exception("数据库备份失败（不影响启动）")
        return None


def _prune(directory: Path, keep: int) -> int:
    """只保留最近 keep 份备份，返回清理数量。"""
    keep = max(1, int(keep or 1))
    files = sorted(
        directory.glob(PREFIX + "*" + SUFFIX),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            logger.warning("清理旧备份失败: %s", old)
    return removed


def list_backups(limit: int = 20) -> list[dict]:
    """列出已有备份（供管理员查看/回滚参考）。"""
    directory = Path(settings.resolved_backup_dir)
    if not directory.exists():
        return []
    files = sorted(
        directory.glob(PREFIX + "*" + SUFFIX),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    return [
        {
            "name": item.name,
            "size_kb": round(item.stat().st_size / 1024, 1),
            "created_time": datetime.fromtimestamp(item.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        for item in files
    ]
