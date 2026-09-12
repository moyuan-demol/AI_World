"""离线模式（未配置 API Key）回答呈现测试（纯函数、离线、确定性、不碰数据库）。

回归背景（用户真实反馈）：
    离线兜底把检索到的片段原样摊开，每块只显示开头 160 字，而且**每行都重复完整
    的文件名**（示例文件名长达 60 字），看起来"一坨"、根本读不下去；
    但检索本身是对的 —— 包含作者行的片段确实在列表里。

本测试锁定改写后的呈现行为（全部为确定性断言，不含空断言）：
1. 相关句提取：只展示与问题实词重合度最高的 1~2 句，无关句（收稿日期/基金项目）被丢弃；
2. 按文档分组：同一个文件名在输出里只出现一次；
3. 组内去重：同一句在多个切片里出现也只展示一次；
4. 无相关句的块回退成开头 120 字摘要，不整段搬运；
5. 相关句被优选出：作者行出现在正文片段之前；
6. 无片段时保留角色自我介绍；
7. 元信息伪切片（document_id 为负）正常参与分组与展示。

用法：
    python tests/offline_render_test.py
"""

import sys
from pathlib import Path

# 本测试会打印含 📄 的离线回答，而 Windows 控制台默认是 GBK：
# 不处理会直接抛 UnicodeEncodeError，把"呈现质量"测试变成编码测试。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 某些运行环境不支持 reconfigure，忽略即可
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.offline import (  # noqa: E402
    MAX_FALLBACK_CHARS,
    offline_chat_answer,
    select_relevant_sentences,
    split_sentences,
    summarize_chunk,
)
from app.rag.retriever import RetrievedChunk  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def printable(text: str) -> str:
    """仅供打印：把 GBK 编不出的符号换成 ASCII，避免测试因控制台编码而失败。"""
    return (text or "").replace("📄", "[文件] ").replace("· ", "- ").replace("…", "...")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + printable(detail) if detail else ""))


# 第一页作者行：论文元信息里最典型的"必须展示"内容
AUTHOR_LINE = "曾沙1，王隽1，高景莘1，李萍1，赵晖2"
PAPER_NAME = "基于网络药理学探究苓桂术甘汤治疗心力衰竭的作用机制研究_曾沙.pdf"
UNRELATED_NAME = "结直肠癌发病机制与靶向治疗研究综述.pdf"
QUESTION = "网络药理学论文的作者包含哪些人物"

# 论文块里故意先放一句与问题无关的基金信息：
# 如果实现是"取前 N 字"而不是"选相关句"，这句一定会被展示出来。
PAPER_FILLER = "收稿日期：2025 年 3 月 12 日，基金项目：国家自然科学基金资助项目，编号 82274123。"
# 标题 + 作者行 + 摘要开头：PDF 抽取里作者行常紧跟在标题后，中间没有句号
PAPER_RELEVANT = (
    "基于网络药理学与分子对接技术探究苓桂术甘汤治疗心力衰竭的作用机制 "
    + AUTHOR_LINE
    + "[摘要] 目的：采用网络药理学方法筛选该方剂治疗心力衰竭的核心靶点与信号通路。"
)
PAPER_METHOD = "方法：构建成分-靶点-通路网络并进行富集分析。"

# 无关块：四句长句，整段远超兜底摘要长度，且与问题没有任何实词重合
UNRELATED_CONTENT = (
    "结直肠癌的发生发展与 APC、KRAS 等基因突变密切相关，属于 Wnt 信号通路异常激活的典型疾病。"
    "其临床表现包括便血、腹痛、排便习惯改变以及不明原因的体重下降，早期筛查依赖肠镜。"
    "临床常用 FOLFOX 化疗方案并联合靶向药物西妥昔单抗，但耐药问题仍然突出。"
    "近年来免疫检查点抑制剂为 MSI-H 型患者带来了新的治疗选择，仍需更多真实世界数据。"
)
UNRELATED_TAIL = "免疫检查点抑制剂"


def make_chunk(document_id: int, filename: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        knowledge_id=1,
        filename=filename,
        chunk_index=0,
        content=content,
        score=1.0,
    )


def main() -> int:
    print("== 1. 纯函数：切句与相关句提取 ==")
    sentences = split_sentences("第一句。第二句！第三句？")
    check(
        "按中文句末标点切句且保留标点",
        sentences == ["第一句。", "第二句！", "第三句？"],
        str(sentences),
    )
    check("过滤纯标点 / 空白句", split_sentences("。。") == [], str(split_sentences("。。")))
    check(
        "换行也算分隔符（PDF 标题行/作者行常常没有句号）",
        split_sentences("标题行\n作者行") == ["标题行", "作者行"],
        str(split_sentences("标题行\n作者行")),
    )
    check(
        "只命中单个常见字不算相关（避免假命中）",
        select_relevant_sentences("是的，这里主要是说明文字。", "是谁") == [],
        str(select_relevant_sentences("是的，这里主要是说明文字。", "是谁")),
    )
    check(
        "相关句优先、无关句被丢弃",
        select_relevant_sentences(PAPER_FILLER + PAPER_RELEVANT, QUESTION) == [PAPER_RELEVANT],
        str(select_relevant_sentences(PAPER_FILLER + PAPER_RELEVANT, QUESTION)),
    )

    print("\n== 2. 渲染：相关句提取 + 按文档分组 + 去重 ==")
    chunks = [
        make_chunk(1, PAPER_NAME, PAPER_FILLER + PAPER_RELEVANT + PAPER_METHOD),
        make_chunk(2, PAPER_NAME, PAPER_METHOD),          # 与上一块重复的句子 -> 组内去重
        make_chunk(3, UNRELATED_NAME, UNRELATED_CONTENT),
    ]
    answer = offline_chat_answer("张医生", "医学专家", QUESTION, chunks)
    print("      ---- 离线回答预览 ----")
    for line in answer.splitlines():
        print("      | " + printable(line))

    check(
        "保留头部说明（原始资料、未经模型加工）",
        "【离线模式】" in answer and "未经模型加工" in answer,
        answer[:120],
    )
    check("保留角色行", "角色：张医生（医学专家）" in answer, answer[:200])
    check("保留问题行", "问题：" + QUESTION in answer, answer[:200])
    check(
        "片段数量说明改为「最相关的原文句子」",
        "检索到 3 条资料片段，以下是与问题最相关的原文句子：" in answer,
        answer[:400],
    )
    check(
        "保留结尾说明",
        "配置 API Key 后，模型会基于以上资料组织完整的专业回答。" in answer,
        answer[-120:],
    )

    # 验收点 1：作者行必须出现（用户的核心诉求）
    check("输出包含作者行", AUTHOR_LINE in answer, answer)
    # 验收点 2：文件名只出现一次
    check(
        "文件名在输出中只出现一次",
        answer.count(PAPER_NAME) == 1,
        "出现 " + str(answer.count(PAPER_NAME)) + " 次",
    )
    check(
        "文档分组前缀只写一次",
        answer.count("📄 " + PAPER_NAME) == 1,
        str(answer.count("📄 " + PAPER_NAME)),
    )
    # 验收点 3：无关长段没有被整段搬出来
    check("无关句没有出现在输出里", UNRELATED_TAIL not in answer, answer)
    check(
        "输出总行数受限（不整段搬运）",
        len(answer.splitlines()) <= 16,
        "共 " + str(len(answer.splitlines())) + " 行",
    )
    # 验收点 4：相关句被优选出，无关句被丢掉
    check("无关的基金信息句被丢弃", "基金项目" not in answer, answer)
    check(
        "作者行出现在正文片段之前",
        -1 < answer.find(AUTHOR_LINE) < answer.find(PAPER_METHOD),
        str((answer.find(AUTHOR_LINE), answer.find(PAPER_METHOD))),
    )
    check("逐行以「·」展示提取出的句子", "· " + PAPER_METHOD in answer, answer)
    # 验收点 5：组内去重
    check(
        "组内重复句子只展示一次",
        answer.count(PAPER_METHOD) == 1,
        "出现 " + str(answer.count(PAPER_METHOD)) + " 次",
    )

    print("\n== 3. 无相关句的块回退成开头摘要 ==")
    summary = summarize_chunk(UNRELATED_CONTENT)
    check("兜底摘要长度受限", len(summary) <= MAX_FALLBACK_CHARS + 1, str(len(summary)))
    check("兜底摘要不会带上无关长段尾部", UNRELATED_TAIL not in summary, summary)

    print("\n== 4. 元信息伪切片（document_id 为负）正常参与展示 ==")
    meta_question = "这份文档的上传时间和切片数是多少"
    meta_content = (
        "文件名：" + UNRELATED_NAME + "；上传时间：2026-02-01 09:30:00；切片数：7"
    )
    meta_answer = offline_chat_answer(
        "张医生", "医学专家", meta_question, [make_chunk(-1, UNRELATED_NAME, meta_content)]
    )
    check("元信息伪切片进入分组展示", "📄 " + UNRELATED_NAME in meta_answer, meta_answer)
    check("元信息内容被展示（上传时间）", "上传时间" in meta_answer, meta_answer)
    check("元信息内容被展示（切片数）", "切片数" in meta_answer, meta_answer)

    print("\n== 5. 无片段：保留角色自我介绍 ==")
    empty = offline_chat_answer("张医生", "医学专家", QUESTION, [])
    check("包含角色自我介绍", "我是 张医生（医学专家），很高兴见到你。" in empty, empty)
    check("说明知识库没有相关内容", "知识库里没有与这个问题相关的内容" in empty, empty)

    print("\n== 6. 单句超长时截断（约 140 字 + 省略号）==")
    long_relevant = "网络药理学方法" * 40 + "。"
    long_answer = offline_chat_answer(
        "张医生", "医学专家", QUESTION, [make_chunk(10, PAPER_NAME, long_relevant)]
    )
    long_lines = [line for line in long_answer.splitlines() if line.startswith("· ")]
    check(
        "超长句被截断并加省略号",
        bool(long_lines) and long_lines[0].endswith("…") and len(long_lines[0]) <= 2 + 140 + 1,
        str([len(line) for line in long_lines]),
    )

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("离线模式「相关句提取 + 按文档分组 + 去重」验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
