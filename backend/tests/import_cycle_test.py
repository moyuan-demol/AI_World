"""导入环 / 导入死锁回归测试（离线、确定性、不联网、不连真实数据库）。

背景（真实线上事故，Streamlit Cloud 启动即挂）：
    File ".../streamlit_app.py", line 120, in <module>
        from app.rag.embedding import EmbeddingConfig
    File ".../app/rag/__init__.py", line 3, in <module>
        from app.rag.rag_service import RagService
    File ".../app/rag/rag_service.py", line 13, in <module>
        from app.rag.retriever import RetrievedChunk, Retriever
    File ".../app/repositories/__init__.py", line 9, in <module>
        ...
    _frozen_importlib._DeadlockError

根因：包 __init__.py 在导入时「急切」re-export 子模块，形成
    app.rag -> app.rag.rag_service -> app.rag.retriever -> app.repositories
    -> app.repositories.* -> ... 的导入环；而 Streamlit（ScriptRunner +
    Tornado）会在多个线程里首次导入应用模块，CPython 的 per-module 导入锁
    出现同线程重入，抛 _DeadlockError，整个应用无法启动。

本测试分三层，逐层加严：
1. 静态层：app 下所有包 __init__.py 不得在导入时急切 re-export 子模块。
   唯一例外是 app/api/routes/__init__.py —— 它必须聚合路由才能给 main 用，
   且是纯单向扇出（路由模块只 import app.api.deps，不会反向 import 本包）。
2. 进程层：用 subprocess 起「全新解释器」逐个导入高风险入口，断言退出码 0、
   stdout 含 ok，且不出现 ImportError / partially initialized module /
   DeadlockError 等关键字；并验证惰性导出对 from app.X import Y 仍兼容。
3. 并发层：多线程同时首次导入 app.rag.embedding 与 app.repositories 等，
   用 join(timeout) 断言没有死锁。

用法：
    python tests/import_cycle_test.py
"""

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# 断言文案带中文，Windows 控制台默认 GBK：不切 UTF-8 会在 print 时崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"

# 数据安全：新解释器里也绝不碰真实数据库 / 上传目录，全部指向临时目录。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_import_cycle_"))
_CHILD_ENV = dict(os.environ)
_CHILD_ENV.update(
    {
        "AI_WORLD_BACKEND_DIR": str(BACKEND_DIR),
        "DATA_DIR": str(_TEST_DIR),
        "UPLOAD_DIR": str(_TEST_DIR / "uploads"),
        "DATABASE_URL": "sqlite+aiosqlite:///" + (_TEST_DIR / "import_cycle.db").as_posix(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
)

# 事故链路 + 各包公共入口，都要能在全新解释器里单独导入。
IMPORT_MODULES = [
    "app.rag.embedding",
    "app.rag",
    "app.repositories",
    "app.services",
    "app.models",
    "app.schemas",
    "app.main",
]

# 出现任一关键字即判定「环 / 死锁仍在」。
BAD_MARKERS = (
    "ImportError",
    "ModuleNotFoundError",
    "partially initialized module",
    "circular import",
    "DeadlockError",
    "cannot import name",
)

# 允许在 __init__ 里急切聚合的包：纯单向、且运行时必须的 router 聚合。
EAGER_ALLOWED = {APP_DIR / "api" / "routes" / "__init__.py"}

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


def _run_python(snippet: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """用全新解释器执行 snippet（子进程隔离，确保是真正的首次导入）。"""
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(BACKEND_DIR),
        env=_CHILD_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _combined(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _bad_marker(text: str) -> str:
    for marker in BAD_MARKERS:
        if marker in text:
            return marker
    return ""


def _tail(text: str, limit: int = 400) -> str:
    text = (text or "").strip().replace("\r", "")
    return text[-limit:]


def test_static_no_eager_reexport() -> None:
    """静态层：确定性拦截「包 __init__ 急切 re-export」这一根因回归。"""
    print("\n== 1. 静态：包 __init__ 不得急切 re-export 子模块 ==")
    offenders: list[str] = []
    for init in sorted(APP_DIR.rglob("__init__.py")):
        if "__pycache__" in init.parts or init in EAGER_ALLOWED:
            continue
        tree = ast.parse(init.read_text(encoding="utf-8"))
        for node in tree.body:  # 只看模块顶层 = 导入时一定执行
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
                offenders.append(
                    str(init.relative_to(BACKEND_DIR)) + ":" + str(node.lineno) + " from " + node.module
                )
    check(
        "各包 __init__ 均为惰性，无急切 re-export",
        not offenders,
        "; ".join(offenders),
    )


def test_fresh_interpreter_imports() -> None:
    """进程层：逐个用全新解释器导入，退出码 0 + stdout 含 ok + 无环/死锁关键字。"""
    print("\n== 2. 全新解释器逐个导入（事故链路入口） ==")
    for module in IMPORT_MODULES:
        snippet = (
            "import os, sys\n"
            "sys.path.insert(0, os.environ['AI_WORLD_BACKEND_DIR'])\n"
            "import " + module + "\n"
            "print('ok')\n"
        )
        try:
            proc = _run_python(snippet)
        except subprocess.TimeoutExpired:
            check("新解释器导入 " + module, False, "子进程超时（疑似导入死锁）")
            continue
        marker = _bad_marker(_combined(proc))
        ok = proc.returncode == 0 and "ok" in (proc.stdout or "") and not marker
        detail = (
            "exit=" + str(proc.returncode)
            + " bad=" + repr(marker)
            + " out=" + repr(_tail(proc.stdout, 200))
            + " err=" + repr(_tail(proc.stderr, 300))
        )
        check("新解释器导入 " + module + " 成功且无环/死锁", ok, detail)

    # 再确认：真正会爆炸的那种「先导入 embedding，再导入 repositories」顺序也安全。
    order_snippet = (
        "import os, sys\n"
        "sys.path.insert(0, os.environ['AI_WORLD_BACKEND_DIR'])\n"
        "import app.rag.embedding\n"
        "import app.repositories\n"
        "import app.services\n"
        "import app.rag\n"
        "print('ok')\n"
    )
    try:
        proc = _run_python(order_snippet)
        marker = _bad_marker(_combined(proc))
        ok = proc.returncode == 0 and "ok" in (proc.stdout or "") and not marker
        check(
            "线上同序：embedding -> repositories -> services -> rag",
            ok,
            "exit=" + str(proc.returncode) + " bad=" + repr(marker) + " err=" + repr(_tail(proc.stderr, 300)),
        )
    except subprocess.TimeoutExpired:
        check("线上同序：embedding -> repositories -> services -> rag", False, "子进程超时")


def test_lazy_reexports_still_work() -> None:
    """兼容层：惰性导出后 from app.X import Y 必须与原来完全一致。"""
    print("\n== 3. 惰性导出向后兼容（from app.X import Y 仍可用） ==")
    snippet = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, os.environ["AI_WORLD_BACKEND_DIR"])

        from app.models import Character, KnowledgeBase, Conversation, Message
        from app.repositories import DocumentRepository, KnowledgeRepository, UserRepository
        from app.services import CharacterService, ChatService, AdminService
        from app.schemas import ChatRequest, CharacterOut
        from app.rag import RagService, Retriever, RetrievedChunk, embed_texts
        from app.ai import AIClient, DEFAULT_ROUNDTABLE_AGENTS
        from app.core import ServiceError, hash_password
        from app.database import Base, SessionLocal, engine, get_session
        from app.config import settings, Settings, get_settings
        from app.tools import WebSearchTool, SearchResult
        from app.api import api_router
        print("ok")
        """
    )
    try:
        proc = _run_python(snippet)
        marker = _bad_marker(_combined(proc))
        ok = proc.returncode == 0 and "ok" in (proc.stdout or "") and not marker
        check(
            "包级 re-export 惰性兼容",
            ok,
            "exit=" + str(proc.returncode) + " bad=" + repr(marker) + " err=" + repr(_tail(proc.stderr, 400)),
        )
    except subprocess.TimeoutExpired:
        check("包级 re-export 惰性兼容", False, "子进程超时")


# 多线程同时首次导入：原事故正是这里抛 _DeadlockError。
# 线程设为 daemon，万一真的卡死也能让子进程按 join 超时判定并退出，不会吊死。
THREADED_SNIPPET = textwrap.dedent(
    """
    import importlib, os, sys, threading

    sys.path.insert(0, os.environ["AI_WORLD_BACKEND_DIR"])

    MODULES = [
        "app.rag.embedding",
        "app.repositories",
        "app.rag",
        "app.services",
        "app.models",
    ]
    errors = []
    barrier = threading.Barrier(len(MODULES))

    def worker(name):
        try:
            barrier.wait(timeout=30)
            importlib.import_module(name)
        except BaseException as exc:
            errors.append(name + ": " + type(exc).__name__ + ": " + str(exc))

    threads = [threading.Thread(target=worker, args=(m,), daemon=True) for m in MODULES]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    stuck = [thread.name for thread in threads if thread.is_alive()]
    if errors or stuck:
        print("FAIL errors=" + repr(errors) + " stuck=" + repr(stuck))
        sys.exit(1)
    print("ok")
    """
)


def test_concurrent_import_no_deadlock() -> None:
    """并发层：多线程同时首次导入，join(timeout) 断言无死锁。"""
    print("\n== 4. 多线程并发导入冒烟（无死锁） ==")
    for attempt in range(1, 4):
        try:
            proc = _run_python(THREADED_SNIPPET, timeout=90)
        except subprocess.TimeoutExpired:
            check("并发导入第 " + str(attempt) + " 轮", False, "子进程超时（疑似死锁）")
            continue
        marker = _bad_marker(_combined(proc))
        ok = proc.returncode == 0 and "ok" in (proc.stdout or "") and not marker
        check(
            "并发导入第 " + str(attempt) + " 轮无死锁",
            ok,
            "exit=" + str(proc.returncode) + " bad=" + repr(marker) + " out=" + repr(_tail(proc.stdout, 300)),
        )


def main() -> int:
    test_static_no_eager_reexport()
    test_fresh_interpreter_imports()
    test_lazy_reexports_still_work()
    test_concurrent_import_no_deadlock()

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        print("Failed checks:")
        for item in FAILED:
            print("  - " + item)
        return 1
    print("导入环 / 导入死锁回归验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
