# nav-tools-63 — 检索导航工具集（#63 / 线② A）实验记录

> 一页结论：**工具做出来了、是只读的、走 `TOOL:` 协议可达、回放逐条确定**（§A）；
> **真 agent（qwen3.7-flash）在 19 条导航探针上对比一次性 top-14 = 不显著**（§B，Δ −0.053）。

## 问题

检索 agent 现在只有 `memory_search`（top-k 语义检索）+ `memory_get`（整条读回）。
`#71` 的探针显示这条面**结构性够不到**四类导航能力：词典外 token 的**字面精搜**（grep）、
**行窗读回**（省 token）、**标题结构**（outline）、**时间轴**（git history）。#63 要加的是
「像人一样由宽到窄导航」的工具面。

## 假设

1. 六个**只读**导航工具（list / grep / read / outline / history / links）能覆盖 #71 的
   19 条可寻址探针（grep 8 / read 6 / outline 5）与 4 条 `.md` 历史探针；
2. 工具目录经 `ToolRegistry.list_tools()` 暴露、派发仍走 `TOOL:` 协议（ADR-0030 D7.6），
   `AgentLoop` 不需要改；
3. 治理过滤（tenant / classification / residency）在工具层**只可收窄**；
4. 用**脚本化 LLM 回放**可以在无 LLM 的机器上证明通路，并把「端到端显著性」明确留白。

## 设置

- 运行时：`memory_agent/agent_loop/tools.py::MemoryNavToolRegistry`（6 工具 + 原 2 工具）。
- 评测入口：`memory_agent/eval/nav_63_agentic.py`
  - **脚本臂 `--run`（默认）**：`harness.stubs.ScriptedLLM` 把决策写死（照 #71 参考动作），
    执行走真工具 → 真索引 / 真文件 / 真 `git`；每探针跑两遍核对 `run_hash`。
  - **agent 臂 `--agent`**：真 LLM（`opencode-server` 或 `openai-compat`），模型自己选工具；
    provider 由 `--provider/--base-url/--model` 或 `MEMORY_AGENT_LLM_*` 环境变量切换。
- 对照臂：`memory_agent/eval/eval_71_nav_baseline.json`（#71 提交的 19 条一次性 top-14 基线）。
- 隔离：`MEMORY_INDEX_DIR` 指向生产 `gen-4` 的**临时副本**；生产索引目录签名前后比对。
- 判命中：**照 `standard_sets/nav_probes_71.spec.md`** ——① 文件存在 ② 锚短语仍在
  ③ 位置在记录值 ±10（read 的行窗按 ±tolerance 加宽；位移只记 `window_shifted` 告警）。
- 证据：`memory_agent/eval/nav_63_results.md`（§A 机制 / §B 真 LLM）。

## 数据（2026-10-07，§A）

| 指标 | 值 |
|---|---|
| 单测 | `tests/unit/test_nav_tools.py` **56 passed**；全量 **639 passed / 3 skipped / 3 xfailed** |
| 回放派发 | **19/19** `TOOL:` 派发成功；命中条目全进 `evidence_ids` |
| 回放确定性 | 每探针两遍 `run_hash` 相同；聚合 **`ac6860b154f7e900`** |
| 可达性 | grep 8/8 · read 6/6 · outline 5/5 → **19/19** |
| 历史面 | **4/6**（2 条是代码文件，基表只装 `.md`） |
| 代码边界 | 3/3 **不可达**（声明边界，非缺陷） |
| paired（脚本臂 − 一次性@14） | **Δ +0.263158**，CI **[+0.105263, +0.473684]**（显著）；n=19 |
| 临时索引 | 副本 260 → 272（首次刷新 263.82s）→ merge 后 277；生产签名 `8c330b95c09bd586` 前后一致 |

### §B 真 LLM（qwen3.7-flash / temperature=0 / seed=42；n=19，语料快照 277）

| 指标 | 值 |
|---|---|
| agent 命中率 | **0.684211**（13/19） |
| 一次性 top-14 命中率 | 0.736842（14/19，来自 #71 基线，快照 272） |
| **paired Δ** | **−0.052632**，95% CI **[−0.157895, 0.000000]** → **不显著** |
| 分面 | grep 0.500 vs 0.625 · read 0.833 vs 0.833 · outline 0.800 vs 0.800 |
| `nav_tool_use_rate` | **0.736842**（14/19；5 条只用了 `memory_search`） |
| stop 分类 | budget 7 · **fallback 5** · answer 4 · no_new_ids 3 |
| 耗时 | 合计 862.0s；单条最大 74.4s（无 >180s、无调用失控） |

**§B 口径**：同一脚本只切 provider（`--run --agent --sanitize-proxy-env`）；loop `k=5`
（生产默认）vs 基线 top-14（**不对称已披露**，`k=14` 变体没跑）；LLM 在环**不是逐位确定**。

## 结论

1. **通路成立**：六个工具经 `list_tools()` 可达、`TOOL:` 派发有结果、结果进证据、回放逐条确定；
   全量单测零回归（584 → 639）。
2. **只有只读**：源码无写动词、git 子命令白名单 `{log,diff,blame}`、索引写方法零调用；
   治理谓词先于一切结果，参数无法放宽。
3. **脚本臂的 Δ（+0.263）与 #71 参考动作 Δ（+0.263）同轴同值**——这是**对照复现**，
   证明"真工具能做到参考动作能做的事"，**不是** agent 的检索能力（决策由脚本给出、有答案泄漏）。
4. **真 LLM 端到端（§B）不显著**：19 条上 Δ −0.053、CI 含 0；模型**确实用了**导航工具
   （14/19），但 5 条 `fallback`（输出不符协议 → 确定性兜底停，**没调 prompt 凑**）拖住了
   grep 面（0.500 vs 0.625）。**"agentic search 显著优于一次性检索"在本集上不成立**——
   如实报，不合并 §A 的上界。
5. **边界不变**：不碰检索默认 / 合成、不改 `trace.py` 契约、不改 `loop.py` 确定性停止语义；
   生产索引零写入（签名前后一致）。

## 复跑

见 `memory_agent/eval/nav_63_results.md` §1（含 §B 的 key 加载步骤，**不含 key 值**）。
