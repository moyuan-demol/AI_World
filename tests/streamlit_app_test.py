"""Streamlit 版冒烟测试：用官方 AppTest 无头运行 streamlit_app.py。

用法（需先安装依赖）：
    pip install -r requirements.txt
    python tests/streamlit_app_test.py
"""

import os
import sys
import tempfile
from pathlib import Path

# 断言文案里带 emoji（「📊 用量统计」），而 Windows 控制台默认是 GBK：
# 不切 UTF-8 会在 print 时抛 UnicodeEncodeError，测试还没跑完就崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 数据安全：测试必须使用独立的临时数据库，绝不能碰 data/database.db（真实数据）。
# 必须在应用读取配置之前设置环境变量。
_TEST_DIR = Path(tempfile.mkdtemp(prefix="ai_world_streamlit_test_"))
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TEST_DIR / "test.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["我的世界", "AI伙伴", "知识世界", "AI聊天", "AI圆桌"]
USAGE_PAGE = "📊 用量统计"
RECYCLE_PAGE = "🗑 回收站"
FAILURES: list[str] = []

# 与 streamlit_app.PAGE_SLUG 对应的期望值：第 9 节断言 ?page= 是否同步到位。
# 这里独立写一份（不 import streamlit_app），避免在测试进程里触发应用模块级副作用。
NAV_SLUGS = {
    "我的世界": "home",
    "AI伙伴": "characters",
    "知识世界": "knowledge",
    "AI聊天": "chat",
    "AI圆桌": "roundtable",
    RECYCLE_PAGE: "recycle",
    USAGE_PAGE: "usage",
}

# 模拟"普通访客被强行带到站长页"：绕过菜单直接调用页面函数。
# streamlit_app 底部有 __main__ 守卫，import 不会顺带执行整个 main()。
FORCE_USAGE_SCRIPT = """
import sys

sys.path.insert(0, r"{root}")

import streamlit as st

st.session_state["is_admin"] = False

import streamlit_app as app  # noqa: E402

app.page_usage(1)
""".format(root=str(ROOT))


def describe(element) -> str:
    return str(getattr(element, "value", element))


def check(name: str, at: AppTest) -> None:
    if at.exception:
        detail = describe(at.exception[0])
        FAILURES.append(name + " -> " + detail)
        print("  [FAIL] " + name + " -> " + detail[:180])
    else:
        print("  [PASS] " + name)


def expect(name: str, condition: bool, detail: str = "") -> None:
    """与 check 不同：这里断言的是一个布尔条件（AppTest 无异常不等于行为正确）。"""
    if condition:
        print("  [PASS] " + name)
    else:
        FAILURES.append(name + (" -> " + detail if detail else ""))
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


def main() -> int:
    print("\n== 1. 首次渲染（建表 + 演示账号 + 预置伙伴）==")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=300)
    at.run()
    check("未登录时显示登录页", at)

    # 现在需要登录才能进入（普通访客：临时库里 demo 用户 id=1，未标记站长）
    at.session_state["uid"] = 1
    at.session_state["uname"] = "demo"
    at.run()
    check("登录后首页渲染", at)

    print("\n== 2. 各页面渲染 ==")
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        check("页面：" + page, at)

    print("\n== 3. AI 伙伴创建 ==")
    at.sidebar.radio[0].set_value("AI伙伴").run()
    inputs = [item for item in at.text_input if item.label.startswith("名称")]
    if inputs:
        inputs[0].set_value("测试助手")
        forms = at.button
        submitted = False
        for button in forms:
            try:
                if button.label == "创建":
                    button.click().run()
                    submitted = True
                    break
            except Exception:
                continue
        check("创建 AI 伙伴" + ("" if submitted else "（未找到提交按钮）"), at)
    else:
        print("  [SKIP] 未找到名称输入框")

    print("\n== 4. AI 聊天（离线模式也能跑通）==")
    at.sidebar.radio[0].set_value("AI聊天").run()
    check("聊天页渲染", at)
    if at.chat_input:
        at.chat_input[0].set_value("医疗 AI 的技术架构和合规风险是什么？").run()
        check("发送消息并得到回答", at)
        assistant = [item for item in at.chat_message if item.name == "assistant"]
        if assistant:
            body = " ".join(str(part) for part in assistant[-1].markdown)
            print("      回答预览: " + body[:120].replace("\n", " "))
        else:
            FAILURES.append("没有渲染出 assistant 消息")
            print("  [FAIL] assistant 消息未渲染")
    else:
        print("  [SKIP] 未找到 chat_input")

    print("\n== 5. AI 圆桌 ==")
    at.sidebar.radio[0].set_value("AI圆桌").run()
    text_areas = [item for item in at.text_area if item.label.startswith("讨论议题")]
    if text_areas:
        text_areas[0].set_value("如何开发一款医疗 AI 产品？")
        clicked = False
        for button in at.button:
            if button.label == "发起圆桌讨论":
                button.click().run()
                clicked = True
                break
        check("发起圆桌讨论" + ("" if clicked else "（未找到按钮）"), at)
    else:
        print("  [SKIP] 未找到议题输入框")

    print("\n== 6a. 自愈：只删掉部分预置角色也要补回来 ==")
    at.sidebar.radio[0].set_value("AI伙伴").run()
    total_before = len([item for item in at.button if item.label == "删除"])
    deleted_partial = 0
    for _ in range(max(1, total_before - 1)):
        remaining = [item for item in at.button if item.label == "删除"]
        if not remaining:
            break
        remaining[0].click().run()
        deleted_partial += 1
    partial_markdown = " ".join(str(getattr(item, "value", "")) for item in at.markdown)
    restored = sum(1 for name in ["张医生", "李工", "王顾问"] if "#### " + name in partial_markdown)
    if restored == 3:
        print("  [PASS] 删掉 " + str(deleted_partial) + " 个后，3 个预置角色自动补齐")
    else:
        FAILURES.append("部分删除后未补齐：只剩 " + str(restored) + " 个")
        print("  [FAIL] 部分删除后未补齐：只剩 " + str(restored) + " 个")
    check("部分自愈后页面无异常", at)

    print("\n== 6b. 自愈：把 AI 伙伴全删光后应自动恢复 ==")
    at.sidebar.radio[0].set_value("AI伙伴").run()
    delete_buttons = [item for item in at.button if item.label == "删除"]
    total = len(delete_buttons)
    print("      当前角色数: " + str(total))
    for _ in range(total):
        remaining = [item for item in at.button if item.label == "删除"]
        if not remaining:
            break
        remaining[0].click().run()
    rendered = " ".join(str(getattr(item, "value", "")) for item in at.markdown)
    if "张医生" in rendered and "李工" in rendered:
        print("  [PASS] 角色被删空后自动恢复预置角色")
    else:
        FAILURES.append("自愈失败：预置角色未恢复")
        print("  [FAIL] 自愈失败：预置角色未恢复")
    check("自愈后页面无异常", at)

    print("\n== 7. 站长功能改为账号制：普通访客完全看不到 ==")
    # 7a 普通用户（session_state 里没有 is_admin）
    normal_options = list(at.sidebar.radio[0].options)
    expect(
        "普通用户(is_admin 缺省)左侧菜单不含「" + USAGE_PAGE + "」",
        USAGE_PAGE not in normal_options,
        str(normal_options),
    )
    at.session_state["is_admin"] = False
    at.run()
    normal_options = list(at.sidebar.radio[0].options)
    expect(
        "普通用户(is_admin=False)左侧菜单不含「" + USAGE_PAGE + "」",
        USAGE_PAGE not in normal_options,
        str(normal_options),
    )

    # 7b 强行切到该页（绕过菜单，直接调用页面函数）：只应看到提示，不渲染数据
    forced = AppTest.from_string(FORCE_USAGE_SCRIPT, default_timeout=300).run()
    if forced.exception:
        detail = describe(forced.exception[0])
        FAILURES.append("非管理员强行进入站长页抛异常 -> " + detail)
        print("  [FAIL] 非管理员强行进入站长页抛异常 -> " + detail[:180])
    else:
        warning_text = " ".join(str(getattr(item, "value", "")) for item in forced.warning)
        expect(
            "非管理员强行进入只看到「仅站长账号可见」提示",
            "仅站长账号可见" in warning_text,
            warning_text,
        )
        metric_labels = [str(getattr(item, "label", "")) for item in forced.metric]
        expect(
            "非管理员强行进入不渲染任何用量数据",
            not forced.metric,
            str(metric_labels),
        )

    print("\n== 8. 站长账号（is_admin=True）可见且可正常渲染 ==")
    at.session_state["is_admin"] = True
    at.run()
    admin_options = list(at.sidebar.radio[0].options)
    expect(
        "站长左侧菜单包含「" + USAGE_PAGE + "」",
        USAGE_PAGE in admin_options,
        str(admin_options),
    )
    at.sidebar.radio[0].set_value(USAGE_PAGE).run()
    check("站长页渲染无异常", at)
    metric_labels = [str(getattr(item, "label", "")) for item in at.metric]
    expect("站长页渲染出用量指标「调用次数」", "调用次数" in metric_labels, str(metric_labels))

    print("\n== 9. 导航单击即生效 ==")
    # 复现用户反馈的"每次切换页面都要点两次"。
    # 根因：导航 radio 没有显式 key 时，Streamlit 会把 index 也编进控件 ID；
    # 而 index 由 URL 的 ?page= 推导。用户点一下 -> 本次运行控件值已经变了，
    # 但 URL 还停在旧页 -> index 仍是旧页 -> 控件 ID 变化 -> Streamlit 把它当成
    # "新控件"，刚点的值被丢弃，页面看起来没反应；等 URL 更新后点第二次才生效。
    # 因此这里必须验证"单击 + 只 run 一次"就切换到位，且每个导航项都单独验。
    # 注意：刻意不在两次单击之间插额外的 run 去"复位到默认页"——多出的那次 run
    # 恰好会让滞后的控件 ID 对齐一次，反而掩盖真实 bug（修复前实测：插入 run 后
    # 全部假通过）。循环开始时已经先 at.run() 停在默认页，符合"先到默认页"的意图。
    nav_at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=300)
    nav_at.run()
    nav_at.session_state["uid"] = 1
    nav_at.session_state["uname"] = "demo"
    nav_at.session_state["is_admin"] = True
    nav_at.run()  # 先 at.run() 到默认页（我的世界）
    check("导航测试初始渲染（默认页）", nav_at)

    def nav_evidence(target_at: AppTest) -> dict:
        """集中取出各页用于断言的"独特关键词"，失败时可直接看出实际渲染了什么。"""
        return {
            "titles": [str(getattr(item, "value", item)) for item in target_at.title],
            "expander_labels": [str(getattr(item, "label", "")) for item in target_at.expander],
            "button_labels": [str(getattr(item, "label", "")) for item in target_at.button],
            "metric_labels": [str(getattr(item, "label", "")) for item in target_at.metric],
            "chat_placeholders": [
                str(getattr(item, "placeholder", "")) for item in target_at.chat_input
            ],
        }

    def is_on_page(label: str, evidence: dict) -> bool:
        titles = evidence["titles"]
        if label == "我的世界":
            return any("欢迎进入 AI World" in item for item in titles)
        if label == "AI伙伴":
            return any(item == "AI 伙伴" for item in titles) and any(
                "新建 AI 伙伴" in item for item in evidence["expander_labels"]
            )
        if label == "知识世界":
            return any(item == "知识世界" for item in titles)
        if label == "AI聊天":
            return any(item == "AI 聊天" for item in titles) and any(
                "输入你的问题" in item for item in evidence["chat_placeholders"]
            )
        if label == "AI圆桌":
            return any(item == "AI 圆桌" for item in titles) and any(
                item == "发起圆桌讨论" for item in evidence["button_labels"]
            )
        if label == RECYCLE_PAGE:
            return any(item == RECYCLE_PAGE for item in titles)
        if label == USAGE_PAGE:
            return any("用量统计" in item for item in titles) and any(
                item == "调用次数" for item in evidence["metric_labels"]
            )
        return False

    def url_page(target_at: AppTest) -> str:
        raw = target_at.query_params.get("page", "")
        if isinstance(raw, (list, tuple)):
            return str(raw[0]) if raw else ""
        return str(raw or "")

    for nav_label in NAV_SLUGS:
        nav_at.sidebar.radio[0].set_value(nav_label).run()  # 单击一次，只 run 一次
        evidence = nav_evidence(nav_at)
        expect(
            "单击「" + nav_label + "」一次即渲染目标页",
            is_on_page(nav_label, evidence),
            str(evidence),
        )
        expect(
            "单击「" + nav_label + "」后 ?page= 同步为 " + NAV_SLUGS[nav_label],
            url_page(nav_at) == NAV_SLUGS[nav_label],
            "实际 ?page=" + url_page(nav_at),
        )

    print("\n" + "=" * 56)
    if FAILURES:
        print("FAILED: " + str(len(FAILURES)))
        for item in FAILURES:
            print("  - " + item)
        return 1
    print("Streamlit 版全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
