# nav_63_results — #63 导航工具集：机制通路（§A）+ 真 LLM 端到端（§B）

> ## ⚠️ 最显眼的一句：**§B 已跑，结论 = 不显著（略负）**
>
> **在 19 条导航探针上，真 agent（qwen3.7-flash，工具导航）对比 #71 一次性 top-14 基线：
> Δ = −0.052632，95% bootstrap CI [−0.157895, 0.000000]（含 0）→ 不显著。**
> 逐条命中 13/19 vs 基线 14/19；模型用导航工具的比例 14/19（0.737）。
>
> - **§A（脚本化回放，已验）**：证明**工具可达 + `TOOL:` 派发通路 + 确定性重放**。
>   它的 paired Δ（+0.263，显著）是「**面 + 工具执行**」的上界（决策由脚本照参考动作点名，
>   **有答案泄漏**），**不是** agentic search 的显著性——**两臂分开列，别混成一个数**。
> - **§B（真 LLM，qwen3.7-flash，temperature=0，seed=42）**：数字见 §4；LLM 在环**不是逐位确定**，
>   只是一个观察值。
>
> 沿 ADR-0030 D7.5 / ADR-0026 D5/D6：外部语料 / 机制探针**只作机制证据**，不声称本库质量增益。

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

### 1.1 复跑须知（环境陷阱，2026-10-07 实测，让下一个人少踩一次）

1. **宿主 `NO_PROXY` 含带方括号的 IPv6（如 `[::1]`）** → httpx 建 client 直接抛
   `httpx.InvalidURL: Invalid port: ':1]'`（#62 只记了这一半）。§B 用本脚本的
   `--sanitize-proxy-env` 在**进程内**删掉 IPv6-ish（含 `:` / `[` / `]`）条目。
2. **本机 env 块里有重复键**（`Get-ChildItem Env:` 会报"已添加了具有相同键的项"）：
   PowerShell 里改 `$env:NO_PROXY` 对**子进程 Python 无效**——实测 `cmd /c echo %NO_PROXY%`
   看得到新值，同一命令起的 `python -c "os.environ['NO_PROXY']"` 仍是**旧值**；对照
   `$env:FOO_TEST` 能正常传。**正解 = 进程内**改 `os.environ`（`--sanitize-proxy-env` 即此）；
   在 shell 里导出代理变量这条路在本机不可靠。
3. **stderr 别合进管道**（tqdm / 模型日志被 pwsh 记成 error、包装退出码变 1）；stdout 摘要
   刻意只用 ASCII（GBK 控制台不再因 `Δ` / `−` 崩）。

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

> **语料快照**：临时副本 **277** 条（`gen-4` 副本；对照臂数字固定来自 #71 提交基线，
> 那是 **272** 条语料时的结果）。两臂口径：`baseline_entry_id ∈ 返回/展示条目`（条目级）。

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
- 首次惰性刷新把副本追平：**260 → 272** 条（263.82s，含 21 条新 embedding）；
  **merge master 后的重跑**：**272 → 277** → 再跑为 **277 → 277**（19.07s，指纹命中即跳过）。
- **生产索引目录签名 `8c330b95c09bd586` 前后一致**（`prod_index_unchanged=True`，§A 与 §B 两次都验）。
- 本票自己的产物（`memory_agent/eval/nav_63*`、`experiments/nav-tools-63/`）由本脚本的 overlay
  **显式排除**出语料（否则等于拿自己的题面当选料）。

## 4. §B 真 LLM 端到端（qwen3.7-flash, temperature=0, seed=42）— **已跑：不显著（略负）**

**设置**：同一脚本、只切 provider（`--run --agent --sanitize-proxy-env`）；
`openai-compat` @ `https://dashscope.aliyuncs.com/compatible-mode/v1`，model **qwen3.7-flash**，
`temperature=0` / `seed=42`；`Budget(max_rounds=3)`（≤2 跳）、**loop 检索 k=5**（生产默认）；
19 条可寻址探针；语料快照 **277** 条；判据 = `baseline_entry_id ∈ trace.final.evidence_ids`。

**一句话结论**：**在 19 条导航探针上，真 agent（工具导航）对比一次性 top-14 = 不显著**
（Δ **−0.052632**，95% bootstrap CI **[−0.157895, 0.000000]**，含 0；n=19 → CI 宽）。

| 臂 | n | 命中率 | MRR 口径 |
|---|---|---|---|
| **§B 真 agent** | 19 | **0.684211**（13/19） | 条目级 `evidence_ids` 命中 |
| 一次性 top-14（#71 基线） | 19 | **0.736842**（14/19） | 条目级 `hit@14` |
| **paired Δ** | 19 | **−0.052632** | CI [−0.157895, 0.000000] → **不显著** |

分面（同粒度）：

| 面 | n | agent 命中 | 一次性命中 |
|---|---|---|---|
| grep | 8 | 0.500 | 0.625 |
| read | 6 | 0.833333 | 0.833333 |
| outline | 5 | 0.800 | 0.800 |

**工具使用**：`nav_tool_use_rate = 0.736842`（**14/19** 至少调一次导航工具）；5 条**只调了
`memory_search`**（`g04` / `r04` / `o02` / `o03` / `o04`）。

**stop 分类**：`budget 7` · `fallback 5` · `answer 4` · `no_new_ids 3`。
`fallback` = 模型那一轮输出**不符合** `NEXT_QUERY:` / `TOOL:` / `ANSWER:` / `INSUFFICIENT`
任一协议 → 循环按确定的兜底停（**这是有价值的发现，没调 prompt 去凑**；对照 #48 的
「stall / 早停」口径）。5 条 fallback 里 `g04`/`r04`/`o03` 直接未命中。

**耗时**：agent 臂合计 **862.0s**（19 条）；单条 p50 ≈ 39.6s、最大 **74.4s**（`g02`）——
**无一条 >180s**，无调用失控。

**逐条明细**（hit / stop / 秒 / 导航工具调用）：

| id | face | hit | stop | 秒 | nav 工具 |
|---|---|---|---|---|---|
| nav-071-g01 | grep | ✅ | no_new_ids | 47.8 | memory_grep |
| nav-071-g02 | grep | ✅ | budget | 74.4 | memory_grep, memory_read, memory_grep |
| nav-071-g03 | grep | ❌ | budget | 64.3 | memory_grep, memory_read, memory_grep |
| nav-071-g04 | grep | ❌ | fallback | 33.9 | （只用 memory_search ×2） |
| nav-071-g05 | grep | ✅ | budget | 74.3 | memory_grep |
| nav-071-g06 | grep | ❌ | no_new_ids | 42.5 | memory_grep |
| nav-071-g07 | grep | ❌ | budget | 72.8 | memory_grep ×3 |
| nav-071-g08 | grep | ✅ | budget | 29.0 | memory_grep |
| nav-071-r01 | read | ✅ | answer | 65.6 | memory_get, memory_grep |
| nav-071-r02 | read | ✅ | budget | 74.2 | memory_grep, memory_grep, memory_read |
| nav-071-r03 | read | ✅ | answer | 38.3 | memory_grep, memory_read |
| nav-071-r04 | read | ❌ | fallback | 5.0 | （只用 memory_search） |
| nav-071-r05 | read | ✅ | no_new_ids | 53.0 | memory_grep |
| nav-071-r06 | read | ✅ | budget | 68.7 | memory_get, memory_read, memory_read |
| nav-071-o01 | outline | ✅ | answer | 37.5 | memory_outline |
| nav-071-o02 | outline | ✅ | fallback | 5.0 | （只用 memory_search，命中靠检索） |
| nav-071-o03 | outline | ❌ | fallback | 6.8 | （只用 memory_search） |
| nav-071-o04 | outline | ✅ | fallback | 29.3 | （只用 memory_search，命中靠检索） |
| nav-071-o05 | outline | ✅ | answer | 39.6 | memory_outline |

**未命中的 6 条**：`g03`/`g04`/`g06`/`g07`（目标都是 `AGENTS.md` 或 ADR-0019——**大条目 /
局部 token**，与 #71 §2.2 的 5 条检索 miss 同一批现象）、`r04`（fallback 早停）、
`o03`（fallback 早停）。其中 `g03`/`g07` **用了** `memory_grep` 但 3 轮内没把目标拉进证据。

**口径警告（必读）**：
1. **k 不对称**：loop 用生产默认 **k=5**，基线是 **top-14**。agent 可以多轮检索补偿，但每轮
   候选更少——这是本对比里一个已披露的不对称。**k=14 的对照臂本文件没跑**（避免"换口径凑显著"）；
   要补就作为**预注册的口径变体**两臂并报。
2. **LLM 在环不是逐位确定**：`temperature=0 + seed=42` 只是一个观察值，**不声称 byte-identical**；
   没做重复跑的一致性观察。
3. **n=19 → CI 宽**（±0.16）：只能支撑"不显著"，**不做分布推断**。
4. `o02`/`o04` 的 hit 是**纯检索**拿到的（模型那轮 fallback）——这正是"agent 臂包含检索臂"
   的应有语义，不是工具功劳。
5. 隔离不变：生产索引目录签名 `8c330b95c09bd586` 前后一致。

**精确复跑命令**（key 只在同一 pwsh 调用内加载；**不含 key 值**）：

```powershell
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$env:PYTHONPATH = "D:\python_work\work2026-4\wk-63-nav"
$key = (Get-Content 'D:\Study\SFT\kg-triplet-sft\.env' |
        Where-Object { $_ -match '^DASHSCOPE_API_KEY=' }) -replace '^DASHSCOPE_API_KEY=',''
if (-not $key) { throw 'DASHSCOPE_API_KEY 未找到' }
$env:MEMORY_AGENT_LLM_PROVIDER    = 'openai-compat'
$env:MEMORY_AGENT_LLM_BASE_URL    = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:MEMORY_AGENT_LLM_MODEL       = 'qwen3.7-flash'
$env:MEMORY_AGENT_LLM_API_KEY     = $key
$env:MEMORY_AGENT_LLM_TEMPERATURE = '0'
$env:MEMORY_AGENT_LLM_SEED        = '42'
& $py memory_agent/eval/nav_63_agentic.py --run --agent --sanitize-proxy-env `
    --out memory_agent/eval/nav_63_results.json
```

> 本机实测：env 块有重复键 → shell 里导出的 `NO_PROXY` 对子进程 Python **无效**，
> 故代理消毒必须在**进程内**做（`--sanitize-proxy-env`，见 §1.1）。

## 5. 边界与未决（如实标注）

1. **n = 19 → CI 宽**：§A 实测 ±0.18；§B 的 CI 上界贴 0（[−0.158, 0.000]）——只能支撑
   "不显著"，**不做分布推断**。
2. **§A 与 §B 是两回事，分开读**：§A（+0.263，显著）= 脚本决策的**面 + 工具执行上界**；
   §B（−0.053，不显著）= 真 agent 的一次观察。**不许把 §A 的显著当 agent 显著**。
3. **§B 的 k 不对称已披露**：loop `k=5`（生产默认）vs 基线 top-14；`k=14` 变体**没跑**
   （避免换口径凑显著）。要补就两臂并报。
4. **§B 不是逐位确定**：`temperature=0 + seed=42` 只是一个观察值；无重复跑一致性观察。
5. **语料是移动靶**：生产 `gen-4` 清单 260 条；临时副本首次刷新 → 272（merge 前）→ merge 后
   **277**。**对照臂数字固定来自 #71 提交的基线**（**272 条**语料）；实验臂记当时快照
   （**277 条**）。所有对比数字都按各自快照标注，`run_hash` 只对"同一快照 + 同一 query 集"有意义。
6. **`nav-071-r04` 的记录行窗已过期**（本次实际锚行 227 vs 记录 `[222,224]`）→ `--verify` 在
   r04 打 `window_shifted` **告警**（28/28 仍通过，按 spec 告警不是失败）；地图每同步一次都可能再移。
   处置归 #71 写域（Lead 定：等 #63 合完、地图冻结后由 eval-base 做一次 `--refresh` + 重跑基线收口）。
7. **本票自产物会进语料**：`memory_agent/eval/nav_63_results.md` 与
   `experiments/nav-tools-63/README.md` 含全部探针 token；本脚本的 overlay 已排除它们，
   但 `eval_71_nav.py` 的 `SELF_EXCLUDE_PREFIXES` **尚未**含这两个前缀 → Lead 已裁：归 #71 写域，
   由 eval-base 扩展前缀并重跑基线。**本票不改跨域文件。**
8. **代码面够不到**是**声明边界**（基表只装 `.md`），不是 #63 的验收缺口。
9. **§B 的 5 条 `fallback`**（26%）是模型没按 `TOOL:/NEXT_QUERY:/ANSWER:/INSUFFICIENT` 协议回
   → 循环按确定性兜底停。**没调 prompt 硬凑**；这与 #48 的 stall/早停是同一类现象，值得独立看。
