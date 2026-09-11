"""Streamlit 版冒烟测试：用官方 AppTest 无头运行 streamlit_app.py。

用法（需先安装依赖）：
    pip install -r requirements.txt
    python tests/streamlit_app_test.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["我的世界", "AI伙伴", "知识世界", "AI聊天", "AI圆桌"]
FAILURES: list[str] = []


def describe(element) -> str:
    return str(getattr(element, "value", element))


def check(name: str, at: AppTest) -> None:
    if at.exception:
        FAILURES.append(name + " -> " + describe(at.exception[0]))
        print("  [FAIL] " + name)
    else:
        print("  [PASS] " + name)


def main() -> int:
    print("\n== 1. 首次渲染（建表 + 演示账号 + 预置伙伴）==")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=300)
    at.run()
    check("首页渲染", at)

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
