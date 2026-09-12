"""混合检索测试：BM25 稀疏通道 + RRF 融合（离线、确定性）。

验证：
1. BM25 对精确术语（阿柏西普 / VEGF）命中准确，不相关文档不入选
2. 单字噪声防护：只共享一个常见字（"是"）不算命中
3. RRF 融合：稠密通道漏掉、但 BM25 命中的文档能被救回来（混合检索的核心收益）
4. RRF 对"两路都命中"的文档给更高排名

用法：
    python tests/hybrid_retrieval_test.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.bm25 import bm25_search, reciprocal_rank_fusion  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  [PASS] " + name)
    else:
        FAILED.append(name)
        print("  [FAIL] " + name + (" -> " + detail if detail else ""))


class Doc:
    def __init__(self, doc_id: int, content: str) -> None:
        self.id = doc_id
        self.content = content


DOCS = [
    Doc(1, "阿柏西普用于治疗糖尿病黄斑水肿，属于抗 VEGF 融合蛋白药物。"),
    Doc(2, "康柏西普与阿柏西普同属融合蛋白，均可抑制新生血管生成。"),
    Doc(3, "结直肠癌的发病率逐年攀升，中医药归入肠覃范畴。"),
    Doc(4, "公司第二季度财报显示营收增长，主要来自订阅制业务。"),
]


def ids(results) -> list[int]:
    return [doc.id for _score, doc in results]


def main() -> int:
    print("== 1. BM25 精确术语命中 ==")
    hits = bm25_search("阿柏西普 适应症", DOCS)
    got = ids(hits)
    check("命中含「阿柏西普」的两篇", set(got) >= {1, 2}, str(got))
    check("不相关的结直肠癌/财报未被命中", 3 not in got and 4 not in got, str(got))

    print("\n== 2. 单字噪声防护 ==")
    noise_docs = [Doc(9, "是的，这里主要是说明文字。"), Doc(10, "你是谁不重要，重要的是结果。")]
    check("只共享常见单字 → 不命中", bm25_search("是谁", noise_docs) == [], str(ids(bm25_search("是谁", noise_docs))))

    print("\n== 3. RRF 融合：BM25 能救回稠密通道漏掉的文档 ==")
    dense_ranking = [1]          # 稠密向量只保留了第 1 篇（第 2 篇低于阈值被丢）
    sparse_ranking = ids(hits)   # BM25 同时命中 1、2
    fused = reciprocal_rank_fusion(dense_ranking, sparse_ranking)
    fused_ids = [doc_id for doc_id, _score in fused]
    check("被稠密通道漏掉的第 2 篇被救回", 2 in fused_ids, str(fused_ids))
    check("融合结果只包含两路出现过文档", set(fused_ids) <= {1, 2}, str(fused_ids))

    print("\n== 4. RRF 排名：两路都命中者优先 ==")
    check("第 1 篇（两路都命中）排第一", fused_ids[0] == 1, str(fused_ids))
    check("融合分为正", all(score > 0 for _doc_id, score in fused))

    print("\n" + "=" * 60)
    print("PASSED: " + str(len(PASSED)) + "   FAILED: " + str(len(FAILED)))
    if FAILED:
        for item in FAILED:
            print("  - " + item)
        return 1
    print("混合检索（BM25 + RRF）验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
