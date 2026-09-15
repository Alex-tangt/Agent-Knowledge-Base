# #26 安装 + MCP 集成冒烟结果

> 记录时间：2026-09-15。目标：证明 `ragcore` 已成真包、`memory_agent` **可安装**，且安装后的
> 包能经**标准 MCP 客户端**跑通 `memory_search` / `memory_get` / `memory_add`（不再依赖
> sys.path 垫片或 CWD）。决策见 `docs/adr/0024`、issue #26。

## 环境

- Python 3.12（仓库根 `venv/`），BGE-M3 已缓存。
- 安装方式：`pyproject.toml`（`ragcore` / `memory_agent` 各一份）。
  - 正式安装：`python -m pip install -e ragcore -e memory_agent`（`--no-deps`；
    依赖已由 `legal_web/requirements.txt` 装齐）→ 两个 editable wheel 均 build 成功。
  - 依赖解析：`python -m pip install --dry-run -e ./ragcore -e ./memory_agent`
    → `ragcore` 由本地目录提供，`memory-agent` 依赖 `mcp`/`ragcore`；除 `torch` 要求的
    `setuptools<82` 外全部 `Requirement already satisfied`。
  - 隔离构建核对：`python -m pip install --no-deps --target <tmp> ./ragcore ./memory_agent`
    → 两个 wheel 均 `finished with status 'done'`；落盘含 `ragcore/{services,strategies,agents,config,models,utils}`
    与 `memory_agent/{corpus,memory}` 全部子包 + 三个 console scripts。

## 干净解释器导入（不靠 CWD）

在仓库**之外**的 CWD、`PYTHONPATH` 只指向安装目录：

```
$ python -c "import memory_agent, ragcore, memory_agent.mcp_server; ..."
memory_agent: <tmp>\pkg26\memory_agent\__init__.py
ragcore:      <tmp>\pkg26\ragcore\__init__.py
mcp_server import OK
```

## MCP 集成冒烟

```
python memory_agent/eval/mcp_install_smoke_26.py
```

- MCP server 以 `python -m memory_agent.mcp_server` 启动，**工作目录在仓库之外**的临时目录
  （源码树既不在 CWD、也不在 sys.path 上）。
- 隔离：#16 sandbox 同款——真相源 = 真实 KB 的已提交态 `git clone`，索引 = 临时目录，
  只读语料 = 空。真实 KB 前后逐字不变。
- `memory_add` 传 `allow_duplicate=true`：#26 只验「安装包能执行写入工具」，去重闸门是 #16 的断言面。

结果：**9/9 通过**。

| # | 断言 | 结果 |
|---|------|------|
| 0 | `-m memory_agent.build_index` 子进程成功且只读根为空 | `readonly_roots=[]` |
| 1 | 安装包暴露记忆工具 | 7 个（`memory_search/get/add/supersede/archive/reindex/index_status`） |
| 2 | 索引从克隆 KB 建成且自洽 | `gen=gen-1 entries=22 points=22` |
| 3 | `memory_search` 返回可写条目 | 命中 `decisions/KB-0001-...` 等，`writable=true` |
| 4 | `memory_get` 读回真实 Markdown | `decisions/KB-0001-...` 4377 字 |
| 5 | `memory_add` 写入并单文件 commit | `status=written`，commit 变化 |
| 6 | 新条目立即可检索（写后增量刷新） | `topics/install-smoke-26-anchor` 置顶 |
| 7 | 真实 KB HEAD 未变 | `7b37efb8bc -> 7b37efb8bc` |
| 8 | 真实 KB 工作树前后逐字一致 | 一致 |

## 回归锚点（同批）

- `pytest tests/unit -q` → **227 passed**。
- `memory_agent/eval/config_independence_22.py`（#22 守卫复跑）→ `passed: true`，
  import 冒烟未触碰 `ragcore.config.llm`、0 次 `legal_web/.env` 读取、227 passed。
- **启动冒烟（legal_web boot 闸门，`memory_agent/eval/baseline_A.md` 口径）**：后台起
  `legal_web/app.py`，独立探测 → `ready:true`（~36s）、`/` 200、`/script.js` 200、
  `/api/kb/list` 2 个 KB、`/api/documents/count?kb_name=documents` → `{"count":3799}`；
  杀进程树后 8000 端口释放。

## 说明

- 冒烟脚本自身不 import `memory_agent`（只驱动子进程），所以能在任意 CWD、任意已安装环境复跑。
- `memory_agent/vector_db`（daemon 日志/索引）与 `readonly_repos.json` 仍 gitignored；安装不影响。
