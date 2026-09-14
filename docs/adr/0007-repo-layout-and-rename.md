# 0007 目录布局甲⁺与仓库更名 Agent-Knowledge-Base

Status: accepted

仓库拆为 `ragcore/`（可复用核心）、`legal_web/`（适配层实例 / 回归锚点）、`memory_agent/`（记忆能力包），并把仓库与本地目录更名为 **Agent-Knowledge-Base**。重构时间盒 3 小时，不绿即回落**甲-min**（仅新增 `memory_agent/`，不动现有目录）。

理由：记忆能力包要能独立打包 / 发布（被外部 host 集成的工具），不应耦合 Web 应用的目录树；甲⁺ 让核心可复用、适配层分离，坐实"service 核心 + 适配层"的产品叙事，并为企业版分层留地基。更名并进本次重构，统一覆盖"记忆 + 未来知识库管理"的完整叙事。

Considered options:
- A 甲⁺ + 更名（采用）——分层清晰、可独立发布；代价是 3h 重构风险。
- B 甲-min（回落）——风险最低，但耦合与叙事弱。
- C 中间态（弃）——只拆 ragcore、legal_web 暂留，收益不完整。

Consequences: 一次性改名动作（GitHub 仓库、本地目录、venv 重建、remote 重设、文档 / KB sources 同步）；需守住"每日可复现 benchmark A"的锚点纪律。

## 执行记录（2026-09-14，第一轮：布局）

- **导入策略**：sys.path 垫片（保留 `services/`、`config/`、`utils/`、`strategies/`、`agents/` 原包名），入口把 `ragcore/` 加入 `sys.path`。
- **实际映射**：`backend/{services,strategies,models,config,utils}` → `ragcore/`；`backend/{app.py,api,ingest.py,fetch_laws.py,test_langsmith.py,kb_registry.json,requirements.txt,.env(.example),vector_db,uploads}` + `frontend/` + `data/` → `legal_web/`；`tests/` 的评测脚本 → `legal_web/tests/`；`tests/unit/` 与 `experiments/`、`docs/` 留顶层。
- **偏离与理由**：`backend/agents/` 改归 **`ragcore/agents/`**（原计划放 `legal_web`）。原因：`ragcore/services/rag_service.py` 直接 import `agents.router_graph` / `agents.session_memory`——若把 `agents` 放适配层，核心将反向依赖适配层，且每个 ragcore 入口都得把 `legal_web/` 也塞进 `sys.path`。二者本就属核心。
- **运行时路径**：`ragcore/config/config.py` 提供 `ROOT_DIR` / `RAGCORE_DIR` / `LEGAL_WEB_DIR`；`VECTOR_DB_PATH` / `UPLOAD_DIR` 默认落 `legal_web/`（可被同名环境变量覆盖）；`.env` 由 `load_dotenv(LEGAL_WEB_DIR/.env)` 显式锚定。
- **锚点复现**：`pytest tests/unit -q` → 71 passed；legal_web 导入冒烟 → import-ok；`compileall` exit 0。证据见 `memory_agent/eval/baseline_A.md`。
- **未完成**：仓库与本地目录更名 `Agent-Knowledge-Base`（GitHub rename + 本地目录 + venv 重建 + remote 重设 + 文档/KB sources 同步）留待第二轮。

