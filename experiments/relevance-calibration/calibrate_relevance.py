"""校准相关性拒答阈值 (Qdrant + BGE-M3 版本)。

用法:
    venv/Scripts/python.exe experiments/relevance-calibration/calibrate_relevance.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ragcore"))

from config.config import QDRANT_COLLECTION_NAME
from services.vector_store_service import VectorStoreService
from utils.logger import logger

RELEVANT = [
    "个人所得税的专项附加扣除有哪些？",
    "劳动合同试用期最长不得超过几个月？",
    "民法典中合同违约责任是怎么规定的？",
    "居住证申领需要哪些材料？",
    "公司股东会职权有哪些？",
    "个人信息被收集可以要求什么？",
    "行政处罚的种类有哪些？",
    "劳动合同解除补偿金怎么算？",
]

IRRELEVANT = [
    "今天北京天气怎么样？",
    "如何做番茄炒蛋？",
    "量子计算机的基本原理是什么？",
    "世界杯2022冠军是谁？",
    "怎么用Python画一只猫？",
    "猫咪一天睡几个小时？",
]


def main():
    vs = VectorStoreService(collection_name=QDRANT_COLLECTION_NAME)
    print(f"集合当前块数: {vs.get_document_count()}\n")
    print(f"模型: BGE-M3 + Qdrant (cosine distance, lower=more relevant)\n")

    def show(title, queries, expected_hit: bool):
        print(f"===== {title} (期待 {'命中' if expected_hit else '拒答'}) =====")
        all_best = []
        for q in queries:
            result = vs.search_documents(q, k=12)
            dists = result["distances"][0] if result.get("distances") else []
            best = dists[0] if dists else None
            all_best.append(best)
            print(f"\nQ: {q}")
            print(f"  best={best:.4f}" if best is not None else "  best=None")
            print("  top-5: " + ", ".join(f"{d:.4f}" for d in dists[:5]))
        print(f"\n  范围 best ∈ [{min(all_best):.4f}, {max(all_best):.4f}]\n")
        return all_best

    best_relevant = show("有依据查询", RELEVANT, True)
    best_irrelevant = show("无依据查询", IRRELEVANT, False)

    r_max = max(best_relevant)
    i_min = min(best_irrelevant)
    gap = i_min - r_max

    print(f"有依据 best max: {r_max:.4f}")
    print(f"无依据 best min: {i_min:.4f}")
    print(f"间隙: {gap:.4f}")

    if gap > 0:
        threshold = r_max + gap * 0.5
        print(f"\n建议 RELEVANCE_THRESHOLD = {threshold:.4f}")
        print(f"  (有依据最大 + 间隙一半, 保证不误拒任何已观测有依据查询)")
    else:
        threshold = (r_max + i_min) / 2
        print(f"\n二者重叠！建议 RELEVANCE_THRESHOLD = {threshold:.4f}")
        print(f"  (取中间值, 需注意可能误拒部分有依据查询或误放无依据查询)")


if __name__ == "__main__":
    main()
