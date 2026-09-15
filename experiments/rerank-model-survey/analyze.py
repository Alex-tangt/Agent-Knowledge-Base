"""把 #29 的结果 JSON 汇总成 markdown 表（README 用）。只读，不改数据。"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load(name):
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    mem = load("memory_results_limit12.json")
    mem_lat = load("latency_memory.json")
    legal = load("legal_results_limit12.json")
    legal_lat = load("latency_legal.json")

    if mem:
        print("## memory quality (first 12 queries, pool cached)\n")
        print("| model | license | recall@1 | MRR | nDCG@10 | misses | run_hash |")
        print("|---|---|---|---|---|---|---|")
        for key, v in mem["models"].items():
            a = v["aggregate"]
            print(f"| `{key}` | {v['license']} | {a['recall']['1']:.4f} | {a['mrr']:.4f} "
                  f"| {a['ndcg@10']:.4f} | {len(a['misses'])} | `{v['run_hash']}` |")
        print()
        print("### model facts\n")
        print("| model | dtype | max_seq_length | load s | rss delta GB | tokens max/mean/over512 |")
        print("|---|---|---|---|---|---|")
        for key, v in mem["models"].items():
            t = v.get("tokens") or {}
            print(f"| `{key}` | {v['dtype']} | {v['max_seq_length']} | {v['load_s']} "
                  f"| {v['rss_delta_gb']} | {t.get('max')}/{t.get('mean')}/{t.get('over_cap')} |")

    if mem_lat:
        print("\n## memory rerank latency (interleaved, 8 q x 3 rounds, 15.9 pairs/call)\n")
        print("| model | mean ms | p50 | p95 | sd | s/pair |")
        print("|---|---|---|---|---|---|")
        for key, v in mem_lat["models"].items():
            L = v["latency_ms"]
            print(f"| `{key}` | {L['mean']:.1f} | {L['p50']:.1f} | {L['p95']:.1f} "
                  f"| {L['sd']:.1f} | {v['s_per_pair']:.3f} |")
        print(f"\npeak_rss_gb (all loaded) = {mem_lat['meta']['peak_rss_gb']}")

    if legal:
        print("\n## legal anchor (first 12 questions; rank agreement vs baseline)\n")
        print("| model | license | top-8 overlap | Kendall tau | refuse | mean ms |")
        print("|---|---|---|---|---|---|")
        for key, v in legal["models"].items():
            L = v["latency_ms"]
            print(f"| `{key}` | {v['license']} | "
                  f"{v.get('topk_overlap_vs_baseline', float('nan')):.4f} | "
                  f"{v.get('kendall_tau_vs_baseline', float('nan')):.4f} | "
                  f"{v['refuse_count']} | {L['mean']:.1f} |")

    if legal_lat:
        print("\n## legal rerank latency (interleaved)\n")
        print("| model | mean ms | p50 | p95 | sd | s/pair |")
        print("|---|---|---|---|---|---|")
        for key, v in legal_lat["models"].items():
            L = v["latency_ms"]
            print(f"| `{key}` | {L['mean']:.1f} | {L['p50']:.1f} | {L['p95']:.1f} "
                  f"| {L['sd']:.1f} | {v['s_per_pair']:.3f} |")


if __name__ == "__main__":
    main()
