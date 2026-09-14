"""去重阈值校准（#16）：在真实 KB 条目上量「应判重」与「应放行」的相似度分布。

背景（ADR-0009 D3）：`DEDUP_THRESHOLD=0.92` 是暂定 knob，偏保守（宁漏报不误报）。
本脚本给出可复现的分布与建议阈值。

方法（读路径，不写 KB）：
- 用真实 KB 的**已提交态**（git clone 到临时目录），索引只含可写条目（只读根为空）。
- 对每条目构造三种查询，走真实 `MemoryIndex.search(writable_only=True)`：
  * `full`   = `f"{title}\\n\\n{body}"` —— 与写入网关写前查询**完全一致**（精确重加）。
  * `body`   = 去掉标题前缀的正文（同义重加的近似代理）。
  * `title`  = 只有标题（极限压缩的重加代理）。
- 记录：
  * 正例分 = 该查询命中**自己**的分数（应 >= 阈值才判重）。
  * 负例分 = `full` 命中**别的条目**的最高分（>= 阈值即误报风险）。
- 输出 JSON + 在候选阈值上统计漏报/误报。

运行（仓库根，主树 venv 绝对路径）：
    venv\\Scripts\\python.exe experiments/dedup-threshold-calibration/calibrate.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SOURCE_KB = os.environ.get(
    "MEMORY_SANDBOX_SOURCE_KB", r"C:\Users\Tan\.config\opencode\knowledge"
)
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scores.json")


def _build_index(kb_dir: str, index_dir: str) -> None:
    env = os.environ.copy()
    env.update(
        AGENT_KB_DIR=kb_dir,
        MEMORY_INDEX_DIR=index_dir,
        MEMORY_READONLY_ROOTS="",
        MEMORY_REINDEX_BATCH="256",
    )
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "memory_agent", "build_index.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"build_index.py 失败：\n{proc.stderr[-2000:]}")


def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))
    return ordered[idx]


def main() -> int:
    if not os.path.isdir(os.path.join(SOURCE_KB, ".git")):
        print(f"找不到真实 KB：{SOURCE_KB}", file=sys.stderr)
        return 2

    sandbox = tempfile.mkdtemp(prefix="dedup-calibration-")
    kb_dir = os.path.join(sandbox, "kb")
    index_dir = os.path.join(sandbox, "index")
    try:
        subprocess.run(["git", "clone", "--quiet", SOURCE_KB, kb_dir], check=True)
        os.environ["AGENT_KB_DIR"] = kb_dir
        os.environ["MEMORY_INDEX_DIR"] = index_dir
        os.environ["MEMORY_READONLY_ROOTS"] = ""
        _build_index(kb_dir, index_dir)

        from memory_agent.corpus.loader import load_corpus
        from memory_agent.runtime import get_index

        corpus = load_corpus()
        index = get_index()
        n = len(corpus)
        print(f"真实 KB 可写条目：{n}")

        rows = []
        for entry in corpus:
            full = f"{entry.title}\n\n{entry.body}".strip()
            body = entry.body.strip()
            title = entry.title

            def self_and_nearest(query: str) -> tuple[float | None, float | None]:
                hits = index.search(query, k=n, writable_only=True)
                self_score = next(
                    (h["score"] for h in hits if h["id"] == entry.id), None
                )
                others = [h["score"] for h in hits if h["id"] != entry.id]
                return self_score, (max(others) if others else None)

            s_full, nearest_full = self_and_nearest(full)
            s_body, _ = self_and_nearest(body)
            s_title, _ = self_and_nearest(title)
            rows.append({
                "id": entry.id,
                "title": entry.title,
                "self_full": s_full,
                "self_body": s_body,
                "self_title": s_title,
                "nearest_other_full": nearest_full,
            })
            print(
                f"  {entry.id:52s} self_full={s_full:.3f} self_body={s_body:.3f} "
                f"self_title={s_title:.3f} nearest_other={nearest_full:.3f}"
            )

        positives_body = [r["self_body"] for r in rows]
        positives_full = [r["self_full"] for r in rows]
        negatives = [r["nearest_other_full"] for r in rows]

        candidate_thresholds = [0.80, 0.84, 0.86, 0.88, 0.90, 0.91, 0.92, 0.93,
                                0.94, 0.95, 0.96]
        sweep = []
        for t in candidate_thresholds:
            fn = sum(1 for v in positives_body if v is not None and v < t)
            fp = sum(1 for v in negatives if v is not None and v >= t)
            sweep.append({"threshold": t, "missed_body_readds": fn, "false_flags": fp})

        summary = {
            "entries": n,
            "positives_full_min": min(positives_full),
            "positives_body_min": min(positives_body),
            "positives_body_p05": _pct(positives_body, 0.05),
            "positives_body_median": _pct(positives_body, 0.5),
            "negatives_max": max(negatives),
            "negatives_p95": _pct(negatives, 0.95),
            "negatives_median": _pct(negatives, 0.5),
            "gap_min_positive_minus_max_negative": min(positives_body) - max(negatives),
            "sweep": sweep,
        }
        payload = {"summary": summary, "rows": rows}
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        print("\n==== 分布 ====")
        print(f"正例(body 重加)  min={min(positives_body):.3f} "
              f"p05={_pct(positives_body,0.05):.3f} median={_pct(positives_body,0.5):.3f}")
        print(f"正例(full 精确重加) min={min(positives_full):.3f}")
        print(f"负例(最近邻)     max={max(negatives):.3f} "
              f"p95={_pct(negatives,0.95):.3f} median={_pct(negatives,0.5):.3f}")
        print(f"间隔 = min(正例) - max(负例) = {summary['gap_min_positive_minus_max_negative']:.3f}")
        print("\n阈值 | 漏报(body重加) | 误报(最近邻)")
        for row in sweep:
            print(f"{row['threshold']:.2f} | {row['missed_body_readds']:>2d}/{n}      "
                  f"| {row['false_flags']:>2d}/{n}")
        print(f"\n已写入 {OUT_PATH}")
        return 0
    finally:
        def _on_error(func, target, _exc):  # noqa: ANN001
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
            except OSError:
                pass

        shutil.rmtree(sandbox, onerror=_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
