"""MultiHop-RAG → 本地语料 / 评测集 的共享构造（issue #47 Phase A）。

本模块只做**导入与转换**，不做检索（检索在 `run_census.py`）。它把外部数据集
（HF `yixuantt/MultiHopRAG`，ODC-BY）落成两样东西：

- **独立语料**：`data/articles/<i>.md`（frontmatter + metadata 头 + 正文）。
  用真实文件承载真相源，`Entry.from_file` 走正常解析路径；索引是派生物。
- **评测集**：`(query, evidence_list)` → 条目级 `relevant`（金标文章 id）。
  `null_query` 单独标记为 `kind=no_answer`（ADR-0017：只作描述性观察，不校阈值）。

映射口径（供复现）：语料第 i 篇 → 条目 id `multihop:<i:04d>`；evidence 用
`url` 对齐语料（609 篇 url 唯一且 evidence url 100% 命中，见 inspection）。

元数据（source / author / published_at）进入**送嵌正文**——temporal / comparison
题依赖它（对齐参考实现 `MetadataMode.LLM` 的 title/source/published_at 口径）。
"""
from __future__ import annotations

import json
import os
import statistics

from memory_agent.memory.entries import Entry
from memory_agent.settings import MAX_ENTRY_CHARS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
ARTICLES_DIR = os.path.join(DATA_DIR, "articles")
CORPUS_JSON = os.path.join(DATA_DIR, "corpus.json")
QUERIES_JSON = os.path.join(DATA_DIR, "MultiHopRAG.json")
ARTIFACTS_DIR = os.path.join(HERE, "artifacts")
EVAL_SET_JSON = os.path.join(ARTIFACTS_DIR, "eval_set.json")
INSPECTION_JSON = os.path.join(ARTIFACTS_DIR, "inspection.json")
STORE_DIR = os.path.join(HERE, "store")
STORE_DB = os.path.join(STORE_DIR, "qdrant")
MANIFEST = os.path.join(STORE_DIR, "manifest.json")

SOURCE_LABEL = "multihop"
COLLECTION = "multihop_rag"
DATASET = "yixuantt/MultiHopRAG"
LICENSE = "ODC-BY"
SOURCE_REPO = "https://github.com/yixuantt/MultiHop-RAG"


def norm(text: str) -> str:
    """与参考实现 `retrieval_evaluate.py` 同口径：折叠空白（不改大小写）。"""
    return " ".join((text or "").split())


def article_id(index: int) -> str:
    return f"{SOURCE_LABEL}:{index:04d}"


# --------------------------------------------------------------------- 语料

def load_dataset(data_dir: str = DATA_DIR) -> tuple[list[dict], list[dict]]:
    with open(os.path.join(data_dir, "corpus.json"), "r", encoding="utf-8") as handle:
        corpus = json.load(handle)
    with open(os.path.join(data_dir, "MultiHopRAG.json"), "r", encoding="utf-8") as handle:
        queries = json.load(handle)
    return corpus, queries


def metadata_header(article: dict) -> str:
    """进送嵌正文的 metadata 头（source / author / published_at）。"""
    author = article.get("author") or "(unknown)"
    return (f"Source: {article.get('source')}\n"
            f"Author: {author}\n"
            f"Published: {article.get('published_at')}\n")


def render_article(index: int, article: dict) -> str:
    """渲染一篇语料为 Markdown（frontmatter + metadata 头 + 正文）。"""
    front = [
        f"id: {article_id(index)}",
        f"title: {json.dumps(article.get('title') or '', ensure_ascii=False)}",
        f"source: {json.dumps(article.get('source') or '', ensure_ascii=False)}",
        f"url: {json.dumps(article.get('url') or '', ensure_ascii=False)}",
        f"published_at: {json.dumps(article.get('published_at') or '', ensure_ascii=False)}",
    ]
    if article.get("author"):
        front.append(f"author: {json.dumps(article['author'], ensure_ascii=False)}")
    if article.get("category"):
        front.append(f"type: {json.dumps(article['category'], ensure_ascii=False)}")
    return ("---\n" + "\n".join(front) + "\n---\n\n"
            + metadata_header(article) + "\n" + (article.get("body") or "") + "\n")


def write_corpus(corpus: list[dict], articles_dir: str = ARTICLES_DIR) -> list[str]:
    os.makedirs(articles_dir, exist_ok=True)
    paths = []
    for i, article in enumerate(corpus):
        path = os.path.join(articles_dir, f"{i:04d}.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_article(i, article))
        paths.append(path)
    return paths


def load_entries(articles_dir: str = ARTICLES_DIR) -> list[Entry]:
    """按文件序号装载条目（真相源 = `data/articles/*.md`）。"""
    names = sorted(name for name in os.listdir(articles_dir) if name.endswith(".md"))
    return [
        Entry.from_file(
            os.path.join(articles_dir, name),
            source=SOURCE_LABEL, writable=False, owner=SOURCE_LABEL, root=articles_dir,
        )
        for name in names
    ]


# ------------------------------------------------------------------ 评测集

def build_eval_set(corpus: list[dict], queries: list[dict]) -> dict:
    """`(query, evidence_list)` → 条目级评测集；`null_query` 记为 `no_answer`。"""
    id_by_url = {a["url"]: article_id(i) for i, a in enumerate(corpus)}
    out = []
    for i, item in enumerate(queries):
        qtype = item["question_type"]
        relevant: list[str] = []
        for evidence in item.get("evidence_list") or []:
            entry_id = id_by_url.get(evidence.get("url"))
            if entry_id and entry_id not in relevant:
                relevant.append(entry_id)
        row = {
            "id": f"mhr{i:04d}",
            "query": item["query"],
            "question_type": qtype,
            "kind": "no_answer" if qtype == "null_query" else "answerable",
            "relevant": [] if qtype == "null_query" else relevant,
            "evidence_count": len(relevant),
            "evidence_count_raw": len(item.get("evidence_list") or []),
        }
        out.append(row)
    return {
        "name": "multihop-rag-census",
        "version": 1,
        "dataset": DATASET,
        "license": LICENSE,
        "source_repo": SOURCE_REPO,
        "id_map": "multihop:<i:04d> -> data/corpus.json[i]",
        "queries": out,
    }


# ---------------------------------------------------------------- inspection

def _percentile(values: list[int], pct: float) -> int:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * pct), len(ordered) - 1)]


def embedding_windows(corpus: list[dict]) -> dict[str, str]:
    """每篇文章的**送嵌正文**（复刻 `Entry.embedding_text`，含 metadata 头 / 截断）。

    返回 `{url: normalized_window_text}`，供"gold fact 是否在送嵌窗口内"的离线诊断。
    """
    out = {}
    for i, article in enumerate(corpus):
        entry = Entry(
            id=article_id(i), path=os.path.join(ARTICLES_DIR, f"{i:04d}.md"),
            source=SOURCE_LABEL, writable=False,
            title=article.get("title") or "",
            content=metadata_header(article) + "\n" + (article.get("body") or ""),
            owner=SOURCE_LABEL,
        )
        out[article["url"]] = norm(entry.embedding_text(MAX_ENTRY_CHARS))
    return out


def fact_window_status(corpus: list[dict], queries: list[dict]) -> dict[tuple[str, str], str]:
    """`(query_id, entry_id) -> "all" | "partial" | "none"`。

    该题该金标文章的 gold fact 是否都落在送嵌窗口内——区分"截断表征"与"检索能力/查询表述"。
    """
    id_by_url = {a["url"]: article_id(i) for i, a in enumerate(corpus)}
    url_by_id = {article_id(i): a["url"] for i, a in enumerate(corpus)}
    windows = embedding_windows(corpus)
    out: dict[tuple[str, str], str] = {}
    for i, item in enumerate(queries):
        facts: dict[str, list[str]] = {}
        for evidence in item.get("evidence_list") or []:
            entry_id = id_by_url.get(evidence.get("url"))
            if entry_id:
                facts.setdefault(entry_id, []).append(norm(evidence.get("fact")))
        for entry_id, items in facts.items():
            inside = sum(1 for fact in items if fact in windows[url_by_id[entry_id]])
            out[(f"mhr{i:04d}", entry_id)] = (
                "all" if inside == len(items) else "none" if inside == 0 else "partial")
    return out


def inspect_dataset(corpus: list[dict], queries: list[dict]) -> dict:
    """数据集检验：构成 / evidence 篇数分布 / 语料完整性 / metadata 齐全性。"""
    urls = [a.get("url") for a in corpus]
    by_url = {a["url"]: a for a in corpus}
    lengths = [len(a.get("body") or "") for a in corpus]

    type_counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    uniq_counts: dict[str, int] = {}
    for item in queries:
        type_counts[item["question_type"]] = type_counts.get(item["question_type"], 0) + 1
        ev = item.get("evidence_list") or []
        raw_counts[str(len(ev))] = raw_counts.get(str(len(ev)), 0) + 1
        uniq = len({e.get("url") for e in ev})
        uniq_counts[str(uniq)] = uniq_counts.get(str(uniq), 0) + 1

    evidence = [e for item in queries for e in (item.get("evidence_list") or [])]
    missing_urls = sorted({e["url"] for e in evidence if e.get("url") not in by_url})
    fact_missing = 0
    window_missing = 0
    window_articles_missing = 0
    fact_window_chars = MAX_ENTRY_CHARS

    window_by_url = embedding_windows(corpus)

    for item in queries:
        ev = item.get("evidence_list") or []
        if not ev:
            continue
        article_has_fact_outside = False
        for e in ev:
            article = by_url.get(e["url"])
            if article is None:
                continue
            in_body = norm(e.get("fact")) in norm(article.get("body"))
            if not in_body:
                fact_missing += 1
            if norm(e.get("fact")) not in window_by_url[e["url"]]:
                window_missing += 1
                article_has_fact_outside = True
        if article_has_fact_outside:
            window_articles_missing += 1

    return {
        "corpus": {
            "articles": len(corpus),
            "url_unique": len(set(urls)) == len(urls),
            "title_unique": len({a.get("title") for a in corpus}) == len(corpus),
            "body_chars": {
                "min": min(lengths), "p50": _percentile(lengths, 0.50),
                "p90": _percentile(lengths, 0.90), "max": max(lengths),
                "mean": round(statistics.mean(lengths), 1),
                "over_embedding_window": sum(1 for x in lengths if x > fact_window_chars),
            },
            "metadata_missing": {
                "author_null": sum(1 for a in corpus if not a.get("author")),
                "published_at_null": sum(1 for a in corpus if not a.get("published_at")),
                "source_null": sum(1 for a in corpus if not a.get("source")),
                "category_null": sum(1 for a in corpus if not a.get("category")),
            },
        },
        "queries": {
            "total": len(queries),
            "by_question_type": dict(sorted(type_counts.items())),
            "null_query": type_counts.get("null_query", 0),
            "answerable": len(queries) - type_counts.get("null_query", 0),
            "evidence_count_raw_distribution": dict(sorted(raw_counts.items(), key=lambda kv: int(kv[0]))),
            "evidence_count_unique_distribution": dict(sorted(uniq_counts.items(), key=lambda kv: int(kv[0]))),
            "queries_with_duplicate_evidence_url": sum(
                1 for item in queries
                if len({e.get("url") for e in (item.get("evidence_list") or [])})
                != len(item.get("evidence_list") or [])),
        },
        "provenance": {
            "evidence_slots": len(evidence),
            "evidence_urls_not_in_corpus": missing_urls,
            "distinct_gold_articles": len({e["url"] for e in evidence}),
            "facts_not_substring_of_body": fact_missing,
            "facts_outside_embedding_window_chars": fact_window_chars,
            "facts_not_in_embedding_window": window_missing,
            "queries_with_any_fact_outside_window": window_articles_missing,
        },
    }


def ensure_prepared() -> tuple[dict, list[Entry]]:
    """幂等准备：写语料文件 + 评测集 + inspection（不加载模型）。"""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    corpus, queries = load_dataset()
    write_corpus(corpus)
    eval_set = build_eval_set(corpus, queries)
    with open(EVAL_SET_JSON, "w", encoding="utf-8") as handle:
        json.dump(eval_set, handle, ensure_ascii=False, indent=1)
    inspection = inspect_dataset(corpus, queries)
    with open(INSPECTION_JSON, "w", encoding="utf-8") as handle:
        json.dump(inspection, handle, ensure_ascii=False, indent=2)
    return eval_set, load_entries()
