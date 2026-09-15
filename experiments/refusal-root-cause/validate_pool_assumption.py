"""
验证检索池假设：
  1. 高分case的相关chunk在top-100内能重排进top-8
  2. false-refusal case中pool=100是否有pool=20漏掉的好chunk（距离<0.5）

用法: venv/Scripts/python.exe experiments/refusal-root-cause/validate_pool_assumption.py
"""
import sys
import os
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from ragcore.services.rag_service import RAGService

HIGH_SCORE_QUERIES = [
    "用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当如何补偿劳动者？",
    "正当防卫明显超过必要限度造成重大损害的，应当如何处罚？",
    "经营者提供商品或者服务有欺诈行为的，应当按照消费者的要求增加赔偿几倍？增加赔偿的金额不足五百元的如何计算？",
]

FALSE_REFUSAL_QUERIES = [
    "用人单位违反本法规定解除或者终止劳动合同，应当依照经济补偿标准的几倍向劳动者支付赔偿金？",
    "处理个人信息应当遵循哪些原则？",
    "消费者购买机动车因质量问题反复维修仍不能正常使用，可以主张什么权利？",
]

print("=" * 70)
print("  RAG 检索池假设验证 — pool=20 vs pool=100")
print("  模型: BGE-M3 embedding + bge-reranker-v2-m3")
print("=" * 70)

rag = RAGService()
vs = rag.get_vector_store("documents")

for label, queries in [
    ("高分 case", HIGH_SCORE_QUERIES),
    ("False Refusal case", FALSE_REFUSAL_QUERIES),
]:
    print(f"\n{'─' * 70}")
    print(f"  [{label}]")
    print(f"{'─' * 70}")

    for qi, query in enumerate(queries, 1):
        print(f"\n  [{label} #{qi}] {query}")

        # --- pool=20 ---
        t0 = time.time()
        raw20 = vs.search_documents(query, k=20)
        t_20 = time.time() - t0
        docs20 = raw20["documents"][0]
        dists20 = raw20["distances"][0]

        # --- pool=100 ---
        t0 = time.time()
        raw100 = vs.search_documents(query, k=100)
        t_100 = time.time() - t0
        docs100 = raw100["documents"][0]
        dists100 = raw100["distances"][0]

        # --- reranker on pool=100, take top-20 ---
        t0 = time.time()
        reranked_100 = rag.reranker.rerank(query, docs100, top_k=min(20, len(docs100)))
        t_rerank = time.time() - t0

        # reranker returns [(score, doc_text), ...]; distance = 1 - score
        rerank_dists = [round(1.0 - s, 4) for s, _ in reranked_100]
        rerank_docs = [d for _, d in reranked_100]

        # top-8 from pool=100 rerank
        top8 = list(zip(rerank_dists[:8], rerank_docs[:8]))

        # how many of top-8 exist in pool=20
        doc_set_20 = set(docs20)
        overlap = sum(1 for _, d in top8 if d in doc_set_20)

        good_in_8_35 = sum(1 for dist, _ in top8 if dist < 0.35)
        good_in_8_50 = sum(1 for dist, _ in top8 if dist < 0.50)

        print(f"  ├─ 搜索耗时            pool=20: {t_20:.2f}s  |  pool=100: {t_100:.2f}s")
        print(f"  ├─ 重排序耗时          100→20: {t_rerank:.2f}s")
        print(f"  ├─ pool=100 重排 top-8 (距离 < 0.35): {good_in_8_35}/8")
        print(f"  ├─ pool=100 重排 top-8 (距离 < 0.50): {good_in_8_50}/8")
        print(f"  ├─ top-8 被 pool-20 覆盖: {overlap}/8 chunks")

        if top8:
            best_dist = min(dist for dist, _ in top8)
            worst_dist = max(dist for dist, _ in top8)
            print(f"  ├─ top-8 距离范围: {best_dist:.4f} ~ {worst_dist:.4f}")

        # --- for false-refusal: compare pool=20 vs pool=100 best distances ---
        if label == "False Refusal case":
            reranked_20 = rag.reranker.rerank(query, docs20, top_k=min(len(docs20), 8))
            best20_dist = min(round(1.0 - s, 4) for s, _ in reranked_20) if reranked_20 else 1.0
            best100_dist = min(round(1.0 - s, 4) for s, _ in reranked_100[:8]) if reranked_100 else 1.0

            print(f"  ├─ 生产模式 pool=20 top-8 best 距离: {best20_dist:.4f}")
            print(f"  ├─ 评估模式 pool=100 top-8 best 距离: {best100_dist:.4f}")

            # check if there's a good chunk (<0.5) in pool=100 but not in pool=20
            good_in_100_not_20 = [
                (dist, doc[:60])
                for dist, doc in zip(rerank_dists, rerank_docs)
                if dist < 0.50 and doc not in doc_set_20
            ]
            if good_in_100_not_20:
                print(f"  └─ *** 漏检! pool=100 中有 {len(good_in_100_not_20)} 个好chunk(距离<0.5)不在 pool=20 中:")
                for dist, snippet in good_in_100_not_20[:5]:
                    print(f"       距离={dist:.4f} | {snippet}...")
            else:
                if best20_dist < 0.50:
                    print(f"  └─ False refusal 非检索问题（生产模式已有好chunk，距离={best20_dist:.4f}）")
                else:
                    print(f"  └─ 即使 pool=100 也找不到好chunk（最小距离={best100_dist:.4f}）→ 需改进检索策略本身")

print(f"\n{'=' * 70}")
print("  验证完成。")
print("=" * 70)
