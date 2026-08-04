# Spec: RAG Evaluation & Optimization Framework

## Problem Statement

The RAG knowledge-base chatbot has completed basic functionality (upload, ingest, chat, multi-KB routing), but development is blocked by the lack of an evaluation framework to guide optimization. The developer cannot answer: "Did my change make retrieval better or worse?" Key pain points:

- The current e2e eval (53 questions, LLM-as-judge) is too slow for iterative development.
- False refusals occur at a 22% rate, but the root cause (retrieval vs. reranker vs. LLM instruction) is unclear.
- Query rewriting has no measurable quality signal — it's a black box.
- Per-phase timing data doesn't exist, making it impossible to profile or compare approaches.
- There is no CI-compatible regression test for retrieval quality.

## Solution

A three-layer evaluation & optimization framework that provides fast feedback loops at each pipeline stage, plus an LLM-driven optimizer for query rewriting.

### Layer 1: Per-Phase Timing Instrumentation

Add timing probes to the RAG pipeline, emitting duration for: **Query Rewriting → Retrieval → Reranking → LLM Generation**. Exposed via API metadata so the frontend can display and benchmark results can be collected.

### Layer 2: Retrieval-Layer Fast Metrics

A new evaluation module that runs queries through the retrieval pipeline only (no LLM generation) and computes:

| Metric | Description | Dependency |
|--------|-------------|------------|
| `norm(Δbest_dist)` | (best_dist_orig - best_dist_rewrite) / best_dist_orig, clamped to [0,1] | Reranker |
| `intent_drift` | Does the rewritten query retain the original intent? LLM binary judgment (0/1) | LLM (one call per query) |
| `split_count` | Number of sub-queries produced by multi-query decomposition | None |

Combined into a **deviance score**: `score = 0.7 × norm(Δbest_dist) - 0.3 × intent_drift`

Evaluation uses a separate retrieval path with `pool=100` (vs production `pool=20`) to measure retrieval coverage loss. Multi-query decomposition results are merged before reranker comparison to ensure fairness.

### Layer 3: Query Rewrite Optimizer (Reflexion-style Loop)

An iterative optimization cycle driven by an optimization LLM:

```
Loop (max 10 rounds, early-stop after 3 rounds of no improvement):
  1. Run dev set (5 queries) through current rewrite prompt → collect metrics
  2. Feed results to optimization LLM with instruction:
     "Which cases regressed? What's the failure pattern?
      Is the prompt causing over-rewriting or under-rewriting?
      Propose ONE specific prompt change."
  3. Apply change → goto 1
```

Constraints:
- `max_splits = 5` (hard cap on multi-query decomposition)
- `max_rounds = 10` (total iterations)
- `early_stop = 3` rounds of no Δ improvement
- `split_count` penalty function: TBD after data collection phase (initially only recorded, no penalty)

Validation: a separate test set (5+ queries) is run once after optimization stops, to detect overfitting to the dev set.

## User Stories

1. As a developer, I want to see per-phase timing (rewrite → retrieve → rerank → generate) in the API response, so that I can identify performance bottlenecks.
2. As a developer, I want to run a fast retrieval-level evaluation that compares rewritten vs. original queries, so that I can iterate on rewrite prompts without waiting for full e2e.
3. As a developer, I want an automated optimizer that refines the query-rewriting prompt using a Reflexion-like loop, so that I don't have to manually trial-and-error prompt engineering.
4. As a developer, I want the optimizer to report per-query metrics (Δbest_dist, intent drift, split count), so that I can trace which queries improved or regressed.
5. As a developer, I want a separate test set that validates optimizer results after convergence, so that I can detect overfitting to the dev set.
6. As a developer, I want multi-query decomposition support in the rewrite step, so that complex queries can be broken into sub-queries for better retrieval coverage.
7. As a developer, I want a hard cap on split count (max 5) and early stopping (3 rounds no improvement), so that optimization runs don't waste resources.
8. As a developer, I want evaluation to use pool=100 retrieval (vs production pool=20), so that I can measure how much relevant content the production pipeline is missing.
9. As a developer, I want retrieval-layer metrics to be collected independent of LLM generation, so that I can separate retrieval quality from generation quality.
10. As a developer, I want the framework to log all optimization rounds with metrics, so that I can later design a data-driven split_count penalty function.

## Implementation Decisions

### Modules Built / Modified

**Modified:** Query rewriting in RAG service. The `_rewrite_query` method is extended to optionally return a list of sub-queries (multi-query decomposition). The existing single-query path remains the default.

**Modified:** `rag_chat_stream` gains timing probes at four boundaries: post-rewrite, post-retrieval, post-rerank, post-generation. Timing data flows through the existing streaming metadata channel.

**New:** `services/eval_service.py` — orchestrates retrieval-layer evaluation. Given a query list, it runs both original and rewritten queries through retrieval+reranking, computes metrics, and returns a structured report.

**New:** `optimizers/rewrite_optimizer.py` — implements the Reflexion-style optimization loop. Manages dev/test set separation, metric collection, optimization LLM interaction, and convergence detection.

### Architecture Decisions

1. **Evaluation uses pool=100, production unchanged at pool=20.** The `search_documents` method already accepts `top_k` — no API change needed.
2. **Multi-query results merged before reranker comparison.** All sub-queries' retrieval results are pooled and deduplicated, then the reranker scores the combined set against the original query for fair comparison.
3. **Timing data as delta durations, not absolute wall-clock.** This avoids conflating system load with code changes.
4. **Optimization LLM is the same provider as the main LLM**, but uses a separate system prompt to avoid model conflicts.
5. **split_count penalty function deferred.** The optimizer records `split_count` for each round without applying a penalty. After 1-2 optimization runs, review the split_count vs. Δbest_dist distribution to design the regularization term.

### API / Data Contracts

**New evaluation endpoint** *(future, not in initial scope)*: A script-based approach first (`python run_eval.py`), with a FastAPI endpoint as a follow-up.

**Timing metadata** added to existing stream response:
```json
{"type": "metadata", "timing": {"rewrite_ms": 120, "retrieve_ms": 340, "rerank_ms": 210, "generate_ms": 1800}}
```

**Optimizer state file** persisted as JSON:
```json
{
  "round": 3,
  "dev_set_scores": [0.45, 0.52, 0.41],
  "best_score": 0.52,
  "rounds_without_improvement": 1
}
```

## Testing Decisions

### What Makes a Good Test

- **Retrieval-layer metrics** should be deterministic given the same prompt, query, and knowledge base state — no random variation.
- **Unit tests** cover pure-logic functions (article parsing, number conversion, adaptive selection, evidence checking, fit_window). These are already identified as testable.
- **Integration tests** run the retrieval pipeline (rewrite → retrieve → rerank) on a known dev set and assert that metrics fall within expected ranges.
- **E2E tests** run full chat stream on the test set and compare LLM-as-judge scores against known baselines.

### Modules Tested

- Pure logic: `_num_to_cn()`, `_parse_article()`, `_extract_key_anchors()`, `_select_adaptive()`, `_has_evidence()`, `_extract_sources()`, `build_rag_prompt()`, `_fit_window()`, `_extract_title()`, `SessionMemory`
- Retrieval pipeline: `eval_service` runs with real Qdrant + reranker, validated against known-good query set
- Optimizer: convergence behavior on synthetic toy problems before production queries

### Prior Art

- Existing `tests/run_eval.py` — e2e RAG vs LLM-only comparison (used as the "final check" layer)
- Existing `tests/score_eval.py` — LLM-as-judge multi-dimension scoring
- LangChain's `unit_tests/` + `fake/` pattern for pure-logic testing
- DeepEval's `@observe(metrics=[...])` for retrieval-level instrumentation
- Reflexion paper (Shinn et al., 2023) for the optimization loop structure

## Out of Scope

- Production CI/CD pipeline for evaluation (manual runs first)
- Embedding or reranker model replacement / fine-tuning
- Document chunking strategy changes
- Frontend visualization of evaluation metrics
- Throughput / QPS testing (described as not needed for a personal tool)
- Real-time online evaluation from user feedback
- Broader multi-KB evaluation (only default `documents` KB targeted)
- The `split_count` regularization term design (data-collection phase only)

## Further Notes

- The pool=100 assumption has been validated on real queries. Pool=20 already captures the best chunk; pool=100 recovers 5-6 additional context chunks.
- False refusals were traced to the post-retrieval pipeline (adaptive selection + LLM instruction), not retrieval coverage. This spec does not directly address them — they remain a separate concern.
- Query rewriting was confirmed to be the highest-impact optimization lever based on root-cause analysis of the false refusal cases.
- The `Docs/rag_testing_research.md` contains the full research backing the framework design decisions.
