"""Run the rewrite optimizer experiment with a filtered dev set.

Filters out no-answer queries and queries with too-low orig_dist,
then adds known good queries from the original experiment.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ragcore.utils.logger import logger

CANDIDATES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dev_set_candidates.json")

ORIGINAL_QUERIES = [
    "公司没给我签合同，已经工作了6个月了，他们合法吗",
    "我在公司干了2年，老板突然说不让我来了，一分钱没给，我能要多少钱",
    "公司无辜把我辞退了我该怎么办",
]

TEST_SET = [
    "劳动合同法对试用期工资有哪些限制性规定？",
    "劳动合同约定试用期三年、试用期工资为正式工资的80%且低于当地最低工资标准，是否合法？",
    "用人单位规章制度直接涉及劳动者切身利益的，在制定和修改时应当经过什么程序？",
    "公司违法辞退员工，员工可以主张哪些救济与赔偿？",
    "我发现某APP未经同意收集我的个人信息，可以依据个人信息保护法要求什么？",
]

MIN_ORIG_DIST = 0.01


def build_dev_set():
    if not os.path.exists(CANDIDATES_FILE):
        logger.error(f"Candidates file not found: {CANDIDATES_FILE}")
        logger.info("Run select_dev_set.py first")
        return None

    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("candidates", [])
    filtered = [
        c for c in candidates
        if c["type"] != "无答案型" and c["best_dist_orig"] >= MIN_ORIG_DIST
    ]

    dev_queries = [c["query"] for c in filtered]
    dev_queries.extend(ORIGINAL_QUERIES)

    logger.info(f"Dev set built:")
    logger.info(f"  Candidates total: {len(candidates)}")
    logger.info(f"  After filtering (non-无答案, orig_dist>={MIN_ORIG_DIST}): {len(filtered)}")
    logger.info(f"  Original queries added: {len(ORIGINAL_QUERIES)}")
    logger.info(f"  Final dev set: {len(dev_queries)} queries")

    for q in dev_queries:
        logger.info(f"    {q[:50]}...")

    return dev_queries


async def main():
    dev_set = build_dev_set()
    if not dev_set:
        return

    from optimizers.rewrite_optimizer import RewriteOptimizer

    optimizer = RewriteOptimizer(
        dev_set=dev_set,
        test_set=TEST_SET,
        max_rounds=10,
        early_stop=3,
    )

    await optimizer.run()
    logger.info("Experiment complete.")


if __name__ == "__main__":
    asyncio.run(main())
