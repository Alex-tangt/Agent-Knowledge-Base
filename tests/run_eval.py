#!/usr/bin/env python3
"""RAG vs LLM-only 对比评测脚本（任务书 §2.6 测试与分析要求）。

前提：
1. 已构建知识库：在 backend/ 下运行 `python ingest.py`；
2. 后端已启动：在 backend/ 下运行 `python app.py`（默认 http://localhost:8000）。

用法：
    cd backend
    python tests/run_eval.py
输出：
    tests/results.md  （对比表格，含人工评分列待填）
"""
import json
import os
import sys

import requests

API_URL = "http://127.0.0.1:8000/api/chat/stream"
HERE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(HERE, "questions.md")
OUTPUT_FILE = os.path.join(HERE, "results.md")


def load_questions(path):
    questions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- ["):
                start = line.find("[") + 1
                end = line.find("]")
                qtype = line[start:end].strip()
                qtext = line[end + 1:].strip()
                questions.append((qtype, qtext))
    return questions


def query(question, use_rag):
    payload = {"messages": [{"role": "user", "content": question}], "use_rag": use_rag}
    try:
        resp = requests.post(API_URL, json=payload, stream=True, timeout=300)
    except requests.RequestException as e:
        return f"(请求失败: {e})", []

    answer = ""
    sources = []
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "content":
                answer += data.get("content", "")
                if data.get("sources"):
                    sources = data["sources"]
            elif data.get("type") == "metadata":
                if data.get("sources"):
                    sources = data["sources"]
            elif data.get("type") == "error":
                answer += " [错误] " + data.get("message", "")
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
        # 流式读取超时（如裁判模型生成较慢）：返回已收到的部分内容，不中断评测
        answer += f" [流式读取中断: {e}]"
    src_str = "; ".join(str(s.get("source", "?")) for s in sources) if sources else "（无来源/拒答）"
    contexts = [s.get("content", "") for s in sources] if sources else []
    return answer.strip(), src_str, contexts


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def main():
    questions = load_questions(QUESTIONS_FILE)
    if not questions:
        print("未在 questions.md 中解析到测试问题")
        sys.exit(1)

    rows = []
    for qtype, q in questions:
        try:
            rag_ans, rag_src, rag_ctx = query(q, True)
        except ValueError:
            # query() 失败时返回 (answer, src_str) 二元组，补齐第三元以免中断整轮评测
            rag_ans, rag_src = query(q, True)
            rag_ctx = []
        try:
            llm_ans, _, _ = query(q, False)
        except ValueError:
            llm_ans, _ = query(q, False)
        rows.append((qtype, q, rag_ans, rag_src, rag_ctx, llm_ans))
        print(f"已完成：[{qtype}] {q[:24]}...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# RAG vs LLM-only 对比测试结果\n\n")
        f.write("> 由 `tests/run_eval.py` 自动生成；「人工评分」列需评测人手动填写。\n\n")
        f.write("| 类型 | 问题 | RAG回答（含来源） | 检索来源 | LLM-only回答 | 人工评分 |\n")
        f.write("| ---- | ---- | ---- | ---- | ---- | ---- |\n")
        for qtype, q, rag_ans, rag_src, llm_ans in rows:
            f.write(
                f"| {qtype} | {esc(q)} | {esc(rag_ans)} | {esc(rag_src)} | {esc(llm_ans)} |  |\n"
            )
    print(f"\n结果已写入：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
