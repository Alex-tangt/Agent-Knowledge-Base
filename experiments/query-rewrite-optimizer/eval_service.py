import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ragcore"))

from config.config import QDRANT_COLLECTION_NAME, ADAPTIVE_POOL, USE_LOCAL_RERANKER
from utils.logger import logger


class EvalService:
    def __init__(self, kb_name="documents", pool_size=100):
        self.pool_size = pool_size
        self.kb_name = kb_name
        self._rag = None

    @property
    def rag(self):
        if self._rag is None:
            from services.rag_service import RAGService
            self._rag = RAGService()
        return self._rag

    async def _rewrite_query(self, query, rewrite_prompt=None):
        return await self.rag._rewrite_query(query, enable_multi=True, rewrite_prompt=rewrite_prompt)

    async def _check_intent_drift(self, original, rewritten):
        if original == rewritten:
            return 0.0
        try:
            response = await self.rag.client.chat.completions.create(
                model=self.rag.model,
                messages=[{
                    "role": "system",
                    "content": (
                        "Rate how well rewritten search keywords preserve the information "
                        "need of the original query. Output a single float in [0.0, 1.0]:\n\n"
                        "0.0 — All key concepts and constraints from the original query are "
                        "precisely captured in the keywords.\n"
                        "0.2 — Core topic matches, but one key entity or constraint is dropped.\n"
                        "0.5 — Same broad domain, but the specific information need has shifted.\n"
                        "0.8 — Keywords are overly vague or would retrieve unrelated content.\n"
                        "1.0 — Rewritten text is an answer/explanation rather than search "
                        "keywords, or addresses a completely different topic.\n\n"
                        "Reply with a float number only, nothing else."
                    )
                }, {
                    "role": "user",
                    "content": (
                        f"Original query: {original}\n"
                        f"Rewritten keywords: {rewritten}\n\n"
                        f"Score:"
                    )
                }],
                temperature=0,
                max_tokens=10,
            )
            answer = response.choices[0].message.content.strip()
            score = float(answer)
            return max(0.0, min(1.0, score))
        except Exception as e:
            logger.warning(f"Intent drift check failed: {e}")
            return 0.5

    def _retrieve(self, query, vs):
        results = vs.search_documents(query, k=self.pool_size)
        return self.rag._rerank(query, results)

    def _retrieve_multi(self, queries, vs):
        all_docs, all_metas, all_dists = [], [], []
        seen = set()
        for sq in queries:
            results = vs.search_documents(sq, k=self.pool_size)
            ranked = self.rag._rerank(sq, results)
            docs = ranked.get("documents", [[]])[0]
            metas = ranked.get("metadatas", [[]])[0]
            dists = ranked.get("distances", [[]])[0]
            for d, mt, dist in zip(docs, metas, dists):
                if d not in seen:
                    seen.add(d)
                    all_docs.append(d)
                    all_metas.append(mt)
                    all_dists.append(dist)
        return {
            "documents": [all_docs],
            "metadatas": [all_metas],
            "distances": [all_dists],
        }

    def _best_dist(self, retrieved):
        distances = retrieved.get("distances", [[]])[0]
        return min(distances) if distances else 1.0

    async def evaluate_single(self, query, rewrite_prompt=None):
        try:
            rewritten = await self._rewrite_query(query, rewrite_prompt=rewrite_prompt)
            vs = self.rag.get_vector_store(self.kb_name)

            original_results = self._retrieve(query, vs)

            if len(rewritten) == 1:
                rewritten_results = self._retrieve(rewritten[0], vs)
            else:
                rewritten_results = self._retrieve_multi(rewritten, vs)

            split_count = len(rewritten)

            best_dist_orig = self._best_dist(original_results)
            best_dist_rewrite = self._best_dist(rewritten_results)

            if best_dist_orig > 0:
                norm_delta = (best_dist_orig - best_dist_rewrite) / best_dist_orig
            else:
                norm_delta = 0.0
            norm_delta = max(0.0, min(1.0, norm_delta))

            rewritten_concat = " | ".join(rewritten)
            intent_drift = await self._check_intent_drift(query, rewritten_concat)

            score = 0.7 * norm_delta - 0.1 * intent_drift
            score = max(-0.1, min(0.7, score))

            return {
                "query": query,
                "rewritten": rewritten,
                "best_dist_orig": round(best_dist_orig, 4),
                "best_dist_rewrite": round(best_dist_rewrite, 4),
                "norm_delta_best_dist": round(norm_delta, 4),
                "intent_drift": intent_drift,
                "split_count": split_count,
                "score": round(score, 4),
            }
        except Exception as e:
            logger.error(f"Evaluation failed for query '{query}': {e}")
            return {
                "query": query,
                "rewritten": [query],
                "norm_delta_best_dist": 0,
                "intent_drift": 0,
                "split_count": 1,
                "score": 0,
                "error": str(e),
            }

    async def evaluate(self, queries, rewrite_prompt=None):
        results = []
        for query in queries:
            logger.info(f"Evaluating: {query}")
            result = await self.evaluate_single(query, rewrite_prompt=rewrite_prompt)
            results.append(result)

        valid_scores = [r["score"] for r in results]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0

        valid_deltas = [r["norm_delta_best_dist"] for r in results]
        avg_delta = sum(valid_deltas) / len(valid_deltas) if valid_deltas else 0

        valid_drifts = [r["intent_drift"] for r in results]
        avg_drift = sum(valid_drifts) / len(valid_drifts) if valid_drifts else 0

        return {
            "summary": {
                "avg_score": round(avg_score, 4),
                "avg_delta_dist": round(avg_delta, 4),
                "avg_intent_drift": round(avg_drift, 4),
            },
            "per_query": results,
        }

    def evaluate_sync(self, queries):
        return asyncio.run(self.evaluate(queries))


DEFAULT_QUERIES = [
    "劳动合同法规定试用期最长是多久？",
    "消费者在网购中享有哪些基本权利？",
    "个人信息保护法对企业数据收集有什么要求？",
    "食品安全法对食品添加剂有什么规定？",
    "未成年人保护法对网络游戏有什么限制？",
]

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Retrieval-layer evaluation service")
    parser.add_argument("--kb", default=QDRANT_COLLECTION_NAME, help="Knowledge base name")
    parser.add_argument("--pool", type=int, default=100, help="Retrieval pool size")
    parser.add_argument("--output", help="Output JSON file path (stdout if not specified)")
    parser.add_argument("queries", nargs="*", help="Queries to evaluate")
    args = parser.parse_args()

    queries = args.queries if args.queries else DEFAULT_QUERIES

    svc = EvalService(kb_name=args.kb, pool_size=args.pool)
    logger.info(f"Starting eval with {len(queries)} queries, kb={args.kb}, pool={args.pool}")
    start = time.time()
    result = svc.evaluate_sync(queries)
    elapsed = time.time() - start
    logger.info(f"Eval completed in {elapsed:.1f}s")

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        logger.info(f"Results written to {args.output}")

    print(output)
