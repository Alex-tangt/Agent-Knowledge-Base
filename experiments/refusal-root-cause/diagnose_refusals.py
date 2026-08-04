import asyncio
import json
import os
import sys
import time

from services.rag_service import RAGService, NO_EVIDENCE_MESSAGE
from config.config import ADAPTIVE_MAX, RELEVANCE_THRESHOLD, ADAPTIVE_FACTOR

QUERIES = [
    {
        "label": "Query 1: 赔偿金倍数",
        "text": "用人单位违反本法规定解除或者终止劳动合同，应当依照经济补偿标准的几倍向劳动者支付赔偿金？",
    },
    {
        "label": "Query 2: 个人信息保护法维权",
        "text": "我发现某APP未经同意收集我的个人信息，可以依据个人信息保护法要求什么？",
    },
]

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "llm_refusal_trace.txt")


def _summarise_docs(label, retrieved):
    docs = retrieved.get("documents", [[]])[0]
    metas = retrieved.get("metadatas", [[]])[0]
    dists = retrieved.get("distances", [[]])[0]

    summary = f"--- {label} ({len(docs)} chunks) ---\n"
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        source = os.path.basename(str(meta.get("source", f"doc{i+1}")))
        first_200 = doc[:200].replace("\n", " ").replace("\r", " ")
        summary += (
            f"  [#{i+1}] dist={dist:.4f} | source={source}\n"
            f"        first_200: {first_200}...\n\n"
        )
    if len(dists) > 0:
        summary += f"  best_dist={min(dists):.4f}  worst_dist={max(dists):.4f}\n"
    summary += "\n"
    return summary


async def trace_one(label, query):
    lines = []
    lines.append("=" * 80)
    lines.append(f"TRACE: {label}")
    lines.append("=" * 80)
    lines.append("")

    svc = RAGService()

    # ----- Step 1: Rewrite -----
    t0 = time.time()
    search_query = await svc._rewrite_query(query)
    lines.append(f"[STEP 1] Query rewrite ({time.time()-t0:.2f}s)")
    lines.append(f"  rewritten -> {search_query}")
    lines.append("")

    # ----- Step 2: Hybrid retrieve -----
    t0 = time.time()
    retrieved = svc._hybrid_retrieve(search_query)
    lines.append(f"[STEP 2] Hybrid retrieve ({time.time()-t0:.2f}s)")
    lines.append(_summarise_docs("RETRIEVED (raw)", retrieved))

    # ----- Step 3: Rerank -----
    t0 = time.time()
    reranked = svc._rerank(query, retrieved)
    lines.append(f"[STEP 3] Rerank ({time.time()-t0:.2f}s)")
    lines.append(_summarise_docs("RERANKED", reranked))

    # ----- Step 4: Evidence check -----
    has_evidence = svc._has_evidence(reranked)
    lines.append(f"[STEP 4] Evidence check")
    lines.append(f"  has_evidence = {has_evidence}")
    docs_check = reranked.get("documents", [[]])[0]
    dists_check = reranked.get("distances", [[]])[0]
    lines.append(f"  chunk_count = {len(docs_check)}")
    lines.append(f"  best_distance = {min(dists_check):.4f}" if dists_check else "  best_distance = N/A")
    lines.append(f"  RELEVANCE_THRESHOLD = {RELEVANCE_THRESHOLD}")
    if dists_check and min(dists_check) > (RELEVANCE_THRESHOLD or 1.0):
        lines.append(f"  -> WOULD REFUSE: min_dist ({min(dists_check):.4f}) > threshold ({RELEVANCE_THRESHOLD})")
    else:
        lines.append(f"  -> WOULD NOT REFUSE (min_dist <= threshold)")
    lines.append("")

    # ----- Step 5: Adaptive selection -----
    adaptive = svc._select_adaptive(reranked)
    lines.append(f"[STEP 5] Adaptive selection")
    lines.append(_summarise_docs("ADAPTIVE (final context)", adaptive))

    # ----- Step 6: Build prompt -----
    sources = svc._extract_sources(adaptive)
    if svc._has_evidence(adaptive) or True:  # always build prompt for diagnostics
        rag_prompt = svc.build_rag_prompt(query, adaptive)
        lines.append("[STEP 6] RAG prompt (FULL)")
        lines.append("-" * 40)
        lines.append(rag_prompt)
        lines.append("-" * 40)
        lines.append("")
    else:
        lines.append("[STEP 6] RAG prompt — skipped (has_evidence=False)")
        lines.append("")

    # ----- Step 7: Run full pipeline -----
    lines.append("[STEP 7] Full rag_chat_stream output")
    lines.append("-" * 40)
    stream_lines = []
    async for chunk in svc.rag_chat_stream(
        [{"role": "user", "content": query}],
        kb_name=None,
        session_id="",
    ):
        stream_lines.append(chunk)
        lines.append(f"  RAW CHUNK: {chunk.rstrip()}")
    lines.append("-" * 40)
    lines.append("")

    # ----- Step 8: Verdict -----
    lines.append("[VERDICT]")
    stream_text = "".join(stream_lines)
    is_refusal = NO_EVIDENCE_MESSAGE in stream_text

    adp_docs = adaptive.get("documents", [[]])[0]
    adp_dists = adaptive.get("distances", [[]])[0]
    best_dist = min(adp_dists) if adp_dists else None
    kept_count = len(adp_docs)

    lines.append(f"  QUERY: {query[:80]}...")
    lines.append(f"  RERANKER_BEST_DIST: {best_dist}")
    lines.append(f"  ADAPTIVE_KEPT: {kept_count}/{ADAPTIVE_MAX}")
    lines.append(f"  HAS_EVIDENCE: {has_evidence}")
    lines.append(f"  IS_REFUSAL: {is_refusal}")

    # Extract LLM response
    llm_response = None
    for sl in stream_lines:
        try:
            obj = json.loads(sl)
            if obj.get("type") == "content":
                content = obj.get("content", "")
                if llm_response is None:
                    llm_response = content
                else:
                    llm_response += content
        except (json.JSONDecodeError, KeyError):
            pass

    if llm_response:
        lines.append(f"  LLM_RESPONSE: {llm_response[:300]}")
    else:
        lines.append(f"  LLM_RESPONSE: (none)")

    # Verdict
    lines.append("")
    if not is_refusal:
        lines.append("  VERDICT: ANSWERED — system did not refuse")
    elif not has_evidence:
        lines.append("  VERDICT: CORRECT_REFUSAL — reranker distances all above threshold, no usable evidence")
    else:
        lines.append("  VERDICT: WRONG_REFUSAL — has_evidence=True but LLM still refused (prompt/LLM problem)")
    lines.append("")

    return "\n".join(lines)


async def main():
    print(f"Diagnostic trace for {len(QUERIES)} queries")
    print(f"Output will be saved to: {OUTPUT_PATH}")
    print()

    all_lines = []
    all_lines.append(f"LLM Refusal Diagnostic Trace")
    all_lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    all_lines.append(f"RELEVANCE_THRESHOLD={RELEVANCE_THRESHOLD}")
    all_lines.append(f"ADAPTIVE_MAX={ADAPTIVE_MAX}")
    all_lines.append(f"ADAPTIVE_FACTOR={ADAPTIVE_FACTOR}")
    all_lines.append("")

    for q in QUERIES:
        trace = await trace_one(q["label"], q["text"])
        all_lines.append(trace)
        all_lines.append("")
        print(f"  Completed: {q['label']}")

    output = "\n".join(all_lines)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"\nDone. Full trace saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
