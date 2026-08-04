import sys
import os
import asyncio
import json
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))
os.chdir(str(backend_dir))

from services.rag_service import (
    RAGService,
    NO_EVIDENCE_MESSAGE,
    _extract_key_anchors,
    _parse_article,
    _num_to_cn,
)
from config.config import (
    RELEVANCE_THRESHOLD,
    ADAPTIVE_FACTOR,
    ADAPTIVE_MAX,
    ADAPTIVE_POOL,
)

TRUNC = 200


def trunc(text, n=TRUNC):
    return text[:n] + ("..." if len(text) > n else "")


SEP = "=" * 72
SUB = "-" * 72


def header(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(f"{SEP}")


def subhead(title):
    print(f"\n{SUB}")
    print(f"  {title}")
    print(f"{SUB}")


async def diagnose(query):
    header(f"QUERY: {query}")

    rag = RAGService()

    # ── Step 1: Query Rewriting ──
    subhead("Step 1: Query Rewriting")
    rewritten = await rag._rewrite_query(query, enable_multi=False)
    print(f"  Production (single): {rewritten}")
    rewritten_multi = await rag._rewrite_query(query, enable_multi=True)
    print(f"  Multi-rewrite:        {rewritten_multi}")

    # ── Step 2: Pure vector retrieval (original query) ──
    subhead("Step 2a: Pure Vector Retrieval (original query, k=ADAPTIVE_POOL={})".format(ADAPTIVE_POOL))
    vs = rag.vector_store
    vec_results = vs.search_documents(query, k=ADAPTIVE_POOL)
    vec_docs = vec_results["documents"][0] if vec_results.get("documents") else []
    vec_metas = vec_results["metadatas"][0] if vec_results.get("metadatas") else []
    vec_dists = vec_results["distances"][0] if vec_results.get("distances") else []
    print(f"  Total: {len(vec_docs)} chunks")
    print(f"  NOTE: Qdrant query_points scores = cosine SIMILARITY (higher = more relevant)")
    for i, (doc, meta, dist) in enumerate(zip(vec_docs[:5], vec_metas[:5], vec_dists[:5])):
        src = os.path.basename(str(meta.get("source", "?")))
        print(f"  #{i+1} score={dist:.4f}  [{src}]  {trunc(doc)}")

    # ── Step 2b: Vector retrieval per rewritten sub-query ──
    subhead("Step 2b: Vector Retrieval per rewritten sub-query")
    for sq in rewritten:
        sr = vs.search_documents(sq, k=ADAPTIVE_POOL)
        s_docs = sr["documents"][0] if sr.get("documents") else []
        s_metas = sr["metadatas"][0] if sr.get("metadatas") else []
        s_dists = sr["distances"][0] if sr.get("distances") else []
        print(f"\n  Sub:  {sq}")
        for j, (doc, meta, dist) in enumerate(zip(s_docs[:5], s_metas[:5], s_dists[:5])):
            src = os.path.basename(str(meta.get("source", "?")))
            print(f"    #{j+1} score={dist:.4f}  [{src}]  {trunc(doc)}")

    # ── Step 3: Hybrid Retrieval (production path) ──
    subhead("Step 3: Hybrid Retrieve (production path)")
    law, num = _parse_article(query)
    anchors = _extract_key_anchors(query)
    print(f"  Article parse:  law={law}, article_num={num}")
    print(f"  Anchors ({len(anchors)}): {sorted(anchors)}")
    hyb = rag._hybrid_retrieve(rewritten)
    h_docs = hyb["documents"][0] if hyb.get("documents") else []
    h_metas = hyb["metadatas"][0] if hyb.get("metadatas") else []
    h_dists = hyb["distances"][0] if hyb.get("distances") else []
    print(f"\n  Total retrieved: {len(h_docs)} chunks")
    print(f"  NOTE: keyword-matched chunks get dist=0.0, vector chunks keep cosine similarity score")
    for i, (doc, meta, dist) in enumerate(zip(h_docs[:5], h_metas[:5], h_dists[:5])):
        src = os.path.basename(str(meta.get("source", "?")))
        print(f"  #{i+1} dist={dist:.4f}  [{src}]  {trunc(doc)}")

    # ── Step 4: Reranker ──
    subhead("Step 4: Reranker")
    reranked = rag._rerank(query, hyb)
    r_docs = reranked["documents"][0] if reranked.get("documents") else []
    r_metas = reranked["metadatas"][0] if reranked.get("metadatas") else []
    r_dists = reranked["distances"][0] if reranked.get("distances") else []
    print(f"  Total reranked: {len(r_dists)} chunks")
    print(f"  NOTE: reranker dist = 1.0 - cross_encoder_score (LOWER = more relevant)")
    print(f"        score=1.0 -> dist=0.0 (perfect),  score=0.0 -> dist=1.0 (completely irrelevant)")
    for i, (doc, meta, dist) in enumerate(zip(r_docs[:8], r_metas[:8], r_dists[:8])):
        src = os.path.basename(str(meta.get("source", "?")))
        reranker_score = round(1.0 - dist, 4)
        print(f"  Rank #{i+1}  dist={dist:.4f} (score={reranker_score:.4f})  [{src}]  {trunc(doc)}")

    # ── Step 5: Adaptive Selection ──
    subhead("Step 5: Adaptive Selection")
    best_dist = r_dists[0] if r_dists else float("inf")
    threshold = best_dist * ADAPTIVE_FACTOR
    print(f"  Best reranker distance:  {best_dist:.4f}")
    print(f"  ADAPTIVE_FACTOR:         {ADAPTIVE_FACTOR}")
    print(f"  Cutoff threshold:        {threshold:.4f}  (best * factor)")
    print(f"  ADAPTIVE_MAX:            {ADAPTIVE_MAX}")
    selected = rag._select_adaptive(reranked)
    s_docs = selected["documents"][0] if selected.get("documents") else []
    s_dists = selected["distances"][0] if selected.get("distances") else []
    n_kept = len(s_docs)
    for i, d in enumerate(r_dists):
        status = "+ KEPT   " if i < n_kept else "- DROPPED"
        exceed = "  (exceeds threshold)" if d > threshold and i < n_kept else ""
        print(f"  Rank #{i+1}  dist={d:.4f}  {status}{exceed}")
    print(f"\n  Kept: {n_kept}/{ADAPTIVE_MAX}")

    # ── Step 6: Evidence Check ──
    subhead("Step 6: Evidence Check (_has_evidence)")
    has_ev = rag._has_evidence(reranked)
    best_dist = r_dists[0] if r_dists else float("inf")
    print(f"  _has_evidence(reranked):  {has_ev}")
    print(f"  Best reranker distance:   {best_dist:.4f}")
    print(f"  RELEVANCE_THRESHOLD:      {RELEVANCE_THRESHOLD}")
    if RELEVANCE_THRESHOLD is not None:
        print(f"  Check: min(dist)={best_dist:.4f} > threshold={RELEVANCE_THRESHOLD}  =>  {'REFUSE' if best_dist > RELEVANCE_THRESHOLD else 'PASS'}")

    # ── Step 7 / 8: Verdict ──
    header("VERDICT")
    if not has_ev:
        print(f"  *** REFUSAL TRIGGERED ***")
        print(f"  Best distance ({best_dist:.4f}) > threshold ({RELEVANCE_THRESHOLD})")
        print(f"  NO_EVIDENCE_MESSAGE: {NO_EVIDENCE_MESSAGE}")

        # root cause analysis
        if n_kept == 0:
            rc = "adaptive_select (zero docs kept)"
        elif best_dist > 0.70:
            rc = "reranker (low relevance scores across all candidates)"
        else:
            rc = "threshold (RELEVANCE_THRESHOLD too strict for these docs)"
    else:
        print(f"  PASSED evidence check")
        rag_prompt = rag.build_rag_prompt(query, selected)
        print(f"  Prompt chars: {len(rag_prompt)}")
        print(f"  Prompt (first 500 chars):")
        print(f"  {rag_prompt[:500]}")
        sys_prompt = (
            "你是一个政策法规问答助手。请依据提供的上下文回答用户问题，"
            "使用 [n] 标注引用来源。若上下文中包含相关信息但不完整，"
            "可以基于已知内容给出分析，同时说明局限性。"
            "仅当上下文完全不涉及问题时才说明无法回答。"
            "回答末尾注明（仅供学习参考，不构成法律意见）。"
        )
        print(f"\n  System prompt: {sys_prompt}")

        if n_kept == 0:
            rc = "adaptive_select (zero docs — would fail downstream)"
        elif best_dist > 0.50:
            rc = "borderline (evidence passed but distances are high)"
        else:
            rc = "none (should produce a normal answer)"

    print(f"\n  root_cause: {rc}")
    return has_ev, best_dist, n_kept, rc


async def main():
    queries = [
        "公司没给我签合同，已经工作了6个月了，他们合法吗",
        "我在公司干了2年，老板突然说不让我来了，一分钱没给，我能要多少钱",
        "公司无辜把我辞退了我该怎么办",
    ]

    print(SEP)
    print("  RAG FALSE REFUSAL DIAGNOSTIC TRACE")
    print(f"  Config: RELEVANCE_THRESHOLD={RELEVANCE_THRESHOLD}")
    print(f"          ADAPTIVE_FACTOR={ADAPTIVE_FACTOR}, ADAPTIVE_MAX={ADAPTIVE_MAX}")
    print(f"          ADAPTIVE_POOL={ADAPTIVE_POOL}")
    print(SEP)

    results = []
    for q in queries:
        r = await diagnose(q)
        results.append(r)
        print()  # blank line between queries

    # ── Final summary ──
    print(f"\n\n{SEP}")
    print("  FINAL SUMMARY")
    print(f"{SEP}")
    print(f"  {'VERDICT':<10} {'best_dist':>10}  {'threshold':>10}  {'kept':>5}  root_cause")
    print(f"  {'-'*10} {'-'*10}  {'-'*10}  {'-'*5}  {'-'*30}")
    for q, (has_ev, bd, kept, rc) in zip(queries, results):
        verdict = "ANSWERED" if has_ev else "REFUSED"
        print(f"  {verdict:<10} {bd:>10.4f}  {RELEVANCE_THRESHOLD:>10}  {kept:>3}/{ADAPTIVE_MAX}  {rc}")
    print(f"{SEP}")


if __name__ == "__main__":
    asyncio.run(main())
