"""生成记忆检索评测集的**标注候选**（issue #24）：LLM 出题 + 人工抽检。

流程：从当前索引（manifest）覆盖的语料里确定性地抽样 → 每条用 LLM 生成一个
「该条目能回答的自然查询」→ 相关条目 = 该条目 → 另附少量无答案 query。
产出的 JSON 经人工/agent 复核后固化入仓库（`retrieval_eval_set.json`）。

只生成候选，不写指标。指标跑 `retrieval_eval.py`。

    venv\\Scripts\\python.exe memory_agent/eval/build_eval_set.py --dry-run
    venv\\Scripts\\python.exe memory_agent/eval/build_eval_set.py --out memory_agent/eval/retrieval_eval_set.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from memory_agent import _bootstrap  # noqa: E402

_bootstrap.configure_stderr_logging()
_bootstrap.ensure_ragcore_on_path()

from memory_agent.corpus.loader import load_corpus  # noqa: E402
from memory_agent.memory.index import MemoryIndex  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "retrieval_eval_set.json")

# 全局 KB 里的测试夹具条目（#19 dogfood 产生），不适合出题。
EXCLUDE_ID_SUBSTRINGS = ("issue19-alpha", "issue19-beta")

# 各组抽样配额（可被 --writable / --per-label 覆盖）。
DEFAULT_WRITABLE = 20
DEFAULT_PER_LABEL = 10

# 明确落在语料之外的 query：用于报告「无答案时 top-1 分数」分布。
NO_ANSWER_QUERIES = [
    "明天北京会下雨吗",
    "红烧肉的家常做法是什么",
    "2026 年世界杯冠军是哪支球队",
    "感冒了应该吃什么药",
    "去日本旅游怎么办签证",
    "怎么训练一只边牧接飞盘",
]

PROMPT = (
    "你在为检索系统出评测题。给定一份知识库条目的标题与节选，写一条用户可能提出的、"
    "且这份条目能够回答的**自然检索查询**。要求：\n"
    "- 用中文（条目主要为英文时可用英文）；\n"
    "- 像真人提问，不要照抄标题或原文的长短语；\n"
    "- 不要出现文件名、路径、条目 id；\n"
    "- 只输出查询本身，一行，不要引号、不要解释。"
)


def _sample_groups(entries, *, writable: int, per_label: int, seed: int):
    rng = random.Random(seed)
    usable = [e for e in entries
              if not any(s in e.id for s in EXCLUDE_ID_SUBSTRINGS)]
    writable_entries = [e for e in usable if e.writable]
    readonly = [e for e in usable if not e.writable]

    by_label: dict[str, list] = {}
    for entry in readonly:
        by_label.setdefault(entry.source.split("/", 1)[0], []).append(entry)

    picked = []
    wr = sorted(writable_entries, key=lambda e: e.id)
    picked.extend(rng.sample(wr, min(writable, len(wr))))
    for label in sorted(by_label):
        items = sorted(by_label[label], key=lambda e: e.id)
        picked.extend(rng.sample(items, min(per_label, len(items))))
    return picked


def generate_query(client, model: str, entry) -> str:
    user = f"标题：{entry.title}\n节选：{entry.body.strip()[:1000]}"
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": user}],
        temperature=0.7,
        # 模型会先输出 reasoning_content，再输出 content；预算要留够否则 content 为空。
        max_tokens=1024,
    )
    text = (response.choices[0].message.content or "").strip()
    return text.splitlines()[0].strip().strip('"“”') if text else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build retrieval eval-set candidates")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--writable", type=int, default=DEFAULT_WRITABLE)
    parser.add_argument("--per-label", type=int, default=DEFAULT_PER_LABEL)
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印抽样结果，不调 LLM、不写文件")
    args = parser.parse_args(argv)

    index = MemoryIndex()
    known = set(index.known_ids())
    entries = [e for e in load_corpus() if e.id in known]
    picked = _sample_groups(entries, writable=args.writable,
                            per_label=args.per_label, seed=args.seed)

    print(f"index gen={index.gen} known={len(known)} corpus={len(entries)} "
          f"picked={len(picked)}")
    for entry in picked:
        print(f"  {'W' if entry.writable else 'R'} {entry.id}  {entry.title}")

    if args.dry_run:
        return 0

    from config.llm import require_llm
    from openai import OpenAI
    llm = require_llm()
    model = args.model or llm.model
    client = OpenAI(api_key=llm.api_key, base_url=llm.base_url)

    queries = []
    for i, entry in enumerate(picked, start=1):
        query = generate_query(client, model, entry)
        if not query:
            print(f"  [skip] {entry.id} 未生成查询")
            continue
        queries.append({
            "id": f"q{i:03d}",
            "query": query,
            "relevant": [entry.id],
            "source_entry": entry.id,
            "kind": "answerable",
        })
        print(f"  q{i:03d} <- {entry.id} :: {query}")

    for j, query in enumerate(NO_ANSWER_QUERIES, start=1):
        queries.append({
            "id": f"na{j:02d}",
            "query": query,
            "relevant": [],
            "kind": "no_answer",
        })

    payload = {
        "name": "memory-retrieval-eval",
        "version": 1,
        "index_gen": index.gen,
        "seed": args.seed,
        "generator": "memory_agent/eval/build_eval_set.py",
        "annotation": (
            "query 由 LLM 依据来源条目的标题+节选生成（temperature=0.7），"
            "相关条目 = 来源条目（条目级二值）；经 agent 复核，易错子集待人工抽检。"
            "no_answer 查询与语料无关，仅用于报告 top-1 分数分布。"
        ),
        "queries": queries,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"written {args.out} ({len(queries)} queries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
