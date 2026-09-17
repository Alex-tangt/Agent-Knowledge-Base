"""数据集检验（issue #47 第 3 步）：构成 / evidence 篇数 / 语料完整性 / metadata。

不需要模型、秒级。写 `artifacts/inspection.json` 并打印摘要。

    python experiments/agentic-rag-census/inspect.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import multihop as mh  # noqa: E402


def main() -> int:
    eval_set, entries = mh.ensure_prepared()
    with open(mh.INSPECTION_JSON, "r", encoding="utf-8") as handle:
        inspection = json.load(handle)

    print(f"corpus: {inspection['corpus']['articles']} articles, "
          f"url_unique={inspection['corpus']['url_unique']}, "
          f"body p50={inspection['corpus']['body_chars']['p50']} "
          f"over_window={inspection['corpus']['body_chars']['over_embedding_window']}")
    print(f"queries: {inspection['queries']['total']} "
          f"(answerable={inspection['queries']['answerable']}, "
          f"null={inspection['queries']['null_query']})")
    print(f"  by_type: {inspection['queries']['by_question_type']}")
    print(f"  evidence(unique) dist: "
          f"{inspection['queries']['evidence_count_unique_distribution']}")
    print(f"  evidence(raw)    dist: "
          f"{inspection['queries']['evidence_count_raw_distribution']}")
    print(f"provenance: evidence_slots={inspection['provenance']['evidence_slots']}, "
          f"not_in_corpus={len(inspection['provenance']['evidence_urls_not_in_corpus'])}, "
          f"facts_not_in_body={inspection['provenance']['facts_not_substring_of_body']}, "
          f"facts_outside_window={inspection['provenance']['facts_not_in_embedding_window']}")
    print(f"entries: {len(entries)}")
    print(f"[out] {mh.INSPECTION_JSON}")
    print(f"[out] {mh.EVAL_SET_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
