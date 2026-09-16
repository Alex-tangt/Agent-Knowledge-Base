# 写路径确定性 sandbox 套件——结果与证据（#16）

> 日期：2026-09-14 · 分支：`feat/16-write-path-sandbox`（基于 `master` `c7dfcc2`）
> 套件：`memory_agent/eval/write_path_sandbox.py` · 结论：**25/25 通过，可重复，真实 KB 未被触碰**
>
> 2026-09-16 复跑（#36 `feat/36-personal-mode`）：**25/25 通过**。1d 断言随 D13 改为
> 「写后不嵌入、`index.mode=lazy`」（原「写后自动增量刷新」）；索引构建子进程由脚本路径
> 改为 `-m memory_agent.build_index`，保证 worktree 场景用同一棵源码树。

## 运行

```powershell
# 仓库根，用主树 venv 的绝对路径（工作树里没有 venv）
venv\Scripts\python.exe memory_agent/eval/write_path_sandbox.py
# 可选：--json-out <path> 落机器可读通过矩阵；--source-kb <path> 换真实 KB
```

## 隔离设计（为什么不会污染真实 KB）

1. **真相源**：真实 KB 的**已提交态** `git clone` 到临时目录，经 `AGENT_KB_DIR` 注入——
   天然避开并发会话的在制品；`tools/kb.py` / `tags.md` / 真实条目都保留。
2. **派生索引**：`MEMORY_INDEX_DIR` 指向临时目录，代目录 + `CURRENT` 指针随之隔离。
3. **只读语料**：`MEMORY_READONLY_ROOTS=""`——不把整个代码仓库索引进来（本票新增覆盖）。
4. **索引构建**：在**子进程**里跑文档化的 `build_index.py`（`MEMORY_REINDEX_BATCH=256`）。
   原因见下「已知环境限制」。
5. 套件全程只对真实 KB 做**只读** `git status` / `rev-parse`；所有写入都落在克隆里。

## 通过矩阵（两次运行一致）

| # | 断言（外部行为） | 结果 |
|---|---|---|
| 0a | `build_index.py` 子进程成功且只读根为空 | PASS `readonly_roots=[]` |
| 0b | 索引从 KB 已提交态建成且自洽 | PASS `built=True consistent=True` |
| 0c | 索引只含可写 KB（未混入代码仓库只读语料） | PASS `entries=21 = 克隆条目数` |
| 1a | `memory_add` 返回 written | PASS |
| 1b | add 落盘且 frontmatter 合规（id/type/status） | PASS |
| 1c | add 的 commit 只含本条目文件 | PASS `files=['topics/sandbox-write-anchor.md']` |
| 1d | 写后不再同步刷（#36/D13：`index.ok` + `mode=lazy`，不嵌入） | PASS `index={'ok': True, 'refreshed': False, 'mode': 'lazy', ...}` |
| 1e | 新条目在下一次 `memory_search` 即被检索到（查询时惰性追平） | PASS |
| 2a | 未知 tag 只警告但仍写入 | PASS |
| 2b | 真实 `kb.py check` 通过（0 errors） | PASS |
| 3a | 近似重复返回 duplicate 且未落盘/未提交 | PASS `reason=semantic` |
| 3b | 去重候选指向已存在条目 | PASS |
| 3c | `allow_duplicate=true` 绕过去重并写入 | PASS |
| 4a | supersede 默认只返回 `confirmation_required` 预览、不落盘 | PASS |
| 4b | supersede confirm 后新条目带 `supersedes`、状态 current | PASS |
| 4c | 旧条目置 superseded + superseded_by，正文保留、文件未删 | PASS |
| 4d | 新旧两文件在同一 commit 且仅这两文件 | PASS |
| 5a | archive 缺 `reason` 被拒 | PASS |
| 5b | archive 默认只返回预览、不落盘 | PASS |
| 5c | archive confirm 后置 archived + archive_reason 且文件仍在 | PASS |
| 5d | archive 的 commit 只含该文件 | PASS |
| 6a | `kb.py check` 报错时写入被拒、无半成品、HEAD 不动 | PASS |
| 7a | `git reset --hard` 可干净撤回（HEAD 归位、工作树干净） | PASS |
| 8a | 真实 KB HEAD 未变 | PASS（两次运行内均不变） |
| 8b | 真实 KB 工作树前后逐字一致（未被沙箱触碰） | PASS |

两次独立运行（`--json-out`）均 **25/25、exit 0**，通过矩阵逐条一致；期间真实 KB 的
`HEAD` 与 `git status --short` 在每次套件前后**逐字不变**。

## 关键 git 证据（外部可观察）

- **单文件提交范围**：add → `topics/sandbox-write-anchor.md`；archive → 单文件；
  supersede → `topics/sandbox-write-anchor.md` + `topics/sandbox-anchor-v2.md`（同一 commit）。
- **破坏性默认不写**：supersede / archive 在 `confirm` 缺省时只回 `confirmation_required`，
  新文件不存在、旧文件与 `HEAD` 均不动。
- **回滚两条路径**：(a) 注入 `tools/kb.py` 报本文件 ERROR → 写入被拒、无半成品、`HEAD` 不动；
  (b) 提交后用 `git reset --hard` 回到基线 → 工作树干净、沙箱文件全消失。
- **不删文件**：supersede / archive 后旧文件仍在，正文原样保留。

## 去重阈值校准（D3）

见 `experiments/dedup-threshold-calibration/`：21 条真实条目，正例（重加）min 0.949、
负例（不同条目最近邻）max 0.792。据此将 `DEDUP_THRESHOLD` **0.92 → 0.88**（写回
`memory_agent/settings.py` 与 `docs/adr/0009` 的「校准」小节）。套件的去重断言用写后
精确重复（分数≈0.95+），不依赖该阈值的具体取值。

## 已知环境限制（非套件缺陷）

本机 commit 已超物理内存（内存被常驻 MCP 的 BGE-M3 占着）。若在**套件进程内**直接
`memory_reindex`，`Reindexer` 会逐块构造 `VectorStoreService`、每实例各加载一份 BGE-M3，
叠加后曾观察到原生崩溃 `0xc0000005`（access violation，与 ADR-0011 记录同源）。
因此套件把索引构建放到**子进程**（`build_index.py`，退出即回收），检查进程只背一份模型；
`MEMORY_REINDEX_BATCH=256` 让它一次嵌完、少构造一份实例。这是让套件在内存紧张时仍可重复的
关键，不改变被断言的外部行为。

## 与既有测试的关系

- `tests/unit/`：离线、确定性、不加载模型（现 137 passed，含本票新增的 READONLY_ROOTS
  覆盖单测）。#16 是**真实 KB 版**运行时证据，与之分开。
- `tests/unit/test_memory_writer.py`：用 FakeIndex 的写路径单测；本套件是其真实 KB 对应物。
