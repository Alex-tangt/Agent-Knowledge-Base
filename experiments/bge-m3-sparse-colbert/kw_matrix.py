"""建 `kw` 信号矩阵（51×134）+ **锚点**：离线复现生产「dense + 手写关键词加法」的 qrels 指标。

- `kw[i,j] = matched / len(keywords)`（`DefaultRetrievalStrategy` 的强度口径；无模型，纯子串匹配）。
- 锚点：按生产口径（dense top-14 池 + 0.05·strength，池尾补 ≤6 条独占候选）重排，
  必须复现 `0.7074 / 0.8685 / 0.9185 / 0.8817 / 0.8731`。复现不了说明离线链有偏差 → 停手。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_agent import settings  # noqa: E402
from memory_agent.eval.metrics import evaluate  # noqa: E402
from ragcore.services.vector_store_service import VectorStoreService  # noqa: E402
from ragcore.strategies.default import DEFAULT_KEYWORD_WEIGHT, extract_keywords  # noqa: E402

KS = (1, 3, 5, 10)
PROD = {"recall@1": 0.7074, "recall@3": 0.8685, "recall@5": 0.9185,
        "nDCG@10": 0.8817, "mrr": 0.8731}
POOL = 14
KEYWORD_CAP = 6


def _rank(items, doc_ids):
    order = sorted(range(len(doc_ids)), key=lambda j: (-float(items[j]), doc_ids[j]))
    return [doc_ids[j] for j in order]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="kw 信号矩阵 + 生产锚点")
    parser.add_argument("--index-dir", required=True)
    parser.add_argument("--dense-st", required=True)
    parser.add_argument("--eval-set", default="memory_agent/eval/retrieval_eval_set.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--gen", default=None)
    args = parser.parse_args(argv)

    gen = args.gen
    if not gen:
        with open(os.path.join(args.index_dir, "CURRENT"), encoding="utf-8") as fh:
            gen = fh.read().strip()

    st = np.load(args.dense_st, allow_pickle=True)
    doc_ids = [str(x) for x in st["doc_ids"]]
    dense_st = st["dense_st"]
    qids = [str(x) for x in st["qids"]]
    with open(args.eval_set, encoding="utf-8") as fh:
        queries = {q["id"]: q for q in json.load(fh)["queries"]}

    service = VectorStoreService(collection_name=settings.COLLECTION_NAME,
                                 db_path=os.path.join(args.index_dir, gen, "qdrant"))
    index = {d: j for j, d in enumerate(doc_ids)}
    kw = np.zeros((len(qids), len(doc_ids)), dtype=np.float32)
    for i, qid in enumerate(qids):
        text = queries[qid]["query"]
        keywords = extract_keywords(text)
        if not keywords:
            print(f"[kw] {qid} 无关键词", file=sys.stderr)
            continue
        hits = service.search_by_keywords(keywords)
        for hit in hits:
            entry_id = (hit.get("metadata") or {}).get("entry_id")
            j = index.get(entry_id)
            if j is None:
                continue
            kw[i, j] = max(kw[i, j], hit.get("matched", 1) / len(keywords))
        print(f"[kw] {qid} kw={len(keywords)} 覆盖 {int((kw[i] > 0).sum())}", file=sys.stderr, flush=True)
    service.close()

    # ---- 锚点：复现生产口径 ----
    kw_first = np.zeros_like(dense_st)
    for i in range(len(qids)):
        pool = np.argsort(-dense_st[i], kind="stable")[:POOL]
        kw_first[i, pool] = kw[i, pool]          # 池内加成
        rest = [j for j in np.argsort(-kw[i], kind="stable") if kw[i, j] > 0 and j not in set(pool)]
        for j in rest[:KEYWORD_CAP]:             # 池尾补充
            kw_first[i, j] = kw[i, j]
    fused = dense_st + DEFAULT_KEYWORD_WEIGHT * kw_first

    records = [{"id": qid, "query": queries[qid]["query"],
                "relevant": queries[qid].get("relevant") or [],
                "ranked": _rank(fused[i], doc_ids)} for i, qid in enumerate(qids)]
    agg = evaluate(records, ks=KS, ndcg_k=10)["aggregate"]
    got = {"recall@1": agg["recall"]["1"], "recall@3": agg["recall"]["3"],
           "recall@5": agg["recall"]["5"], "nDCG@10": agg["ndcg@10"], "mrr": agg["mrr"]}
    print("\n锚点（离线复现生产 dense + 0.05·kw）:", file=sys.stderr)
    ok = True
    for k, want in PROD.items():
        delta = got[k] - want
        flag = "OK" if abs(delta) < 1e-4 else "**MISMATCH**"
        ok = ok and abs(delta) < 1e-4
        print(f"  {k:9} got {got[k]:.6f}  want {want:.6f}  Δ {delta:+.6f}  {flag}", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, kw=kw, doc_ids=np.array(doc_ids), qids=np.array(qids))
    print(f"\nkw -> {args.out}  anchor={'PASS' if ok else 'FAIL'}", file=sys.stderr, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
