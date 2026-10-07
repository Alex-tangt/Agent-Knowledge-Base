# nav_63_results — #63 导航工具集：机制通路（§A）+ 真 LLM 端到端（§B）

> ## ⚠️ 最显眼的一句：**端到端显著性 = 见 §B 状态**
>
> - **§A（脚本化回放，已验）**：证明**工具可达 + `TOOL:` 派发通路 + 确定性重放**。
>   它的 paired Δ 是「**面 + 工具执行**」的上界（决策由脚本照参考动作点名，**有答案泄漏**），
>   **不是** agentic search 的显著性。
> - **§B（真 LLM，qwen3.7-flash）**：owner 已批；`--agent` 臂跑完前，本文件的结论一律
>   标注「**端到端显著性未验**」。跑完后本节改为真实数字 + 命令，**同一个 JSON 里两臂并存**。
>
> 沿 ADR-0030 D7.5 / ADR-0026 D5/D6：外部语料/机制探针**只作机制证据**，不声称本库质量增益。

## 0. 交付物

| 路径 | 作用 |
|---|---|
| `memory_agent/agent_loop/tools.py` | `MemoryNavToolRegistry`：六个**只读**导航工具（+ 原检索面） |
| `memory_agent/agent_loop/__init__.py` | 导出 `MemoryNavToolRegistry` / `NAV_TOOLS` |
| `tests/unit/test_nav_tools.py` | 56 条边界单测（空结果 / 行窗越界 / 条目不存在 / 过滤收窄 / 治理只收窄 / 只读 / 派发） |
| `memory_agent/eval/nav_63_agentic.py` | 评测入口：脚本臂 `--run` + agent 臂 `--agent`（provider 参数/环境可切） |
| `memory_agent/eval/nav_63_results.json` | 逐题明细（工具调用 / 命中 / 排名 / CI / 隔离快照 / run_hash） |
| `experiments/nav-tools-63/README.md` | 实验记录（问题→假设→设置→数据→结论） |

## 1. 复跑命令

```powershell
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$env:PYTHONPATH = "D:\python_work\work2026-4\wk-63-nav"

# 单测
& $py -m pytest tests/unit -q

# §A 脚本臂（无 LLM；首次会惰性刷新临时索引副本，~264s；之后 ~30s）
& $py memory_agent/eval/nav_63_agentic.py --run `
    --out memory_agent/eval/nav_63_results.json

# §B agent 臂（真 LLM；provider 写在环境变量里，脚本不改）
#   注意：key 只在同一 pwsh 调用里加载，绝不 echo / 不落盘 / 不进证据。
$key = (Get-Content 'D:\Study\SFT\kg-triplet-sft\.env' |
        Where-Object { $_ -match '^DASHSCOPE_API_KEY=' }) -replace '^DASHSCOPE_API_KEY=',''
if (-not $key) { throw 'DASHSCOPE_API_KEY 未找到' }
$env:MEMORY_AGENT_LLM_PROVIDER    = 'openai-compat'
$env:MEMORY_AGENT_LLM_BASE_URL    = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:MEMORY_AGENT_LLM_MODEL       = 'qwen3.7-flash'
$env:MEMORY_AGENT_LLM_API_KEY     = $key
$env:MEMORY_AGENT_LLM_TEMPERATURE = '0'
$env:MEMORY_AGENT_LLM_SEED        = '42'
Remove-Item Env:MEMORY_AGENT_LLM_API_KEY   # 跑完即清（可选但推荐）

# 先 2–3 条 smoke（--limit 只作用于 agent 臂），再跑满 19 条
& $py memory_agent/eval/nav_63_agentic.py --run --agent --limit 3 `
    --out memory_agent/eval/nav_63_results.json
& $py memory_agent/eval/nav_63_agentic.py --run --agent `
    --out memory_agent/eval/nav_63_results.json
```

> 运维：跑的时候**别把 stderr 合进管道**（tqdm / 模型日志会让 pwsh 记成 error）——用 `2>$null`
> 或 `2>$file`。本脚本的 stdout 摘要刻意只用 ASCII（GBK 控制台不再因 `Δ` 崩）。

## 2. LLM 可用性探测（2026-10-07，开工第一步）

| 探测 | 结果 |
|---|---|
| `opencode serve` @ 127.0.0.1:4096 | **无监听**（`Get-NetTCPConnection` 无 listener；HTTP 连接被拒） |
| `opencode_server_smoke_62.py` | **SKIP**（server 缺席）；第一次探测还暴露宿主 `NO_PROXY` 含 `[::1]` → httpx `InvalidURL`，加 `--sanitize-proxy-env` 后如实报连接拒绝 |
| `openai-compat` key | 开工时**无**（`memory_agent/.env` 不存在；进程环境无 `MEMORY_AGENT_LLM_*`）→ 走诚实路线 |
| 后续 | owner 批 qwen（`DASHSCOPE_API_KEY` 在外部 `.env`）→ §B 用 **环境变量**切换 provider，脚本零改动 |

## 3. §A 机制通路（脚本化回放 + 真工具执行）— **已验**

### 3.1 工具面 / 只读 / 治理（单测）

- **目录来自 `list_tools()`**：`["memory_search","memory_get","memory_list","memory_grep",
  "memory_read","memory_outline","memory_history","memory_links"]`（沿 ADR-0030 D7.6）。
- **只读**：源码无 `open(` / `.write(` / `os.remove` / `shutil` / git 写动词；git 白名单
  `{log,diff,blame}`；行为上 `FakeIndex` 的 `add/delete/clear` 一次都没被调用。
- **治理只可收窄**：`visibility(meta)` 由网关注入；工具参数里没有 tenant/classification/residency，
  六个工具的全部结果都先过谓词；谓词抛异常 = 看不见（不静默放宽）。
- 单测：`pytest tests/unit/test_nav_tools.py -q` → **56 passed**；全量
  `pytest tests/unit -q` → **639 passed / 3 skipped / 3 xfailed**（含 `test_agent_loop_isolation.py`，运行时零 eval import）。

### 3.2 回放（`ScriptedLLM` + 真 `AgentLoop` + 真导航工具）

- 19/19 探针 **`TOOL:` 全部派发成功**（`dispatched=True`），**命中条目全部进 `evidence_ids`**
  （`target_in_evidence=True`）；`memory_search` 在回放里**打桩返回 []**（避免加载 BGE-M3；
  检索臂不在这里度量）。
- 确定性：每探针跑两遍，`run_hash` 逐条相同；聚合 **`run_hash = ac6860b154f7e900`**
  （语料 276 → 277 条两次运行间增长，hash 不变 = 工具调用与证据不受新增无关条目影响）。

### 3.3 可达性（19 条可寻址探针）

| 面 | n | 工具臂命中 | 一次性检索 hit@14 |
|---|---|---|---|
| grep | 8 | **1.000** | 0.625 |
| read | 6 | **1.000** | 0.833 |
| outline | 5 | **1.000** | 0.800 |
| **合计** | **19** | **1.000** | **0.737** |

read 面行窗按探针 `tolerance=±10` 加宽（`nav_probes_71.spec.md` §4 ③ 就是"锚在记录行 ±10"，
位移只记 `window_shifted` 告警）——加宽后 19/19；`nav-071-r04` 的记录行窗 `[222,224]` 已不含
当前锚（实际行 **227**，锚短语仍在），加宽读窗 `[212,234]` 覆盖。

### 3.4 paired（对照 = #71 一次性 top-14 基线）

- n = **19**；工具臂 1.000 vs 检索臂 0.736842（基线 recall@14 **0.737**，口径一致）。
- **Δ = +0.263158**，95% bootstrap CI **[+0.105263, +0.473684]** → **显著**（CI 不含 0）。
- **口径警告（必读）**：这是**脚本臂**——决策由脚本照参考动作给出（**答案泄漏**），
  所以 Δ 度量的是「**面 + 工具执行**」的差；与 #71 的 `+0.263 [+0.105,+0.474]` 同轴同值，
  属**对照复现**，**不能**当 agentic search 的显著性。

### 3.5 历史面（`memory_history`，6 条）与代码边界（3 条）

- 历史面 **4/6 可达**：`h02/h03`（ADR-0030）、`h04`（AGENTS.md）、`h06`（ADR-0019）——
  用 `git log --diff-filter=A` / `-S <pattern>` 真取到期望 commit。
- **2/6 结构性不可达**：`h01`（`memory_agent/trace.py`）、`h05`（`memory-research.js`）——
  是**代码文件**，基表只装 `.md`（#71 §2 边界层）。
- 代码边界 3 条（`b01/b02/b03`）真跑工具 → **0 命中**：`memory_grep` 的 glob 内没有 `.py`
  条目，`memory_read` 无对应 entry id。这是**声明边界**，不是缺陷。

### 3.6 临时索引隔离（生产零写入）

- `MEMORY_INDEX_DIR` = `%TEMP%/eval63-nav/index`（生产 `gen-4` 的**副本**）。
- 首次惰性刷新把副本追平：**260 → 272** 条（263.82s，含 21 条新 embedding）。
- **生产索引目录签名 `8c330b95c09bd586` 前后一致**（`prod_index_unchanged=True`）。
- 本票自己的产物（`memory_agent/eval/nav_63*`、`experiments/nav-tools-63/`）由本脚本的 overlay
  **显式排除**出语料（否则等于拿自己的题面当选料）。

## 4. §B 真 LLM 端到端（qwen3.7-flash, temperature=0, seed=42）— **状态：待跑**

- 入口：`--agent`（provider 走 `MEMORY_AGENT_LLM_*` 环境变量；脚本零改动）。
- 判据：`baseline_entry_id ∈ trace.final.evidence_ids`（与 #71 同粒度，条目级）。
- 先 smoke 3 条 → 再满 19 条；paired Δ + bootstrap CI；**不显著也照实报**。
- 确定性口径：LLM 在环**不是逐位确定**；只报同 `temperature=0 + seed=42` 下重复跑的
  一致性观察值，**不声称 byte-identical**。
- 跑完后本节的 `end_to_end_significance.verified` 由 `false` 变 `true`，数字写在这里。

## 5. 边界与未决（如实标注）

1. **n = 19**：paired CI 宽（本文件实测 ±0.18）——机制探针，**不做分布推断**。
2. **§A 的决策是脚本给的**（见 §3.4 口径警告）：Δ 只到"面 + 工具执行"，**不是** agent 行为。
3. **语料是移动靶**：生产 `gen-4` 清单 260 条；临时副本首次刷新 → 272；此后主树被并行会话
   追加文档 → 276 → 277。**对照臂数字固定来自 #71 提交的基线**（272 条语料，18:30），
   工具臂在新语料上跑；新增无关条目未改变 19 条探针的命中与回放 hash（已实测）。
4. **`nav-071-r04` 的记录行窗已过期**（实际锚行 227 vs 记录 `[222,224]`）→ `--verify` 会在
   r04 打 `window_shifted` 告警（28/28 仍通过，按 spec 该告警不是失败）。**建议 eval-base/Lead
   跑一次 `eval_71_nav.py --refresh`** 把位置元数据追平（#71 文件不在本票写域）。
5. **本票自产物会进语料**：`memory_agent/eval/nav_63_results.md` 与
   `experiments/nav-tools-63/README.md` 含全部探针 token；本脚本的 overlay 已排除它们，
   但 `eval_71_nav.py` 的 `SELF_EXCLUDE_PREFIXES` **尚未**含这两个前缀 → 将来重跑 #71 `--baseline`
   前建议由该票 owner 补上（跨域，本票不改）。
6. **代码面够不到**是**声明边界**（基表只装 `.md`），不是 #63 的验收缺口。
