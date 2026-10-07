# nav_probes_71 — 导航机制探针口径（#71）

> 数据：`nav_probes_71.json`（28 条）。**机制探针，不是 in-domain 评测集**——in-domain 集归 #65（E），
> 本集只考 #63 导航工具集的**测量面**，并给 #63 提供 paired 对照基线（一次性检索）。

## 1. 为什么要有这组探针

标准 RAG 集（MultiHop-RAG / BEIR 系）只覆盖**语义相似检索**这一个面。`#63` 要加的是
`grep` / `read`（按行范围）/ `outline`（标题结构）/ `history`（git log·diff·blame）四种**导航动作**——
它们的能力与"把 query 送进向量库取 top-k"**不同轴**：

- `grep` 对**词典外 token**（sha、env 名、编号）是精确能力，向量/BM25 都会碎成子词；
- `read` 给的是**行窗**（省 token、可指认），一次性检索只能给整条；
- `outline` 给的是**结构**（标题树），索引里没有这一层；
- `history` 给的是**时间轴**（某行何时被改），索引只有终态快照；
- 还有**边界**：只读语料只索引 `.md`，代码文件根本不进索引（`nav-071-b0*`）。

没有任何公开标准集覆盖这四类动作，所以要自造——但**只作机制探针**，不声称任何本库质量增益
（ADR-0026 D5/D6 / ADR-0030 D7.5）。

## 2. 对象与作用域

| scope | 对象 | 说明 |
|---|---|---|
| `corpus` | 仓库内 `.md`（只读语料，label `agent-knowledge-base`） | 复刻 `memory_agent/corpus/loader.py`：`EXCLUDE_DIR_NAMES` + `EXCLUDE_REL_PREFIXES` + 1MB 上限 |
| `kb` | 可写 KB = `AGENT_KB_DIR`（本机 `~/.config/opencode/knowledge`） | 条目 id = frontmatter `id`（无 `repo:` 前缀）；**机器本地**，换机需重跑 verify |
| `repo` | 整个仓库工作树（含代码） | 只读语料**不索引代码** → 对一次性检索结构性不可达（边界层） |

## 3. 每条探针的字段

```json
{"id": "...", "face": "grep|read|outline|history", "scope": "corpus|kb|repo",
 "difficulty": "easy|medium|hard", "query": "自然语言任务",
 "action": {"tool": "...", "..." : "..."},
 "expected": {"file": "...", "anchor_line": 67, "span": "...", "line_range": [a,b],
              "heading": "...", "level": 2, "commit": "...", "unique": true},
 "in_corpus": true, "baseline_entry_id": "repo:<label>/<rel> | <kb-id> | null",
 "min_tool_calls": 1, "why": "这题为什么考这个面"}
```

- `action` = **参考动作**（一个称职 agent 该做的那次导航调用）；verify 模式就在执行它。
- `anchor_line` / `line_range` 是**当次冻结版本**（commit `6e3181c` 之后的工作树）的行号；
  行号会随文档增长漂移，故 grep 的判命中**不依赖 `anchor_line`**（见 §4），`anchor_line` 只作人类可读的锚。
- `baseline_entry_id` = 一次性检索要返回哪个条目才算命中；`null` 表示**结构性不可达**（历史面 / 代码面）。
- `min_tool_calls` = 参考动作最少需要的导航工具调用数（不含 `list`）——供 #63 对比"导航 vs 检索"的成本。

## 4. 判命中口径（由 `memory_agent/eval/eval_71_nav.py --verify` 执行）

**A. 导航动作层（做得到吗）——三段判定**（2026-10-07 收尾修订；Lead 复核后由"行号写死"改为三段）：

| 段 | 判据 | 说明 |
|---|---|---|
| ① 存在 | 目标文件存在（`corpus`/`kb`/`repo` 作用域内） | 文件被删/改名 ⇒ 失败 |
| ② **语义锚** | **锚短语仍在**：`grep`/`read` = `span` 出现在目标文件；`outline` = 标题逐字存在且 `level` 相符；`history` = git 查询返回期望 `commit` | **这才是被测语义**；② 失败 = **真失效**（锚被删改），必须改探针 |
| ③ 位置 | 锚的**实际行**与记录值（`span_line`）之差 ≤ **tolerance（默认 ±10）** | 文档位移在容差内 ⇒ 通过（并记 `window_shifted` 告警）；超差 ⇒ 提示 `--refresh` |

- **`--refresh`**：重推 `span_line` / `anchor_line` / read 的 `line_range`，并记录**内容指纹**
  （`span_sha1` = 锚所在行的 sha1 前 12 位）与 `refreshed_at`（当时 HEAD）、`tolerance`。
  它**只改位置元数据，不动 query / 期望语义**；锚短语找不到的探针进 `unrefreshable`
  （需人工换锚），**不会**被自动改写。
- **`unique=true` 降级为告警**（`unique_mismatch(scope=N)`）：仓库合法地多出一处同名 token 不该判负；
  每条仍记录 scope 内命中数供人工判断。
- `content_changed`（行内容变但锚短语还在）也是**告警**，不是失败——它提示"这条锚的事实可能过期"。
- `anchor_ambiguous(in_file=K,far=[...])`：锚短语在**目标文件内**出现多次，且**存在离记录行超 tolerance
  的另一处**（= refresh 的「就近取用」可能跳到别的段落）。只在**真会误导**时报；窗口内紧邻的重复
  （如 `memory_agent/trace.py` 的 `STOP_FALLBACK` 定义行 + 元组引用行）不报。
  → 处置：把锚**加长到该段落唯一**（2026-10-07 实测命中过 `r04`，见 §4.2）。
- 逐 face 的参考动作与作用于 §2 的 scope 定义一致：`grep` 字面匹配 + globs；`read` 读 `[lo,hi]`；
  `outline` 解析标题树（**跳过 fenced code block**）；`history` 用 `git log --diff-filter=A` /
  `git log -S`。`--refresh` 会同时把 read 的**参考动作 `action.line_range`** 与期望行窗对齐
  （两者同为位置元数据，不应自相矛盾）。

**B. paired 基线层（一次性检索够不够）**——`--baseline` 模式：

- 对**每条** `query` 调 `MemoryIndex.search(query, k=14)`（生产链路：BGE-M3 dense + BM25 sparse +
  store 原生 **DBSF** 融合，`MEMORY_RERANK` 关；这正是 master 默认口径）。
- 命中判据：`baseline_entry_id ∈ top-k`（**条目级**——与 #24/#47 同粒度，避免粒度混轴）。
- 指标：`recall@1/5/10/14`、`MRR`；`k=14` = 生产 `MEMORY_RETRIEVAL_POOL`。
- 配对差值（一次性的失败率 − 导航的失败率）用 **paired bootstrap**（复用 `harness/stats.py`：
  百分位 bootstrap、10000 次重采样、seed=0），**不显著也照实报**。
- `baseline_entry_id = null` 的探针（history 6 条 + code 边界 3 条）**单独作一层**：
  一次性检索结构性不可达（命中率按定义 0），**不混进 paired CI**。

**C. 边界（本集**不**测什么）**

- 不测答案正确性、不测 LLM 在环、不建 in-domain 集（#65）。
- 不测 rerank（保持生产默认关）；不测具名视图 / 退役过滤。
- 位置元数据绑定在**刷新时的提交**（`refresh.recorded_at_commit`）；文档一改：位移 ⇒ `--refresh` 可修，
  锚消失 ⇒ verify 报**真失效**（不是假通过，也不是必然失败）。

### 4.1 2026-10-07 收尾重同步（记录）

Lead 在合并后同步了地图（#69 CI + 计划段 + ADR 收窄）→ `--verify` 在 master 上 **26/28**。两处：

| 探针 | 现象 | 处置 |
|---|---|---|
| `nav-071-r01`（read，ADR-0030 D7.5–D7.7 行窗） | 锚 `D7.7 regret` 从行 60 位移到 **66**（整段刚性下移） | **`--refresh`**：行窗 `[55,61]` → **`[61,67]`**（按 `span_offset=5` / `line_width=6` 刚性平移）；`span_line=66`、`span_sha1=2050152724fc` |
| `nav-071-g07`（grep，原锚 `579 passed`） | 锚**消失**（单测数随提交变，#69 把它改成 `584 passed`）→ **真失效** | **就地换锚**（非位移）：改为冻结的实测数字 **`2.67s`**（#35 ONNX 重排器，AGENTS.md:88，全 scope 唯一）；query/why 同步改写，原因写进 `why` 字段 |

`--refresh` 一次更新 **22** 条（10 grep + 7 read + 5 outline）、不动 6 条 history（不可变）、
`unrefreshable = 0`；`--verify` 回到 **28/28**；再次 `--refresh` 的 diff 哈希不变（**幂等**）。
刷新以 `json.dump(indent=2)` 规范化写回 → 嵌套单行对象被展开（**一次性重排**，此后刷新是最小 diff）。
`nav-071-g07` 的 query 变了 ⇒ paired 基线**重跑一次**（见 `eval_71_results.md` §2.2）。

### 4.2 2026-10-07 task-6 收口（#63 合并后）

`#63` 合并 + 地图同步后，`--verify` 仍是 **28/28** 但 `nav-071-r04` 带 `window_shifted` 告警
（记录行窗 `[222,224]`，实际锚行 227→229）。**收口时发现一个真缺陷（不只是位移）**：

| 探针 | 现象 | 处置 |
|---|---|---|
| `nav-071-r04`（read，AGENTS.md「退役条目 = 0」段） | 锚短语 `退役条目 = 0` 在 AGENTS.md 出现**两次**（L229 裁决参照 / L343 前沿票）；refresh 的「就近取用」把它锚到了 **L229**——与 query 语义（"全索引 260 条中退役条目 = 0，据此不投资 C"）不符 | ① 锚**加长为** `全索引 260 条中退役条目 = 0`（query 原句、全库唯一解析到 L343）；② 行窗 → **`[343,345]`**；③ 新增 `anchor_ambiguous` 告警防复发（只在"另一处离记录行超 tolerance"时报） |

结果：`--verify` **28/28 且 `flags=[]`**（零告警）。这是**锚歧义**这一类问题的首个实例：
**位移可 refresh，歧义必须人工消歧**（refresh 没有语义，只有就近规则）。

## 5. 谁来执行 / 怎么复跑

```powershell
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$env:PYTHONPATH = "D:\python_work\work2026-4\Agent-Knowledge-Base"   # 合并后主树；并行期用你的 worktree

# A. 只读校验（无模型、秒级）：三段判定能不能过全部 28 条
& $py memory_agent/eval/eval_71_nav.py --verify

# A'. 文档位移后用刷新修位置元数据（不改 query；锚消失的会进 UNREFRESHABLE）
& $py memory_agent/eval/eval_71_nav.py --refresh          # 默认 ±10，可 --tolerance N

# B. paired 基线（需 BGE-M3；索引在临时副本上跑，绝不写生产）
& $py memory_agent/eval/eval_71_nav.py --baseline --out memory_agent/eval/eval_71_nav_baseline.json
```

结果与边界写进 `memory_agent/eval/eval_71_results.md`；实验记录（问题→假设→设置→数据→结论）
在 `experiments/nav-probes-71/README.md`。

## 6. 已知边界（如实标注）

1. **n 小**：28 条（paired 层 19 条）→ bootstrap CI 必然宽；本集只做**机制探针**，不做分布推断。
2. **机器本地**：3 条 `scope=kb` 依赖本机 `AGENT_KB_DIR` 内容；换机可能 stale。
3. **单一仓库**：只覆盖本仓（含其 git 史），不外推到其它语料/语言。
4. **verifier 是 Python 复刻**，不是 #63 的真工具实现——它证明"题可解、位置可核"，
   不证明 #63 的工具体验（那是 #63 自己的验收）。
5. **一次性检索的对照只有条目级粒度**：导航命中行窗，检索只能到条目——这个**粒度差本身就是被测结论**，
   不能被当成"参数没调好"。

## 7. 自产证据排除表（`SELF_EXCLUDE_PREFIXES`）的判据

**问题**：本批（Agent Teams）自己的评测/压测/验收证据会被只读语料索引（语料只排除代码，`.md` 一律收）。
于是"跑评测"和"出题/记答案的文件"落在同一张基表里 → **自我验证**（answer leakage）+ **语料漂移**
（基线随同批其它票的产出变化）。

**判据（两条，命中任一即排除）**：

| # | 判据 | 说明 |
|---|---|---|
| 1 | **答案泄漏** | 文件含探针**锚短语 / 探针 id / 命中结论**。实测（`--leak-scan`，见下）：`eval_71_results.md` **6 锚**、`nav_probes_71.spec.md` **4 锚**、`phase_c/report.md` **1**、`experiments/nav-probes-71/README.md` **1**、`nav_63_results.md` **全部 19 条探针 id + 逐题 ✅/❌ 命中表**（`o01` 的 2 字标题命中过 60+ 文件 = 噪声，已用 ≥5 字过滤） |
| 2 | **语料漂移** | 文件是**本批**的评测/压测/验收证据（`perf_70*`、`isolation_bypass_*`），不是冻结快照的基表内容，却会改语料规模与 BM25 统计 → 让"基线"随同批产出漂移 |

**当前排除表**（`eval_71_nav.py`）：

```
memory_agent/eval/standard_sets/        # #71 题面 / 规格 / 结果
memory_agent/eval/eval_71                # #71 结果 + 脚本 + 基线
experiments/nav-probes-71/              # #71 实验记录
experiments/agentic-rag-census/phase_c/ # #71 适配器与报告
memory_agent/eval/nav_63                # #63 证据（探针 id + 命中表）
memory_agent/eval/perf_70               # #70 压测证据（判据 2）
memory_agent/eval/isolation_bypass_     # #34/#39 证据（判据 2）
experiments/nav-tools-63/               # #63 实验记录
```

**刻意不排除**（防误伤）：

- `memory_agent/eval/README.md` —— 是 `g05` / `r03` 的**目标条目**；
- `experiments/agentic-rag-census/report.md`（含 `r05` 的锚）—— **#47 的不可变证据**，属基表历史内容；
- `docs/adr/0030-…md`（含 `r04` 的旧短锚）—— 合法 ADR；
- 任何 `docs/**` / `AGENTS.md` / `CONTEXT.md` —— 它们**就是**探针要检索的基表。

**验证方式**（复现泄漏扫描）：

```powershell
# 对每条探针的 query / 锚（≥5 字）扫全语料，列『非目标文件』的命中
# 判据 1 的直接证据；2026-10-07 结果见上表
& $py -c "<见 eval_71_results.md §2.3 的内联脚本>"
```

**边界**：排除表是**评测语料的视图**，不是产品收录规则——`memory_agent/corpus/loader.py` 与真实
索引/daemon **不受影响**（探针脚本自己生成 overlay `exclude`，只在评测进程内生效）。
