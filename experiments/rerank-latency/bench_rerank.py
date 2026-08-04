"""POC: bge-reranker-v2-m3 CPU 延迟基准。

测量池大小 x 文本长度 x batch_size 对 rerank 延迟的影响。
输入：data/raw/ 真实法条文本（模拟 ARTICLE_MAX_CHARS=800 的切块）。
输出：本目录 results.md。
"""
import os
import re
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW = os.path.join(REPO, "data", "raw")


def load_chunks(max_chars=800, limit=48):
    """从法条原文切出 max_chars 的片段，模拟 article-aware 切块后的长度分布。"""
    chunks = []
    for fname in sorted(os.listdir(RAW)):
        if not fname.endswith(".md"):
            continue
        with open(os.path.join(RAW, fname), encoding="utf-8") as f:
            text = re.sub(r"\n{2,}", "\n", f.read())
        for para in re.split(r"\n", text):
            para = para.strip()
            if len(para) < 50:
                continue
            if len(para) <= max_chars:
                chunks.append(para)
            else:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i:i + max_chars])
        if len(chunks) >= limit:
            break
    return chunks[:limit]


def truncate(text, n):
    return text if len(text) <= n else text[:n]


def bench(model, query, docs, trunc_n=None, batch_size=32, repeats=2):
    pairs = [[query, truncate(d, trunc_n) if trunc_n else d] for d in docs]
    times = []
    for _ in range(repeats):
        t0 = time.time()
        model.predict(pairs, batch_size=batch_size)
        times.append(time.time() - t0)
    return sum(times) / len(times), len(pairs)


def fmt(sec):
    return f"{sec * 1000:8.0f} ms"


def main():
    from sentence_transformers import CrossEncoder
    from tqdm import tqdm

    queries = [
        "用人单位违法解除劳动合同，应当如何赔偿劳动者？",
        "个人信息处理者处理个人信息应当遵循哪些原则？",
        "无证驾驶机动车发生交通事故应当承担什么责任？",
    ]

    print("loading chunks from data/raw ...")
    chunks = load_chunks()
    print(f"chunks: {len(chunks)}")

    print("loading CrossEncoder (local_files_only) ...")
    t0 = time.time()
    model = CrossEncoder(
        "BAAI/bge-reranker-v2-m3",
        trust_remote_code=True,
        local_files_only=True,
    )
    load_sec = time.time() - t0
    print(f"model loaded in {load_sec:.1f}s")

    lines = ["# rerank-latency results\n"]
    lines.append(f"- 模型加载: {load_sec:.1f}s (local_files_only, HF_HUB_OFFLINE=1)")
    lines.append(f"- 候选池来源: {len(chunks)} 条真实法条片段 (≤800字)")
    lines.append(f"- 配置: CPU, repeats=2 取均值\n")
    lines.append("| config | pool | trunc | batch | latency |")
    lines.append("|--------|------|-------|-------|---------|")

    configs = [
        # (label, pool, trunc, batch)
        ("baseline(20x800)", 20, None, 32),
        ("trunc400", 20, 400, 32),
        ("trunc300", 20, 300, 32),
        ("trunc200", 20, 200, 32),
        ("pool10-full", 10, None, 32),
        ("pool10-trunc300", 10, 300, 32),
        ("pool8-trunc300", 8, 300, 32),
        ("pool40-full", 40, None, 32),
        ("pool20-trunc300-bs16", 20, 300, 16),
        ("pool20-trunc300-bs1", 20, 300, 1),
    ]

    query = queries[0]
    print(f"query: {query}\n")
    results = {}
    for label, pool, trunc, bs in tqdm(configs, desc="bench configs", ncols=80):
        docs = chunks[:pool]
        sec, n = bench(model, query, docs, trunc_n=trunc, batch_size=bs)
        results[label] = sec
        tqdm.write(f"{label:22s} latency={fmt(sec)}")
        lines.append(f"| {label} | {pool} | {trunc if trunc else 'full'} | {bs} | {fmt(sec)} |")

    base = results["baseline(20x800)"]
    lines.append(f"\n- 基准 vs 最优: `baseline(20x800)` = {fmt(base)}，最便宜方案加速比见上表。")
    lines.append("- 结论待分析（README 回填）。")

    out = os.path.join(HERE, "results.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwritten -> {out}")


if __name__ == "__main__":
    main()
