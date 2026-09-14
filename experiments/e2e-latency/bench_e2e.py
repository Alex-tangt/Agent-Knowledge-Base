"""E2E 延迟定位：对运行中的后端逐条跑问题，解析 metadata.timing 四段耗时。

前提：legal_web 后端已在 :8000 运行且模型 ready（GET /api/status -> ready=true）。
用法：
    venv/Scripts/python.exe experiments/e2e-latency/bench_e2e.py [N]
输出：本目录 results.md + stdout 摘要。
"""
import argparse
import json
import os
import sys
import time

import requests

API = "http://127.0.0.1:8000/api/chat/stream"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
QUESTIONS = os.path.join(REPO, "tests", "questions.md")
PER_QUERY_TIMEOUT = 120.0
NO_EVIDENCE = "知识库中未找到直接依据"


def load_questions():
    qs = []
    with open(QUESTIONS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- ["):
                start = line.find("[") + 1
                end = line.find("]")
                qtype = line[start:end].strip()
                qtext = line[end + 1:].strip()
                qs.append((qtype, qtext))
    return qs


def sample(qs, n):
    if len(qs) <= n:
        return qs
    step = len(qs) / n
    out = []
    for i in range(n):
        out.append(qs[int(i * step)])
    return out


def run_query(qtype, q):
    payload = {"messages": [{"role": "user", "content": q}]}
    t0 = time.time()
    meta = None
    refusal = False
    try:
        with requests.post(f"{API}?kb_name=documents", json=payload, stream=True, timeout=PER_QUERY_TIMEOUT) as r:
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "metadata":
                    meta = obj
                elif t == "content" and NO_EVIDENCE in obj.get("content", ""):
                    refusal = True
        total = time.time() - t0
        return {"qtype": qtype, "total": total, "meta": meta, "refusal": refusal, "ok": True}
    except requests.exceptions.Timeout:
        return {"qtype": qtype, "total": time.time() - t0, "meta": None, "refusal": False, "ok": False,
                "timeout": True}
    except Exception as e:
        return {"qtype": qtype, "total": time.time() - t0, "meta": None, "refusal": False, "ok": False,
                "error": str(e)}


def main():
    from tqdm import tqdm

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    qs = sample(load_questions(), n)
    print(f"queries: {len(qs)} (sampled from tests/questions.md)")

    rows = []
    for i, (qtype, q) in enumerate(tqdm(qs, desc="queries", ncols=90, unit="q")):
        r = run_query(qtype, q)
        r["q"] = q[:40]
        rows.append(r)
        t = r.get("meta") or {}
        tm = t.get("timing") or {}
        tqdm.write(
            f"[{i + 1}/{len(qs)}] {qtype:4s} total={r['total']:6.1f}s "
            f"rewrite={tm.get('rewrite_ms', float('nan')) / 1000:5.1f}s "
            f"retrieve={tm.get('retrieve_ms', float('nan')) / 1000:5.1f}s "
            f"rerank={tm.get('rerank_ms', float('nan')) / 1000:5.1f}s "
            f"generate={tm.get('generate_ms', float('nan')) / 1000:5.1f}s "
            f"{'REFUSE' if r['refusal'] else ''}{'TIMEOUT' if not r['ok'] else ''}"
        )

    lines = ["# e2e-latency results\n"]
    lines.append(f"- 查询数: {len(rows)}，来源: tests/questions.md 抽样")
    lines.append(f"- 超时阈值: {PER_QUERY_TIMEOUT}s/条\n")
    lines.append("| # | type | total | rewrite | retrieve | rerank | generate | refuse |")
    lines.append("|---|------|-------|---------|----------|--------|----------|--------|")
    for i, r in enumerate(rows, 1):
        m = r.get("meta") or {}
        tm = m.get("timing") or {}
        lines.append(
            f"| {i} | {r['qtype']} | {r['total']:6.1f} | "
            f"{tm.get('rewrite_ms', float('nan')) / 1000:6.1f} | "
            f"{tm.get('retrieve_ms', float('nan')) / 1000:6.1f} | "
            f"{tm.get('rerank_ms', float('nan')) / 1000:6.1f} | "
            f"{tm.get('generate_ms', float('nan')) / 1000:6.1f} | "
            f"{'Y' if r['refusal'] else ''}{'T' if not r['ok'] else ''} |"
        )

    ok = [r for r in rows if r["ok"]]
    times = [r["total"] for r in ok]
    lines.append("")
    if times:
        lines.append(f"- total: mean={sum(times) / len(times):.1f}s  min={min(times):.1f}s  max={max(times):.1f}s")
        keys = ["rewrite_ms", "retrieve_ms", "rerank_ms", "generate_ms"]
        for k in keys:
            vals = [r["meta"]["timing"].get(k) for r in ok if r["meta"] and r["meta"].get("timing", {}).get(k) is not None]
            if vals:
                vals = [v / 1000 for v in vals]
                mean = sum(vals) / len(vals)
                sorted_v = sorted(vals)
                p95 = sorted_v[min(int(len(vals) * 0.95), len(vals) - 1)]
                lines.append(f"- {k}: mean={mean:.1f}s  p95={p95:.1f}s  max={max(vals):.1f}s")
    ntimeout = sum(1 for r in rows if not r["ok"])
    lines.append(f"- timeout/error: {ntimeout}/{len(rows)}")
    lines.append("- 结论待分析（README 回填）。")

    out = os.path.join(HERE, "results.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwritten -> {out}")


if __name__ == "__main__":
    main()
