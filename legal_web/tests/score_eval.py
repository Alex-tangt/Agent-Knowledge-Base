"""
RAG 自动化评测脚本（LLM-as-judge 版）

设计说明
--------
原计划用 Ragas 的多指标编排（Faithfulness / AnswerRelevancy / ContextPrecision /
ContextRecall），但本环境以 DeepSeek 作为裁判模型，Ragas 每个指标都要发起多次
LLM 调用，实测单条问题即超时（>5min），不适用于 50 题规模。因此改为**单次调用
DeepSeek 的多维度 LLM-as-judge**：一次请求返回忠实度、相关性、上下文精确率、上下文
召回、正确性（事实型）五项 0-5 分，维度与 Ragas 对齐，但调用量从“题×指标×多次”
降到“题×1”，稳定且快速。

另含两个确定性指标（无需 LLM）：
- 来源召回 source_recall：事实型问题，预期法律名是否出现在检索到的来源文件名中。
- 拒答率 refusal：无答案型问题，系统是否返回“知识库中未找到可靠依据”式拒答。

用法
----
     venv/Scripts/python.exe legal_web/tests/score_eval.py            # 全量 53 题评测（含 RAG 与 LLM-only 对比）
     venv/Scripts/python.exe legal_web/tests/score_eval.py --smoke    # 仅前 3 题（快速验证裁判模型可用）
 """

import os
import sys
import json
import re
import time
import random
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from ragcore.config.llm import require_llm
from openai import OpenAI
import run_eval
from run_eval import load_questions, query

NO_EVIDENCE_HINTS = ("知识库中未找到可靠依据", "知识库中未找到直接依据")

# ---------------------------------------------------------------------------
# 解析标准答案 ground_truth.md
# ---------------------------------------------------------------------------
def load_ground_truth(path):
    gt = {}
    cur = None
    qtype_section = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            if s.startswith("## "):
                qtype_section = s[3:].strip()
                continue
            if s.startswith("- 问题："):
                q = s[len("- 问题："):].strip()
                cur = {"type": qtype_section, "expected_source": "", "expected_answer": "", "expected": ""}
                gt[q] = cur
            elif cur is not None:
                if s.strip().startswith("预期来源："):
                    cur["expected_source"] = s.strip()[len("预期来源："):].strip()
                elif s.strip().startswith("预期答案："):
                    cur["expected_answer"] = s.strip()[len("预期答案："):].strip()
                elif s.strip().startswith("预期："):
                    cur["expected"] = s.strip()[len("预期："):].strip()
    return gt


# ---------------------------------------------------------------------------
# 同步裁判客户端（单题一次调用）
# ---------------------------------------------------------------------------
_llm = require_llm()
client = OpenAI(api_key=_llm.api_key, base_url=_llm.base_url, timeout=120, max_retries=2)

JUDGE_SYSTEM = """你是政策法规 RAG 问答系统的评测裁判。根据【问题类型】【用户问题】【检索上下文】【系统回答】以及（事实型提供的）【标准答案】，对系统回答打分。
仅输出一个 JSON 对象，键为 faithfulness, relevancy, context_precision, context_recall, correctness, rationale。各分项 0-5 整数（5 最好）。
- faithfulness（忠实度）：系统回答是否完全由检索上下文支持、无编造、无超范围推断。若回答是恰当的“知识库无依据、无法回答”式拒答且确无依据，记 5。
- relevancy（相关性）：回答是否切题、对用户有用。
- context_precision（上下文精确率）：检索到的上下文与问题是否相关、能否支撑回答。
- context_recall（上下文召回）：事实型按“标准答案的关键要点是否被检索上下文覆盖”给分；其他类型按“上下文是否足以回答该问题”给分。
- correctness（正确性）：仅事实型给出；与标准答案相比的事实正确程度（0 严重错误或编造，5 完全一致）。其他类型填 null。
注意：无答案型问题本就不应在知识库中，恰当拒答应得高分；若其反而给出了具体回答（可能编造），faithfulness/correctness 应给低分。
只输出 JSON，不要任何额外解释。"""


def judge(qtype, q, rag_ans, rag_ctx, reference):
    ctx = "\n\n".join(f"[{i+1}] {c[:600]}" for i, c in enumerate(rag_ctx[:6]))
    user = f"问题类型：{qtype}\n用户问题：{q}\n检索上下文（前若干条）：\n{ctx}\n系统回答：\n{rag_ans}\n"
    user += f"标准答案：\n{reference if reference else '（无）'}\n"
    try:
        resp = client.chat.completions.create(
            model=_llm.model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        text = resp.choices[0].message.content
        m = re.search(r"\{.*\}", text, re.DOTALL)
        obj = json.loads(m.group(0)) if m else {}
        return {
            "faithfulness": float(obj.get("faithfulness", 0) or 0),
            "relevancy": float(obj.get("relevancy", 0) or 0),
            "context_precision": float(obj.get("context_precision", 0) or 0),
            "context_recall": float(obj.get("context_recall", 0) or 0),
            "correctness": (float(obj["correctness"]) if obj.get("correctness") not in (None, "", "null") else None),
            "rationale": str(obj.get("rationale", "")),
        }
    except Exception as e:
        return {"faithfulness": 0, "relevancy": 0, "context_precision": 0,
                "context_recall": 0, "correctness": None, "rationale": f"裁判调用失败: {e}"}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="仅评测前 3 题")
    ap.add_argument("--no-llm", action="store_true", help="不调用 LLM-only 模式（更快；对比列留空）")
    ap.add_argument("--subset", type=int, default=0, metavar="N",
                    help="按题型等比例抽取共 N 题（如 --subset 18 = 每种题型各抽 N/3，用于快速回归验证）")
    ap.add_argument("--seed", type=int, default=42, help="--subset 抽样随机种子")
    ap.add_argument("--tag", default="", help="输出文件名后缀，如 --tag post_refactor → results_scored_post_refactor.md")
    ap.add_argument("--progress", default="", help="进度文件路径（每题写入 JSON，可配合 --resume 断点续跑）")
    ap.add_argument("--resume", action="store_true", help="跳过 --progress 文件中已完成的题")
    args = ap.parse_args()

    questions = load_questions(os.path.join(HERE, "questions.md"))
    gt = load_ground_truth(os.path.join(HERE, "ground_truth.md"))
    if args.smoke:
        questions = questions[:3]
    elif args.subset > 0:
        per = max(1, args.subset // 3)
        by_type = {}
        for qtype, q in questions:
            by_type.setdefault(qtype, []).append((qtype, q))
        rng = random.Random(args.seed)
        sampled = []
        for qtype in sorted(by_type):
            picked = rng.sample(by_type[qtype], min(per, len(by_type[qtype])))
            sampled.extend(picked)
        questions = sampled[:args.subset]
        print(f"[subset] 共抽取 {len(questions)} 题 (seed={args.seed})："
              f"{ {t: sum(1 for x in questions if x[0] == t) for t in set(q for q, _ in questions)} }", flush=True)

    # --- 断点续跑：读取进度文件，跳过已完成的题 ---
    progress_file = args.progress or os.path.join(HERE, "eval_progress.json")
    done_ids = set()
    if args.resume and os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                prev = json.load(f)
            done_ids = {r["q"] for r in prev.get("rows", [])}
            print(f"[resume] 进度文件中已有 {len(done_ids)} 题完成，将跳过", flush=True)
        except Exception as e:
            print(f"[resume] 读取进度文件失败，从头开始: {e}", flush=True)

    rows = []
    _start = time.time()

    def _save_progress():
        elapsed = time.time() - _start
        remaining = max(0, len(questions) - len(rows))
        eta = (elapsed / max(1, len(rows))) * remaining if rows else None
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump({
                "total": len(questions),
                "done": len(rows),
                "remaining": remaining,
                "elapsed_sec": round(elapsed, 1),
                "eta_sec": round(eta, 1) if eta is not None else None,
                "current": current_q,
                "rows": rows,
            }, f, ensure_ascii=False, indent=2)
        if eta is not None:
            print(f"[progress] {len(rows)}/{len(questions)} 完成 | 已用 {elapsed:.0f}s | 剩余预估 {eta:.0f}s", flush=True)
        else:
            print(f"[progress] {len(rows)}/{len(questions)} 完成", flush=True)

    current_q = ""
    for qtype, q in questions:
        current_q = q
        if q in done_ids:
            print(f"[skip] 已完成：[{qtype}] {q[:22]}...", flush=True)
            continue
        t_q = time.time()
        rag_ans, rag_src, rag_ctx = query(q, True)
        llm_ans = ""
        if not args.no_llm:
            try:
                llm_ans, _, _ = query(q, False)
            except Exception as e:
                llm_ans = f"(LLM-only 请求失败: {e})"
        g = gt.get(q, {})
        reference = g.get("expected_answer", "")
        is_refusal = any(h in rag_ans for h in NO_EVIDENCE_HINTS)
        # 确定性：事实型来源召回
        source_recall = None
        if qtype == "事实型" and g.get("expected_source"):
            law = g["expected_source"].split(" ")[0]
            source_recall = law in rag_src
        sc = judge(qtype, q, rag_ans, rag_ctx, reference)
        rows.append({
            "type": qtype, "q": q, "rag_ans": rag_ans, "rag_src": rag_src,
            "llm_ans": llm_ans, "ctx_count": len(rag_ctx),
            "is_refusal": is_refusal, "source_recall": source_recall, **sc,
        })
        _save_progress()
        print(f"[{len(rows)}/{len(questions)}] 评分完成：[{qtype}] {q[:22]}... "
              f"faith={sc['faithfulness']} rel={sc['relevancy']} 用时={time.time()-t_q:.0f}s", flush=True)

    # 聚合
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    def block(t):
        sub = [r for r in rows if r["type"] == t]
        if not sub:
            return {}
        return {
            "n": len(sub),
            "faithfulness": avg([r["faithfulness"] for r in sub]),
            "relevancy": avg([r["relevancy"] for r in sub]),
            "context_precision": avg([r["context_precision"] for r in sub]),
            "context_recall": avg([r["context_recall"] for r in sub]),
            "correctness": avg([r["correctness"] for r in sub]),
            "source_recall_rate": (round(sum(1 for r in sub if r["source_recall"]) / sum(1 for r in sub if r["source_recall"] is not None), 3)
                                   if any(r["source_recall"] is not None for r in sub) else None),
        }

    summary = {t: block(t) for t in ["事实型", "综合型", "无答案型"]}
    summary["总体"] = {
        "n": len(rows),
        "faithfulness": avg([r["faithfulness"] for r in rows]),
        "relevancy": avg([r["relevancy"] for r in rows]),
        "context_precision": avg([r["context_precision"] for r in rows]),
        "context_recall": avg([r["context_recall"] for r in rows]),
        "correctness": avg([r["correctness"] for r in rows]),
        "refusal_rate_noans": (round(sum(1 for r in rows if r["type"] == "无答案型" and r["is_refusal"]) /
                                     max(1, sum(1 for r in rows if r["type"] == "无答案型")), 3)
                               if any(r["type"] == "无答案型" for r in rows) else None),
        "unexpected_refusal_rate": (round(sum(1 for r in rows if r["type"] != "无答案型" and r["is_refusal"]) /
                                          max(1, sum(1 for r in rows if r["type"] != "无答案型")), 3)
                                    if any(r["type"] != "无答案型" for r in rows) else None),
    }

    # 失败判定
    failures = []
    for r in rows:
        reasons = []
        if r["type"] == "无答案型":
            if not r["is_refusal"]:
                reasons.append("无答案型未拒答（可能编造）")
        else:
            if r["is_refusal"]:
                reasons.append("应回答却拒答")
            if r["faithfulness"] < 3:
                reasons.append(f"忠实度偏低({r['faithfulness']})")
            if r["correctness"] is not None and r["correctness"] < 3:
                reasons.append(f"正确性偏低({r['correctness']})")
            if r["type"] == "事实型" and r["source_recall"] is False:
                reasons.append("来源召回失败（未检索到预期法条）")
        if reasons:
            failures.append((r, reasons))

    write_results(rows, summary, args.smoke, args.tag)
    write_failure_analysis(failures, args.smoke, args.tag)
    print("\n==== 汇总 ====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"失败样例数：{len(failures)}")


def write_results(rows, summary, smoke, tag=""):
    base = "results_scored" if not smoke else "results_scored_smoke"
    out = f"{base}_{tag}.md" if tag else f"{base}.md"
    with open(os.path.join(HERE, out), "w", encoding="utf-8") as f:
        f.write("# RAG 自动化评测结果（LLM-as-judge）\n\n")
        f.write("> 裁判模型：" + _llm.model + "（DeepSeek）。维度与 Ragas 对齐：忠实度 / 相关性 / 上下文精确率 / 上下文召回 / 正确性（事实型）。\n")
        f.write("> 另含确定性指标：来源召回（事实型法条是否被检索到）、拒答率（无答案型是否恰当拒答）。\n")
        f.write("> 本报告同时给出 **RAG 回答** 与 **LLM-only 回答**（纯大模型、不检索知识库）的逐题对比，用于评估检索增强带来的事实性提升。\n\n")
        f.write("## 汇总\n\n")
        f.write("| 维度 | 题数 | 忠实度 | 相关性 | 上下文精确率 | 上下文召回 | 正确性 | 来源召回率 |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for t in ["总体", "事实型", "综合型", "无答案型"]:
            s = summary[t]
            if not s:
                continue
            f.write(f"| {t} | {s.get('n','')} | {s.get('faithfulness','')} | {s.get('relevancy','')} | "
                    f"{s.get('context_precision','')} | {s.get('context_recall','')} | {s.get('correctness','')} | "
                    f"{s.get('source_recall_rate', s.get('refusal_rate_noans',''))} |\n")
        f.write("\n")
        # RAG vs LLM-only 对比概览（确定性统计，不依赖裁判）
        rag_refused = sum(1 for r in rows if r["is_refusal"])
        llm_refused = sum(1 for r in rows if any(h in r["llm_ans"] for h in NO_EVIDENCE_HINTS))
        rag_answered = len(rows) - rag_refused
        llm_answered = len(rows) - llm_refused
        f.write("## RAG 与 LLM-only 对比概览\n\n")
        f.write("| 指标 | RAG（检索增强） | LLM-only（纯大模型） |\n")
        f.write("|---|---|---|\n")
        f.write(f"| 总题数 | {len(rows)} | {len(rows)} |\n")
        f.write(f"| 给出回答（未拒答） | {rag_answered} | {llm_answered} |\n")
        f.write(f"| 直接拒答 | {rag_refused} | {llm_refused} |\n")
        f.write(f"| 事实型来源召回率 | {summary['事实型'].get('source_recall_rate','-') if summary.get('事实型') else '-'} | — |\n")
        f.write("\n> 说明：RAG 在“知识库无依据”类问题上通过拒答避免幻觉（忠实度 5.0）；LLM-only 不检索，对超出知识库范围的问题可能凭参数记忆编造，故二者回答风格与可靠性有本质差异。逐题对比见下文。\n\n")
        f.write("## 逐题明细\n\n")
        f.write("| 类型 | 问题 | 忠实度 | 相关性 | 精确率 | 召回 | 正确性 | 来源召回 | 拒答 | 检索条数 |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            sr = ("✓" if r["source_recall"] else "✗") if r["source_recall"] is not None else "-"
            rf = "✓" if r["is_refusal"] else ""
            f.write(f"| {r['type']} | {r['q']} | {r['faithfulness']} | {r['relevancy']} | "
                    f"{r['context_precision']} | {r['context_recall']} | {r['correctness'] if r['correctness'] is not None else '-'} | "
                    f"{sr} | {rf} | {r['ctx_count']} |\n")
        f.write("\n## 逐题回答与裁判说明\n\n")
        for i, r in enumerate(rows, 1):
            f.write(f"### {i}. [{r['type']}] {r['q']}\n")
            f.write(f"- **RAG 回答**：{r['rag_ans']}\n")
            f.write(f"- **LLM-only 回答**：{r['llm_ans']}\n")
            f.write(f"- **检索来源**：{r['rag_src']}\n")
            f.write(f"- **裁判**：忠实度 {r['faithfulness']} / 相关性 {r['relevancy']} / 精确率 {r['context_precision']} / "
                    f"召回 {r['context_recall']} / 正确性 {r['correctness'] if r['correctness'] is not None else '-'}；"
                    f"来源召回 {'✓' if r['source_recall'] else ('✗' if r['source_recall'] is False else '-')}；"
                    f"拒答 {'是' if r['is_refusal'] else '否'}\n")
            f.write(f"- **裁判说明**：{r['rationale']}\n\n")
    print(f"已写出 {out}")


def write_failure_analysis(failures, smoke, tag=""):
    base = "failure_analysis" if not smoke else "failure_analysis_smoke"
    out = f"{base}_{tag}.md" if tag else f"{base}.md"
    with open(os.path.join(HERE, out), "w", encoding="utf-8") as f:
        f.write("# 失败案例分析\n\n")
        if not failures:
            f.write("本轮评测未触发失败判定阈值，暂无失败样例。\n")
        for r, reasons in failures:
            f.write(f"## [{r['type']}] {r['q']}\n")
            f.write(f"- **失败原因**：{'；'.join(reasons)}\n")
            f.write(f"- **RAG 回答**：{r['rag_ans']}\n")
            f.write(f"- **检索来源**：{r['rag_src']}\n")
            f.write(f"- **裁判说明**：{r['rationale']}\n")
            f.write(f"- **改进建议**：（请结合检索距离/分块，补充针对性诊断）\n\n")
    print(f"已写出 {out}")


if __name__ == "__main__":
    main()
