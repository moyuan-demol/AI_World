"""数据库层：惰性导出 Base 与 Session 工厂。"""

from __future__ import annotations

import importlib
from typing import Any

# 名称 -> 真正定义它的子模块。为什么不在这里直接 import：见 __getattr__。
_EXPORTS: dict[str, str] = {
    "Base": "app.database.base",
    "SessionLocal": "app.database.session",
    "engine": "app.database.session",
    "get_session": "app.database.session",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """按需导入并返回包级名字（PEP 562 惰性导出）。

# 为什么改成惰性导出（PEP 562）而不是在 __init__ 里急切 import：
#   只要包 __init__ 里出现 `from app.X.y import Z`，导入任意子模块
#   （例如 `import app.rag.embedding`）都会先完整执行包 __init__，把该包
#   所有子模块连同依赖一次性拉起来；而本项目 rag / repositories / services /
#   models / schemas 的子模块之间又互相引用，多个包的 __init__ 级联 eager
#   import 就形成环：线程 A 持包锁等子模块、线程 B 持子模块锁等包锁。
#   Streamlit（ScriptRunner + Tornado）在不同线程里首次导入应用模块时，
#   CPython 的 per-module 导入锁出现同线程重入，抛
#   `_frozen_importlib._DeadlockError`，线上应用直接无法启动（真实事故）。
#   惰性化后包 __init__ 只登记名字、零 import、零 I/O，锁瞬间释放，环被从根上
#   打断；真正 import 推迟到该名字第一次被访问时，且结果会缓存进包命名空间，
#   运行时行为与原来的急切导出完全一致（`from app.X import Z` 仍可用）。
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # 首次访问才真正 import；随后写入 globals()，后续访问直接命中、不再走这里。
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
