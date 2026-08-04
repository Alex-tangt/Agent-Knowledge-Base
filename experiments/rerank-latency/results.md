# rerank-latency results

- 模型加载: 3.7s (local_files_only, HF_HUB_OFFLINE=1)
- 候选池来源: 48 条真实法条片段 (≤800字)
- 配置: CPU, repeats=2 取均值

| config | pool | trunc | batch | latency |
|--------|------|-------|-------|---------|
| baseline(20x800) | 20 | full | 32 |     4622 ms |
| trunc400 | 20 | 400 | 32 |     4522 ms |
| trunc300 | 20 | 300 | 32 |     4441 ms |
| trunc200 | 20 | 200 | 32 |     4484 ms |
| pool10-full | 10 | full | 32 |     2419 ms |
| pool10-trunc300 | 10 | 300 | 32 |     2456 ms |
| pool8-trunc300 | 8 | 300 | 32 |     2040 ms |
| pool40-full | 40 | full | 32 |     9239 ms |
| pool20-trunc300-bs16 | 20 | 300 | 16 |     4341 ms |
| pool20-trunc300-bs1 | 20 | 300 | 1 |     8064 ms |

- 基准 vs 最优: `baseline(20x800)` =     4622 ms，最便宜方案加速比见上表。
- 结论待分析（README 回填）。
