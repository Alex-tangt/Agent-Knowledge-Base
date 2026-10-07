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

**A. 导航动作层（做得到吗）**——verify 模式用 Python 复刻参考动作执行：

| face | 参考动作 | 命中条件 |
|---|---|---|
| `grep` | 在 scope 内逐行**字面**匹配 `pattern` | 期望 `file` 出现该 `span`；`unique=true` 时要求全 scope 唯一（否则报 `probe_stale`） |
| `read` | 读 `file` 的 `[lo,hi]` 行 | 该行窗文本含期望 `span`（**行号错位即不算命中**——行窗本身就是被测能力） |
| `outline` | 解析该文件的标题树（**跳过 fenced code block**） | 存在期望 `heading`（含 `level` 与逐字文本；`anchor_line` 仅警告不判负） |
| `history` | `git log --diff-filter=A -- <path>` 或 `git log -S <pattern> -- <path>` | 返回的提交集合含期望 `commit` |

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
- `read` / `outline` 的期望行号绑定在**当次工作树**；文档一改，verify 会报 stale——**这是设计**（探针要可失活）。

## 5. 谁来执行 / 怎么复跑

```powershell
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$env:PYTHONPATH = "D:\python_work\work2026-4\wk-71-eval"

# A. 只读校验（无模型、秒级）：参考动作能不能命中全部 28 条
& $py memory_agent/eval/eval_71_nav.py --verify

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
