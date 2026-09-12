"""Rerank 精排测试（纯 Python、离线、确定性，不调用模型与数据库）。

覆盖：
1. 术语密集（覆盖率 + 短语命中）的切片排在前面
2. 只命中一个常见单字的切片排到最后（防噪）
3. 空查询不报错，保持原顺序并截断
4. 完全无实词命中的切片排到最后，但**仍然保留**（不丢弃）
5. top_k 截断

用法：
    python tests/rerank_test.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.rerank import (  # noqa: E402
    longest_common_substring_length,
    meaningful_terms,
    rerank,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


@dataclass
class Chunk:
    """最小切片替身：rerank 只依赖 content 属性。"""

    content: str


def names(chunks) -> list[str]:
    return [chunk.content[:12] for chunk in chunks]


def main() -> int:
    print("== 0. 辅助函数 ==")
    check("实词只保留长度 >= 2 的记号", "阿" not in meaningful_terms("阿柏西普"), str(meaningful_terms("阿柏西普")))
    check("英文词进入实词", "vegf" in meaningful_terms("VEGF 适应症"), str(meaningful_terms("VEGF 适应症")))
    check("最长公共子串正确", longest_common_substring_length("abcd", "xbcdy") == 3)
    check("空串公共子串为 0", longest_common_substring_length("", "abc") == 0)

    print("\n== 1. 术语密集的切片排在前面 ==")
    dense = Chunk("阿柏西普是一种抗 VEGF 融合蛋白，适用于治疗适应症明确的患者。")
    medium = Chunk("这类疾病的适应症需要专业医生评估后再决定。")
    unrelated = Chunk("公司第二季度财报显示营收增长，主要来自订阅制业务。")
    ordered = rerank("阿柏西普 VEGF 适应症", [unrelated, medium, dense], 3)
    check("术语密集的切片排第一", ordered[0] is dense, names(ordered))
    check("只命中一个术语的排第二", ordered[1] is medium, names(ordered))
    check("完全不相关的排最后", ordered[2] is unrelated, names(ordered))

    print("\n== 2. 只命中一个常见单字 -> 排到最后 ==")
    dense2 = Chunk("心肌梗死溶栓时间窗通常是发病后十二小时以内。")
    noise = Chunk("是的，这里主要是说明文字，和你问的内容没有关系。")
    ordered2 = rerank("心肌梗死溶栓时间窗", [noise, dense2], 2)
    check("相关切片排第一", ordered2[0] is dense2, names(ordered2))
    check("只含常见单字的噪声排最后", ordered2[1] is noise, names(ordered2))

    print("\n== 3. 空查询 / 纯符号查询不报错 ==")
    items = [Chunk("第一条"), Chunk("第二条"), Chunk("第三条")]
    empty_result = rerank("", items, 2)
    check("空查询返回 top_k 条", len(empty_result) == 2, str(len(empty_result)))
    check("空查询保持原顺序", empty_result[0] is items[0] and empty_result[1] is items[1])
    check("纯标点查询同样不报错", len(rerank("???", items, 3)) == 3)

    print("\n== 4. 完全无命中排最后但保留 ==")
    keep = Chunk("心肌梗死需要尽快完成心电图检查。")
    no_hit = Chunk("今天天气不错，适合出门散步。")
    result4 = rerank("心肌梗死", [no_hit, keep], 5)
    check("无命中切片被保留而非丢弃", len(result4) == 2, str(len(result4)))
    check("有命中的排前面", result4[0] is keep, names(result4))
    check("无命中排在最后", result4[1] is no_hit, names(result4))

    print("\n== 5. top_k 截断 ==")
    many = [Chunk("心肌梗死" + str(index)) for index in range(5)]
    check("返回条数受 top_k 限制", len(rerank("心肌梗死", many, 2)) == 2)

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("Rerank 精排验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
