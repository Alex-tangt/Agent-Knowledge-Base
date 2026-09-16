"""k census（#21）：消费者口径返回条数 k 的收益曲线——纯离线，不加载模型。

数据源 = 已冻结的逐题排名（`--report` 指到 `experiments/rerank-jina-35/*.json`，
其 `per_query[].ranked` 是池 14 上的完整降序排名）。对每个 k = 1..10 计算：
- `recall@k`（有答案题）；`hits@k`（≥1 个 gold）；`precision@k`（gold/返回数）；
- `marginal`（recall@k − recall@k−1），看「每多返回一条」的边际收益。

用法：
    python census.py --report ../rerank-jina-35/retriever_only.json \
        --report ../rerank-jina-35/jina_onnx.json --report ../rerank-jina-35/m3_torch.json \
        --out census.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

KMAX = 10


def _curve(per_query: list[dict]) -> dict:
    answerable = [q for q in per_query if q.get("relevant")]
    total = len(answerable)
    curve = {}
    prev_recall = 0.0
    for k in range(1, KMAX + 1):
        recall_sum = hits = precision_sum = 0.0
        for q in answerable:
            gold = set(q["relevant"])
            topk = q["ranked"][:k]
            found = len(gold & set(topk))
            recall_sum += found / len(gold)
            precision_sum += found / k
            hits += 1 if found else 0
        recall = recall_sum / total if total else 0.0
        curve[k] = {
            "recall": round(recall, 4),
            "hits": round(hits / total, 4) if total else 0.0,
            "precision": round(precision_sum / total, 4) if total else 0.0,
            "marginal_recall": round(recall - prev_recall, 4),
        }
        prev_recall = recall
    return {"answerable": total, "curve": curve}


def _label(report: dict, path: str) -> str:
    meta = report.get("meta", {})
    if meta.get("rerank") is False or meta.get("backend") is None and meta.get("fusion") is None and meta.get("sparse_backend") is None:
        return "default(dense+keyword)"
    if meta.get("backend"):
        return f"rerank={meta['backend']}"
    if meta.get("sparse_backend"):
        return f"native {meta['sparse_backend']}/{meta.get('fusion')}"
    return os.path.basename(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="k census (offline)")
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    results = {}
    for path in args.report:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        label = _label(report, path)
        results[label] = {"path": os.path.relpath(os.path.abspath(path), REPO_ROOT),
                          **_curve(report["per_query"])}

    header = f"{'link':26} " + " ".join(f"r@{k:<2}" for k in range(1, KMAX + 1))
    print(header)
    for label, data in results.items():
        row = " ".join(f"{data['curve'][k]['recall']:.3f}" for k in range(1, KMAX + 1))
        print(f"{label:26} {row}")
    print(f"\n{'link':26} " + " ".join(f"m@{k:<2}" for k in range(2, KMAX + 1)))
    for label, data in results.items():
        row = " ".join(f"{data['curve'][k]['marginal_recall']:+.3f}" for k in range(2, KMAX + 1))
        print(f"{label:26} {row}")
    print(f"\n{'link':26} " + " ".join(f"p@{k:<2}" for k in range(1, KMAX + 1)))
    for label, data in results.items():
        row = " ".join(f"{data['curve'][k]['precision']:.3f}" for k in range(1, KMAX + 1))
        print(f"{label:26} {row}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2)
        print(f"\nwritten -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
