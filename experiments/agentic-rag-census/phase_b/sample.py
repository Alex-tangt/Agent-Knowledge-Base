"""先导抽样（issue #48 规格）：分题型 × unique 金标篇数 分层，固定 seed，冻结 id 列表。

- 总体 = MultiHop-RAG 全部 2556 条（含 `null_query`，作为 `null_query|0` 层）。
- N=200 比例分配（最大余数法）；每层内固定 seed 无放回抽样。
- 每层再按固定 seed 切 **dev / holdout = 70 / 30**（holdout 不参与 prompt / 规则调整）。
- 输出只含 id 与层信息（**无数据集正文**），可安全提交。

    python experiments/agentic-rag-census/phase_b/sample.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict

from common import SAMPLE_JSON, load_eval

DEFAULT_SEED = 20260917
DEFAULT_N = 200
DEV_RATIO = 0.7


def stratum_key(row: dict) -> str:
    if row["kind"] == "no_answer":
        return "null_query|0"
    return f"{row['question_type']}|{row['evidence_count']}"


def allocate(populations: dict[str, int], total: int) -> dict[str, int]:
    """按总体比例做最大余数分配，和恰为 `total`。"""
    grand = sum(populations.values())
    raw = {key: total * pop / grand for key, pop in populations.items()}
    floor = {key: int(value) for key, value in raw.items()}
    remainder = total - sum(floor.values())
    order = sorted(raw, key=lambda key: (-(raw[key] - floor[key]), key))
    for key in order[:remainder]:
        floor[key] += 1
    return floor


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase B pilot sampling")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--out", default=SAMPLE_JSON)
    args = parser.parse_args(argv)

    eval_set = load_eval()
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for qid, row in eval_set.items():
        by_stratum[stratum_key(row)].append(qid)
    for ids in by_stratum.values():
        ids.sort()

    populations = {key: len(ids) for key, ids in by_stratum.items()}
    allocation = allocate(populations, args.n)

    rng = random.Random(args.seed)
    dev: list[str] = []
    holdout: list[str] = []
    all_ids: list[str] = []
    strata_report = {}
    for key in sorted(populations):
        pool = list(by_stratum[key])
        n_s = allocation[key]
        picked = rng.sample(pool, n_s)
        picked.sort()
        rng.shuffle(picked)
        n_dev = int(round(n_s * DEV_RATIO))
        dev.extend(picked[:n_dev])
        holdout.extend(picked[n_dev:])
        all_ids.extend(picked)
        strata_report[key] = {
            "population": populations[key],
            "sampled": n_s,
            "dev": n_dev,
            "holdout": n_s - n_dev,
        }

    assert len(all_ids) == args.n, (len(all_ids), args.n)
    assert set(all_ids) == set(dev) | set(holdout)
    assert not (set(dev) & set(holdout))

    sample = {
        "name": "multihop-rag-phase-b-pilot",
        "version": 1,
        "dataset": "yixuantt/MultiHopRAG",
        "license": "ODC-BY",
        "seed": args.seed,
        "n": args.n,
        "dev_ratio": DEV_RATIO,
        "strata": strata_report,
        "counts": {
            "answerable": sum(1 for qid in all_ids
                              if eval_set[qid]["kind"] != "no_answer"),
            "null_query": sum(1 for qid in all_ids
                              if eval_set[qid]["kind"] == "no_answer"),
        },
        "dev": dev,
        "holdout": holdout,
        "all": all_ids,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=1)
    print(f"[out] {args.out}")
    for key, info in strata_report.items():
        print(f"  {key}: pop={info['population']} sampled={info['sampled']} "
              f"(dev={info['dev']}, holdout={info['holdout']})")
    print(f"total={len(all_ids)} answerable={sample['counts']['answerable']} "
          f"null={sample['counts']['null_query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
