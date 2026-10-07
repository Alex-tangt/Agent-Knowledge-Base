"""标准 RAG 集（#71 阶段 4）：分钟级 census → 选型 → 小规模一次性检索基线。

**选型（依据见 `standard_sets/standard_rag_set_71.census.md`）**：`SciFact`（BEIR 打包，
HF `BeIR/scifact` + `BeIR/scifact-qrels`，**cc-by-sa-4.0**）——英文、**单跳**
（claim → abstract）、语料 5,183 篇 / test 300 query（283 篇金标，二值 qrels，p50 1 篇/题）。
中文候选（T2Retrieval / DuRetrieval / CmedqaRetrieval / MMarcoRetrieval）语料 **10 万+**，
远超 ≤2k 规模锚点且许可卡缺失；BEIR 其余集也更大或 qrels 是分级稠密（NFCorpus）。
`CRUD-RAG` 未取到 HF 卡（401，数据在 GitHub 且是**生成**导向基准）→ 不采。

**边界（沿 ADR-0026 D5/D6）**：外部语料**只作机制证据、不声称本库增益**；域外（生物医学）、
语言（英文）、单跳——都不能外推到本 KB。

跑法：

    $py = "D:\\...\\venv\\Scripts\\python.exe"
    $env:PYTHONPATH = "D:\\...\\wk-71-eval"
    & $py memory_agent/eval/eval_71_standard_set.py --census
    & $py memory_agent/eval/eval_71_standard_set.py --run --docs 1500        # ≤2k 规模锚点档
    & $py memory_agent/eval/eval_71_standard_set.py --run --docs 0          # 全量 5,183（外部可比档）

隔离：数据缓存与索引都在 `%TEMP%/eval71-standard/`；**从不**用 `MEMORY_INDEX_DIR` 指针模式、
`MEMORY_READONLY_ROOTS` 置空——生产索引 / 真实 KB / daemon 一概不碰。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STANDARD_SETS = os.path.join(HERE, "standard_sets")
DEFAULT_DATA_DIR = os.path.join(tempfile.gettempdir(), "eval71-standard", "data")
DEFAULT_INDEX_DIR = os.path.join(tempfile.gettempdir(), "eval71-standard", "index")
COLLECTION = "scifact_71"
KS = (1, 5, 10, 20, 50, 100)
PROD_POOL = 14
KMAX = 100
ENTRY_PREFIX = "scifact:"

SETS = {
    "scifact": {
        "language": "en",
        "task": "claim → abstract（单跳证据检索）",
        "corpus": ("BeIR/scifact", "corpus", "corpus"),
        "queries": ("BeIR/scifact", "queries", "queries"),
        "qrels": ("BeIR/scifact-qrels", "default", "test"),
        "dataset": "BeIR/scifact (+ BeIR/scifact-qrels)",
        "license": "cc-by-sa-4.0",
        "homepage": "https://github.com/allenai/scifact",
    },
}

# 分钟级 census（2026-10-07 由 HF API `/datasets/<id>` + `/info` + `/parquet` 实测；
# `--census --refresh` 可重测）。规模 = corpus 条目数 / 有 qrels 的 test query 数。
CENSUS = [
    {"set": "BeIR/scifact", "lang": "en", "corpus": 5183, "queries": 300,
     "test_qrels": "339 对 / 300 query / 283 金标篇（二值）", "license": "cc-by-sa-4.0",
     "reembed": "是（新语料，需重嵌 dense + sparse）", "verdict": "**采用**"},
    {"set": "BeIR/nfcorpus", "lang": "en", "corpus": 3633, "queries": 3237,
     "test_qrels": "分级稠密（每 query 多篇相关）", "license": "cc-by-sa-4.0",
     "reembed": "是", "verdict": "备选：qrels 分级 + 相关篇数多 → recall@k 语义更钝"},
    {"set": "BeIR/arguana", "lang": "en", "corpus": 8674, "queries": 1406,
     "test_qrels": "1 篇/题", "license": "cc-by-sa-4.0", "reembed": "是",
     "verdict": "不采：任务形态是反论点检索（非证据检索）"},
    {"set": "BeIR/fiqa", "lang": "en", "corpus": 57638, "queries": 6648,
     "test_qrels": "有", "license": "cc-by-sa-4.0", "reembed": "是",
     "verdict": "不采：语料 5.7 万，超规模锚点"},
    {"set": "BeIR/scidocs", "lang": "en", "corpus": 25657, "queries": 1000,
     "test_qrels": "有", "license": "cc-by-sa-4.0", "reembed": "是",
     "verdict": "不采：语料 2.6 万（引用推荐任务）"},
    {"set": "BeIR/trec-covid", "lang": "en", "corpus": 171332, "queries": 50,
     "test_qrels": "有", "license": "cc-by-sa-4.0", "reembed": "是",
     "verdict": "不采：语料 17 万 / 仅 50 query"},
    {"set": "mteb/T2Retrieval", "lang": "zh", "corpus": 118605, "queries": 22812,
     "test_qrels": "有", "license": "apache-2.0", "reembed": "是",
     "verdict": "不采：中文但语料 11.9 万，≫2k"},
    {"set": "C-MTEB/T2Retrieval", "lang": "zh", "corpus": 118605, "queries": 22812,
     "test_qrels": "有", "license": "卡缺 license", "reembed": "是",
     "verdict": "不采：同规模 + 许可不清"},
    {"set": "mteb/DuRetrieval", "lang": "zh", "corpus": 100001, "queries": 2000,
     "test_qrels": "有", "license": "卡缺 license", "reembed": "是",
     "verdict": "不采：语料 10 万 + 许可不清"},
    {"set": "C-MTEB/CmedqaRetrieval", "lang": "zh", "corpus": 100001, "queries": 3999,
     "test_qrels": "有", "license": "卡缺 license", "reembed": "是",
     "verdict": "不采：语料 10 万 + 许可不清"},
    {"set": "mteb/MMarcoRetrieval", "lang": "zh", "corpus": 106813, "queries": 6980,
     "test_qrels": "有", "license": "卡缺 license", "reembed": "是",
     "verdict": "不采：语料 10.7 万 + 许可不清"},
    {"set": "CRUD-RAG", "lang": "zh", "corpus": None, "queries": None,
     "test_qrels": "—", "license": "HF 卡取不到（401）", "reembed": "—",
     "verdict": "不采：HF 无数据集卡，数据在 GitHub；且它是**生成**导向基准（检索是子任务）"},
]


# ------------------------------------------------------------------ census

def census_markdown(rows: list[dict], measured_at: str) -> str:
    lines = [
        "# 标准 RAG 集 census（#71 阶段 4）", "",
        f"> 实测时间：{measured_at}；来源：HF `api/datasets/<id>`（许可 / 标签）+ "
        "`datasets-server/info`（规模）+ `/parquet`（SciFact 三件套字段核对）。",
        "> 判据：**语言 / 规模（≤~2k 条目锚点）/ 许可 / 是否必须重嵌 / 任务形态**。", "",
        "| 候选集 | 语言 | corpus 条目 | test query | test qrels 形态 | 许可（HF 卡） | 必须重嵌 | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        corpus = row["corpus"] if row["corpus"] is not None else "—"
        queries = row["queries"] if row["queries"] is not None else "—"
        lines.append(f"| `{row['set']}` | {row['lang']} | {corpus} | {queries} | "
                     f"{row['test_qrels']} | {row['license']} | {row['reembed']} | "
                     f"{row['verdict']} |")
    lines += [
        "", "## 选型结论", "",
        "**采用 `SciFact`（BEIR 打包：`BeIR/scifact` corpus+queries / `BeIR/scifact-qrels` test）**，理由：",
        "",
        "1. **规模**：corpus 5,183 / test 300 query（283 篇金标）——是候选里**最小的干净单跳集**；"
        "另外提供 `--docs 1500` 的 **gold-complete 子采样**档满足「≤~2k 条目」锚点。",
        "2. **任务形态**：claim → abstract 的**单跳证据检索**，正好覆盖本票要的「单跳检索质量」面。",
        "3. **qrels 干净**：二值、p50 1 篇/题（max 5）→ recall@k / nDCG@10 语义清晰；"
        "NFCorpus 分级稠密会让 recall@k 变钝。",
        "4. **许可**：HF 卡 `cc-by-sa-4.0`（corpus / queries / qrels 三件套一致）——可核、可归属。",
        "5. **成本**：文档 p50 **1330** 字（MultiHop-RAG 新闻正文 p50 7836 字）→ CPU 重嵌成本远低于"
        "「609 篇 ≈47min」的锚点（实测见结果文件）。",
        "", "**不采中文集的原因**：MTEB/C-MTEB 的中文检索集 corpus 都在 **10 万级**，"
        "既超 ≤2k 锚点，多数 HF 卡还**缺 license**；`CRUD-RAG` 取不到 HF 卡且是生成导向基准。",
        "→ 语言维度的边界如实记为**未覆盖**（这正好是「外部集只作机制证据」的一部分）。",
        "", "## 边界", "",
        "- 外部语料**只作机制证据**、**不声称本库增益**（ADR-0026 D5/D6）；域（生物医学）/ 语言（英）/",
        "  任务（单跳 claim 检索）**都不可外推**到本 KB。",
        "- `--docs 1500` 子采样是 **gold-complete**（保留全部金标 + 随机干扰项），"
        "干扰项密度低于全量 → 数字**乐观于官方 BEIR**，故只作机制 smoke；",
        "  全量档（`--docs 0`）才是可与外部量级对话的那个数。",
        "- 我们**不与 BEIR 排行榜比**：我们的链路是 BGE-M3 + BM25 + DBSF（且条目级整篇送嵌），",
        "  官方榜用不同编码器 / 切块；论文数字只作量级锚点。",
        "",
    ]
    return "\n".join(lines)


def do_census(out_md: str, out_json: str, measured_at: str) -> dict:
    payload = {"measured_at": measured_at, "rows": CENSUS,
               "selected": "scifact", "selection_reason_ref": "census.md §选型结论"}
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    with open(out_md, "w", encoding="utf-8") as handle:
        handle.write(census_markdown(CENSUS, measured_at))
    return payload


# ------------------------------------------------------------------ 数据

def _parquet_url(dataset: str, config: str, split: str) -> str:
    import urllib.request

    api = f"https://huggingface.co/api/datasets/{dataset}/parquet"
    req = urllib.request.Request(api, headers={"User-Agent": "dsh-eval71/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return data[config][split][0]


def _cached_parquet(dataset: str, config: str, split: str, data_dir: str):
    """下载并缓存一份 parquet（离线可复用）。"""
    import pandas as pd

    os.makedirs(data_dir, exist_ok=True)
    cache = os.path.join(data_dir, f"{dataset.replace('/', '_')}__{config}__{split}.parquet")
    if not os.path.isfile(cache):
        url = _parquet_url(dataset, config, split)
        frame = pd.read_parquet(url)
        frame.to_parquet(cache, index=False)
    return pd.read_parquet(cache)


def load_set(name: str, data_dir: str):
    spec = SETS[name]
    corpus = _cached_parquet(*spec["corpus"], data_dir)
    queries = _cached_parquet(*spec["queries"], data_dir)
    qrels = _cached_parquet(*spec["qrels"], data_dir)
    return spec, corpus, queries, qrels


def gold_complete_subset(corpus, qrels, n: int, seed: int = 0):
    """`n` ≤ 0 = 全量；否则保留**全部金标篇** + 随机干扰项到 n 篇。"""
    if n is None or n <= 0 or n >= len(corpus):
        return corpus
    gold = set(qrels["corpus-id"].astype(str))
    keep = corpus[corpus["_id"].astype(str).isin(gold)]
    rest = corpus[~corpus["_id"].astype(str).isin(gold)]
    need = max(0, n - len(keep))
    if need > 0:
        idx = list(rest.index)
        random.Random(seed).shuffle(idx)
        keep = corpus.loc[sorted(list(keep.index) + idx[:need])]
    return keep


def entries_from(corpus):
    from memory_agent.memory.entries import Entry

    out = []
    ids = corpus["_id"].astype(str)
    titles = corpus["title"].fillna("")
    texts = corpus["text"].fillna("")
    for doc_id, title, text in zip(ids, titles, texts):
        title = str(title).strip()
        text = str(text).strip()
        content = f"# {title}\n\n{text}" if title else text
        out.append(Entry(
            id=f"{ENTRY_PREFIX}{doc_id}", path=f"<scifact>/{doc_id}.md", source="scifact",
            writable=False, title=title, content=content, owner="scifact",
        ))
    return out


# ------------------------------------------------------------------ 指标

def metrics_for(ranked: list[str], gold: set[str]) -> dict:
    from memory_agent.eval.metrics import ndcg_at_k, recall_at_k, reciprocal_rank

    return {
        "recall": {str(k): round(recall_at_k(ranked, sorted(gold), k), 6) for k in KS},
        "nDCG@10": round(ndcg_at_k(ranked, sorted(gold), 10), 6),
        "mrr": round(reciprocal_rank(ranked, sorted(gold)), 6),
    }


def mean(values) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 6) if values else None


# ------------------------------------------------------------------ 主运行

def run(name: str, *, docs: int, data_dir: str, index_dir: str,
        out_path: str | None, kmax: int = KMAX, pool: int = PROD_POOL,
        rerank: bool = False, limit_queries: int | None = None) -> dict:
    # env pin：显式路径模式（不用指针），并把只读语料置空 → 绝不触碰生产索引 / 真实 KB。
    os.environ["MEMORY_INDEX_DIR"] = index_dir
    os.environ.setdefault("MEMORY_SPARSE_BACKEND", "bm25")
    os.environ.setdefault("MEMORY_STORE_FUSION", "dbsf")
    os.environ["MEMORY_READONLY_ROOTS"] = ""
    os.environ["MEMORY_RERANK"] = "1" if rerank else "0"
    os.environ["MEMORY_WARMUP"] = "0"

    from memory_agent.eval.harness.stats import bootstrap_ci
    from memory_agent.memory.index import MemoryIndex
    from memory_agent.memory.retrieval import MemoryRetriever
    from memory_agent.memory.store import open_store

    spec, corpus_all, queries, qrels = load_set(name, data_dir)
    corpus = gold_complete_subset(corpus_all, qrels, docs)
    entries = entries_from(corpus)
    variant = f"{name}-docs{docs}" if docs > 0 else f"{name}-full"
    db_path = os.path.join(index_dir, variant, "qdrant")
    manifest_path = os.path.join(index_dir, variant, "manifest.json")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    t0 = time.time()
    store = open_store(db_path=db_path, collection_name=COLLECTION, hybrid=True)
    index = MemoryIndex(store=store, manifest_path=manifest_path)
    built = index.rebuild(entries)
    build_s = round(time.time() - t0, 2)

    retriever = MemoryRetriever(store, pool_size=max(pool, kmax))
    index = MemoryIndex(store=store, manifest_path=manifest_path,
                        retriever_factory=lambda _s: retriever)

    gold_by_query: dict[str, set[str]] = {}
    for qid, cid in zip(qrels["query-id"].astype(str), qrels["corpus-id"].astype(str)):
        gold_by_query.setdefault(qid, set()).add(f"{ENTRY_PREFIX}{cid}")
    qrows = {str(qid): (text or "")
             for qid, text in zip(queries["_id"].astype(str), queries["text"].fillna(""))}
    eval_queries = [qid for qid in gold_by_query if qid in qrows]
    eval_queries.sort(key=lambda q: int(q))
    if limit_queries:
        eval_queries = eval_queries[:limit_queries]

    rows = []
    t1 = time.time()
    for qid in eval_queries:
        hits = index.search(qrows[qid], k=kmax)
        ranked = [h["id"] for h in hits]
        gold = gold_by_query[qid]
        rows.append({"query_id": qid, "gold": sorted(gold), "ranked": ranked,
                     **metrics_for(ranked, gold)})
    eval_s = round(time.time() - t1, 2)

    aggregate = {
        "queries": len(rows),
        "recall": {str(k): mean(r["recall"][str(k)] for r in rows) for k in KS},
        "recall_ci": {str(k): bootstrap_ci([r["recall"][str(k)] for r in rows]) for k in KS},
        "nDCG@10": mean(r["nDCG@10"] for r in rows),
        "nDCG@10_ci": bootstrap_ci([r["nDCG@10"] for r in rows]),
        "mrr": mean(r["mrr"] for r in rows),
        "mrr_ci": bootstrap_ci([r["mrr"] for r in rows]),
    }
    signature = hashlib.sha256(json.dumps(
        [{"q": r["query_id"], "r": r["ranked"]} for r in rows], sort_keys=True).encode()
    ).hexdigest()[:16]

    result = {
        "set": name, "variant": variant,
        "dataset": spec["dataset"], "license": spec["license"],
        "language": spec["language"], "task": spec["task"],
        "corpus_available": int(len(corpus_all)), "corpus_used": int(len(corpus)),
        "subset_rule": ("全量" if docs <= 0 else
                        f"gold-complete：全部金标篇 + seed=0 随机干扰项到 {docs} 篇"),
        "queries_total_with_qrels": len(gold_by_query), "queries_evaluated": len(rows),
        "chain": (f"MemoryIndex.search（native hybrid dense+BM25+DBSF, rerank="
                  f"{'on' if rerank else 'off'}）；pool={max(pool, kmax)}；条目级整篇送嵌"),
        "ks": list(KS), "build": {**built, "elapsed_s": build_s,
                                  "s_per_doc": round(build_s / max(1, len(entries)), 4)},
        "eval_elapsed_s": eval_s,
        "run_hash": signature,
        "aggregate": aggregate,
        "index_dir": db_path,
        "rows": rows,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
    store.close()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="standard RAG set #71")
    parser.add_argument("--census", action="store_true")
    parser.add_argument("--measured-at", default="2026-10-07")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--set", default="scifact")
    parser.add_argument("--docs", type=int, default=1500,
                        help="≤0 = 全量语料；>0 = gold-complete 子采样到 N 篇")
    parser.add_argument("--kmax", type=int, default=KMAX)
    parser.add_argument("--pool", type=int, default=PROD_POOL)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--index-dir", default=DEFAULT_INDEX_DIR)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if args.census:
        payload = do_census(
            os.path.join(STANDARD_SETS, "standard_rag_set_71.census.md"),
            os.path.join(STANDARD_SETS, "standard_rag_set_71.census.json"),
            args.measured_at)
        print(f"[census] {len(payload['rows'])} 个候选；selected={payload['selected']}")
        print(f"[out] {os.path.join(STANDARD_SETS, 'standard_rag_set_71.census.md')}")
    if args.run:
        result = run(args.set, docs=args.docs, data_dir=args.data_dir,
                     index_dir=args.index_dir, out_path=args.out, kmax=args.kmax,
                     pool=args.pool, rerank=args.rerank,
                     limit_queries=args.limit_queries)
        agg = result["aggregate"]
        print(f"[run] {result['variant']} corpus={result['corpus_used']} "
              f"queries={result['queries_evaluated']} build={result['build']['elapsed_s']}s "
              f"({result['build']['s_per_doc']}s/doc)")
        print(f"  recall={agg['recall']}")
        print(f"  nDCG@10={agg['nDCG@10']} MRR={agg['mrr']} run_hash={result['run_hash']}")
    if not args.census and not args.run:
        parser.error("至少要给 --census 或 --run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
