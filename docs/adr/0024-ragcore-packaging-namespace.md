# 0024 ragcore 包化：真命名空间 + memory_agent 可安装

Status: proposed

## 背景

`#26` 要让 `memory_agent` 成为**可安装**的能力包。卡点不在 `memory_agent` 自身，而在 `ragcore`：

- `ragcore` **不是包**——它只是源码目录；`memory_agent/_bootstrap.py::ensure_ragcore_on_path()` 把 `ragcore/` 塞进 `sys.path`，于是 `services/`、`config/`、`utils/`、`strategies/`、`agents/`、`models/` 以**通用顶层名**被导入。
- 通用顶层名**不能安全进 site-packages**（污染 + 撞名：任何 `config`/`utils`/`services` 都会撞）。
- 依赖面已确认：`memory_agent` **不 import fastapi / legal_web**；ragcore 需要 `langchain-core / qdrant-client / sentence-transformers / openai`。

## 决策

- **D1 `ragcore` 成为真包**：补 `__init__.py`，导入统一为 `ragcore.<subpackage>.*`（如 `ragcore.services.reranker_service`）——**保留 `ragcore` 这个名称**，不再有裸 `services/`。
- **D2 退役 sys.path 垫片**：删 `_bootstrap.ensure_ragcore_on_path()` 的路径注入（**保留** `configure_stderr_logging`——stdio MCP 铁律）。
- **D3 monorepo 内可安装**：`ragcore` 与 `memory_agent` 各自 `pyproject.toml`，支持 `pip install -e`；**不拆独立仓库**（沿 `#26` 方向锁定）。
- **D4 两平面同源**：`legal_web` 与 `memory_agent` 都从 `ragcore.` 导入，不再各自假设路径。

## 理由

- 通用顶层名入 site-packages = 污染/撞名；**包化是唯一干净的解法**。
- 不选 vendor：策略 / reranker 正是**仍在迭代的热点代码**，拷贝两份必然漂移。
- 不选"垫片照旧"：那让"可安装"名不副实。

Considered options:

- **A 包化（采用）**
- **B vendor 进 memory_agent（弃）**——自包含但重复 + 漂移
- **C ragcore 独立成一个包（≈A + 多一份打包维护）**
- **D 保留垫片（弃）**——最省事但最不诚实

## Consequences

- **触及 `legal_web`（回归锚点）**：`app.py`、`api/routes.py`、`ingest.py`、测试、`experiments/` 脚本的 import 都要改。**必须重跑锚点验收**：启动冒烟（`/api/status` → ready、`/`+`/script.js` 200、`kb/list`、`documents/count`）+ `pytest tests/unit`（当前 227 passed）+ import 冒烟。
- MCP 启动可改为 `python -m memory_agent.mcp_server`（仍是绝对路径注册亦可）。
- `venv` 内需 editable 安装（`pip install -e ragcore -e memory_agent`）；根 `requirements.txt` 与 `legal_web/requirements.txt` 的关系需理顺。
- 与 ADR-0007（布局）、ADR-0016（config 分层）一致；不改变 config 的 core/llm 语义。

Relates: #26、ADR-0007、ADR-0016、ADR-0013（daemon/proxy 拓扑）。
