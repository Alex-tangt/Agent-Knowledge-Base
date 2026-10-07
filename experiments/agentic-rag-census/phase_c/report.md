# phase_c — MultiHop-RAG → H harness trace 契约（#71）

- 证据来源：`../artifacts/per_query_ids.json`（#47 Phase A，**复用未重跑**）
- 契约：`memory_agent/trace.py（Trace JSONL）`；消费：`memory_agent.eval.harness`
- 单位：entry-level（一篇文章 = 一个条目 multihop:<i:04d>）；k = [1, 5, 10, 14, 20, 50]

## 一次性检索基线（条目级 recall@k）+ bootstrap CI

| run | r@1 | r@5 | r@10 | r@14 | r@20 | r@50 |
|---|---|---|---|---|---|---|
| base:hybrid | 0.2526 | 0.6504 | 0.7833 | 0.8470 | 0.8998 | 0.9645 |
| base:vector | 0.2459 | 0.6324 | 0.7720 | 0.8364 | 0.8922 | 0.9645 |

| run | k | 95% CI | 与 #47 公布数字一致 |
|---|---|---|---|
| base:hybrid | 1 | [0.243422, 0.261752] | ✅ |
| base:hybrid | 5 | [0.637546, 0.66286] | ✅ |
| base:hybrid | 10 | [0.772579, 0.793865] | ✅ |
| base:hybrid | 14 | [0.837583, 0.856467] | ✅ |
| base:hybrid | 20 | [0.891685, 0.907797] | ✅ |
| base:hybrid | 50 | [0.959017, 0.969734] | ✅ |
| base:vector | 1 | [0.23677, 0.255026] | — |
| base:vector | 5 | [0.61918, 0.645713] | ✅ |
| base:vector | 10 | [0.760865, 0.783186] | — |
| base:vector | 14 | [0.826644, 0.846231] | — |
| base:vector | 20 | [0.883703, 0.900628] | — |
| base:vector | 50 | [0.959017, 0.969734] | ✅ |

## harness 通路自证（按 stop 分组；answer_correct 不适用）

| run | arm(display_k) | traces | run_hash | mean_evidence_recall | gold_unreached_rate |
|---|---|---|---|---|---|
| base:hybrid | k5(5) | 2556 | c685e97779fa4e69 | 0.650369549 | 0.632816 |
| base:hybrid | k14(14) | 2556 | 86323cbcfe20cc0b | 0.847043607 | 0.355211 |
| base:vector | k5(5) | 2556 | efcfa86fab5e9410 | 0.63240946 | 0.64878 |
| base:vector | k14(14) | 2556 | 6d71a1f92ddbd416 | 0.836400591 | 0.371175 |

## 边界

1. **外部语料只作机制证据**，不声称本 KB 增益（ADR-0026 D5/D6 / ADR-0030 D7.5）。
2. 本报告**复用 #47 的不可变证据**，不是重新测量；一致性核对证明适配器消费的是同一份数据。
3. **全量 trace 不入库**（20MB 级，可一行命令重生成）：落 `%TEMP%/eval71-phase-c/trace/`；
   入库的只有契约**样例** `artifacts/sample_traces.jsonl`（前 25 条）。
4. trace 的 `query` 是**占位符**：数据集 query 文本未落盘（`per_query_ids.json` 只有序号与排名），
   故本适配只证明**契约形状与打分通路**，不重放查询文本。
5. `answer_correct` 不适用：one-shot 基线不产答案，`gold_answer=None` → scorer 回 `None`。
6. 许可/版本：`yixuantt/MultiHopRAG`（ODC-BY），参考实现 commit `c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8`（见 census README）。
7. 一致性核对容差 0.001（#47 README 的数字是四舍五入）。
