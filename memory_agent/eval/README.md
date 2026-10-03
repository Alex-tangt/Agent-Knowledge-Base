# memory_agent/eval — 运行时证据与确定性评测基座

放 memory_agent 的**运行时证据**（sandbox、dogfood、验收）与**检索评测基座**（issue #24 / #21）。
纯单测在 `tests/unit/`；这里的东西需要真实模型 / 真实索引。

## 记忆检索确定性评测基座（#24）

**对象 = 记忆检索**（不是法律 RAG）。评测链路 = **目标链路**（向量 + 关键词 + rerank），
走 `MemoryIndex.search` → `MemoryRetriever` → `ragcore` 策略 + `RerankerService`。
**不调 LLM、可重复**（同一次运行两次得到相同 `run_hash`）。

| 文件 | 作用 |
|---|---|
| `retrieval_eval_set.json` | 固化评测集：`query → 相关条目 id`（条目级二值） |
| `build_eval_set.py` | 从当前索引语料确定性抽样 + LLM 出题的**候选**生成器（provenance） |
| `retrieval_eval.py` | harness：一条命令产出 recall@k / nDCG@10 / MRR |
| `metrics.py` | 纯函数指标（单测 `tests/unit/test_eval_metrics.py`） |
| `retrieval_baseline.md` | 基线数字 + 命令 + 结论（证据） |
| `retrieval_baseline.json` / `retrieval_ablation_*.json` | 各模式逐题明细 |

### 评测集

- 51 条：45 条有答案（来源条目 = 初始 ground truth）+ 6 条**无答案**（语料外）。
- 抽样覆盖可写 KB 与三个只读仓库（`agent-knowledge-base` / `agent-infra` / `kg-triplet-sft`）。
- **标注**：LLM 依据来源条目的标题+节选生成 query（`temperature=0.7`），经 **agent 复核**——
  对 baseline 里来源非 top-1 的 8 条逐条核对内容，补入真正能回答的**共相关条目**
  （跨仓库同文如 `issue-tracker.md`/`triage-labels.md`、同主题如 ADR-0013 ↔ 可写 KB 条目），
  记录在每条 query 的 `label_review`；q042 判定为真 miss 不补。
- **人工抽检**：2026-09-15 用户确认上述 8 条补标注（q042 保持真 miss）→ 精标完成。
- 固定到索引 **gen-2**（134 条，2026-09-14 构建）。语料其后新增了若干 ADR；重建为 gen-3 会让
  语料变多、分数可能微移，需在本目录留新一版基线。

### 无答案 query 的口径

**只做描述性观察**（报告 top-1 分数分布），**不做阈值校准**——生成层决定去强制拒答、
由 LLM 在对话里说明「知识库中无相关内容」，见 `docs/adr/0017`。各模式 score 空间不同
（vector=余弦、hybrid=余弦+有界词面分（#30）、rerank=交叉编码器 logit），**只在同模式内可比**。

### 跑

```powershell
# 目标链路（基线；需 BGE-M3 + reranker，CPU ~15 分钟）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank `
  --out memory_agent/eval/retrieval_baseline.json

# 消融（快，仅 BGE-M3）
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode vector
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid

# 长跑可断点续跑：--trace <jsonl>，重拉即从已完成的 query 续
# 冒烟：--limit N
```

> `--mode hybrid` 用 `DefaultRetrievalStrategy` 的**当前默认融合**。默认融合于 **#30** 由
> 「关键词优先」改为**加法关键词增强**（`docs/adr/0022` D4）：recall@1 0.25 → **0.7074**。
> 融合对照 / 池曲线 / rerank 交互见 `experiments/fusion-selection/`。

`--out` 里的 `meta.run_hash` 是逐题结果的 sha256 截断；**两次运行同 hash = 确定性成立**。

## 检索 agent 评测 harness（#62 / ADR-0030 D7）

**评测与运行时分离**：运行时（`memory_agent/agent_loop/`）只**写** `trace`（契约
`memory_agent/trace.py`）；本 harness 只**消费** trace + 评测集。运行时**不 import 评测**
（守卫 `tests/unit/test_agent_loop_isolation.py`）。

| 文件 | 作用 |
|---|---|
| `harness/scenarios.json` | **确定性场景集**（10 条：dev 3 / holdout 7；纯数据，改场景不动逻辑） |
| `harness/scenarios.py` | 加载 + 全量 schema 校验 + 显式 `split` 留出规则（`ScenarioSetError`） |
| `harness/stubs.py` | `ScriptedLLM` + `StubTools`（回放脚本 → 零网络、零权重、逐位可复现） |
| `harness/runner.py` | in-process 驱动 `AgentLoop` 跑场景，产出/落盘 trace |
| `harness/scorer.py` | trace × 评测集 → **按 stop 分类的答案正确率** + gold 覆盖筛查（`gold_unreached`） |
| `harness/stats.py` | bootstrap CI + paired 差值（纯计算） |
| `harness/replay.py` | 确定性 replay（回放录下的 `model_output`，不调模型） |
| `harness/__main__.py` | 命令入口：跑场景集 → 报告（JSON / MD），stdout 与 `--out` 同字节 |

### 跑（验收②）

```powershell
venv\Scripts\python.exe -m memory_agent.eval.harness            # 打印报告
venv\Scripts\python.exe -m memory_agent.eval.harness --out memory_agent/eval/agent_harness_62_report.json
```

**确定性锚点**：同一命令两次 → stdout 与 `--out` **逐字节相同**（回放、无采样、无时钟、无路径；
2026-10-03 实测 sha256 `0363106c…2d6569` / 5384 字节）。报告含 **holdout** 的答案正确率 + bootstrap CI、
「不作答题不编造」率、gold 覆盖达标率、dev↔holdout paired 差值（题号无交集时 `n=0` 并明说无结论）。
区间宽度为 0 的指标标 `degenerate=true`，**不当作显著**。

**口径（ADR-0030 D7）**：答案正确率**优先**；gold 覆盖只作**筛查**（"早停率"是**上界**，
不作危害证据）。**报告里的数是 harness / 打分链路自证**——脚本 LLM 是人写的，
**不是检索质量**；本 KB 的检索增益要等 **#65（E）的 in-domain 集 + 真模型**（ADR-0030 D5/D6）。
**测试/评测固定 qwen 口径**（`openai-compat`，temp=0/seed）；生产默认 `opencode-server`（借主对话
模型分配，接入契约与真实冒烟见 `opencode_server_smoke_62_results.md`）。
