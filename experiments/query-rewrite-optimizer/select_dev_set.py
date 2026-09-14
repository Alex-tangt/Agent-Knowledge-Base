"""Dev set 筛选工具：从 questions.md 按 best_dist_orig 降序选出候选查询。

Usage（从仓库根运行）:
    python experiments/query-rewrite-optimizer/select_dev_set.py [--top N] [--output FILE]
"""
import argparse
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ragcore"))

from utils.logger import logger


def parse_questions(filepath):
    """Parse tests/questions.md, return list of (type, text)."""
    if not os.path.exists(filepath):
        logger.error(f"Questions file not found: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(r"^- \[(.*?)\]\s+(.*)$", re.MULTILINE)
    return [(m.group(1), m.group(2).strip()) for m in pattern.finditer(content)]


async def main():
    parser = argparse.ArgumentParser(description="Dev set selector")
    parser.add_argument("--top", type=int, default=15, help="Top N queries to select (default: 15)")
    parser.add_argument("--output", help="Output JSON file (stdout if not specified)")
    parser.add_argument("--questions", default=None, help="Path to questions.md")
    parser.add_argument("--kb", default="documents", help="Knowledge base name")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    questions_path = args.questions or os.path.join(repo_root, "tests", "questions.md")

    questions = parse_questions(questions_path)
    logger.info(f"Parsed {len(questions)} questions from {questions_path}")

    from eval_service import EvalService
    from services.rag_service import RAGService

    rag = RAGService()
    vs = rag.get_vector_store(args.kb)

    results = []
    for qtype, text in questions:
        try:
            retrieved = rag._rerank(text, vs.search_documents(text, k=100))
            distances = retrieved.get("distances", [[]])[0]
            best = min(distances) if distances else 1.0
            results.append({
                "type": qtype,
                "query": text,
                "best_dist_orig": round(best, 4),
            })
            logger.info(f"  [{best:.4f}] {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed for '{text[:50]}...': {e}")

    results.sort(key=lambda r: r["best_dist_orig"], reverse=True)
    selected = results[:args.top]

    output = {
        "candidates": results,
        "selected_top": args.top,
        "dev_set": selected,
        "selection_threshold": selected[-1]["best_dist_orig"] if selected else None,
    }

    json_out = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_out)
        logger.info(f"Saved to {args.output}")
    else:
        print(json_out)


if __name__ == "__main__":
    asyncio.run(main())
