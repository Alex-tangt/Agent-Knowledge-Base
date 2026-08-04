# E2E 延迟定位（端到端）

**问题**：单查询偶发 2-3 分钟，时间到底花在哪？孤立 reranker POC（`../rerank-latency/`）证明 rerank 20 池 ≈4.6s、上限 ~32 池 ≈7.4s，**解释不了分钟级**。

**假设**：慢在 ① 生成阶段（API 慢，此前日志见 27s 复杂题）；② embedding/检索首次加载；③ rerank 池实际比 20 大（≤32，约 7s 级）；④ 偶发 API/网络波动。

**设置**：对运行中的后端（:8000）跑 20 条混合类型问题（事实型/综合型/无答案型），逐条解析 metadata 的 `timing`（rewrite_ms / retrieve_ms / rerank_ms / generate_ms）与 `thinking_time`。脚本：`bench_e2e.py`（tqdm 进度，单条超时 120s 记为 timeout）。

**数据**：`results.md`（脚本生成）。

**结论**：

1. **rerank_ms 是绝对瓶颈**：20 条查询 total 9.0-33.5s，其中 rerank 6.9-29.1s（mean ≈15s），占总时长 60-90%。retrieve ≈0.3s、rewrite ≈1s、generate 1-3s（拒答时 0）——其余三段都可忽略。
2. **真实池比孤立 POC 假设的大**：vector(20) + keyword(≤6) + anchor(≤6) 合并去重后最多 ~32 条；且 E2E 与后端同机运行有 CPU 争用，实测 rerank 6.9-29.1s（孤立 20 池为 4.6s）。
3. **"2-3 分钟"最坏情形** = 综合型/大候选查询（anchor 命中多）+ 机器繁忙；本批 20 条未复现分钟级，但 rerank 已占绝对主导，最坏查询 33.5s。
4. 无答案型 3/4 正确拒答（REFUSE），1 条误答（q17）——供拒答调查参考。

**修复方向**：**pre-rerank 候选截断**——保留检索池 20（检索质量不变），只让 cross-encoder 重排由廉价向量分选出的 top-N 短名单（如 12）。标准做法，不损召回。截断文本无效（见 `../rerank-latency/`），不作考虑。

**数据**：`results.md`。
