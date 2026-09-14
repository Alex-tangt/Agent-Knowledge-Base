# AGENTS.md

FastAPI + vanilla-JS RAG 应用，已重构为三模块单仓：`ragcore/`（可复用核心）、`legal_web/`（适配层实例 / 回归锚点——FastAPI 应用 + 前端）、`memory_agent/`（agent 记忆能力包，MCP + skill）。目标形状为 **Agent-Knowledge-Base**（hero = 记忆能力包；政策法规问答 web 退为适配层实例与回归锚点，见 `docs/adr/0005`–`0007`）。起点是大学 NLP 课程作业（政策法规问答助手）。Backend serves the frontend as static files, so there is no separate frontend build or server.

## 目录布局

```
ragcore/            # 可复用核心（零 FastAPI 依赖）
  services/  strategies/  models/  config/  utils/  agents/
legal_web/          # 适配层实例 / 回归锚点
  app.py  api/  frontend/  data/raw/  tests/
  ingest.py  fetch_laws.py  test_langsmith.py  kb_registry.json
  requirements.txt  .env  vector_db/  uploads/
memory_agent/       # 记忆能力包（MCP + skill）
  mcp_server.py  runtime.py  _bootstrap.py  settings.py  build_index.py
  corpus/  memory/  eval/  requirements.txt  vector_db/  README.md
tests/unit/         # ragcore 核心 + memory_agent 单测（pytest）
experiments/  docs/
```

`ragcore` 与 `legal_web` / `memory_agent` 之间用 **sys.path 垫片**连接：入口（`legal_web/app.py`、`legal_web/ingest.py`、`memory_agent/*.py`、`tests/unit/conftest.py`、实验脚本）把 `ragcore/` 加入 `sys.path`，包名保持 `services/`、`config/`、`utils/`、`strategies/`、`agents/` 不变。`agents/`（router_graph + session_memory）属核心——`rag_service` 直接 import 它们。`memory_agent` 自身用 `memory_agent.` 前缀绝对导入（其模块名**不得**叫 `config`，会遮蔽 ragcore 的 `config` 包，见 ADR-0008）。

## Virtual environment (REQUIRED)
The project uses a venv at the repo root (`venv/`). Always activate it first:
```bash
# From repo root
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r legal_web/requirements.txt
```
`venv/` is gitignored. If it doesn't exist, create it: `python -m venv venv`.

## Run / develop
- Activate venv (see above), then: `venv\Scripts\python.exe legal_web/app.py` (uses uvicorn on `0.0.0.0:8000`). Open `http://localhost:8000`.
  - Startup takes ~1s to serve the frontend page.
  - Models (BGE-M3 embedding + bge-reranker-v2-m3) load in background (~30-40s); the frontend shows a loading screen with step-by-step progress.
- Build or rebuild the knowledge base: `venv\Scripts\python.exe legal_web/ingest.py` ingests `legal_web/data/raw/*` into Qdrant local mode (`legal_web/vector_db`). Documents can also be added at runtime via the upload endpoint.
- The frontend is served from `/` via `StaticFiles(directory="legal_web/frontend")`. No build step — edit `legal_web/frontend/*.html|css|js` directly.
- There is **no lint, typecheck, or CI config** in this repo. Don't invent those commands.

### memory_agent (记忆能力包)
- Build the derived memory index (loads BGE-M3; ~6 min per 60 entries on CPU): `venv\Scripts\python.exe memory_agent/build_index.py` → `memory_agent/vector_db/` (gitignored).
- Run as stdio MCP: `venv\Scripts\python.exe memory_agent/mcp_server.py`. Tools: `memory_search`, `memory_get`. Registered in `~/.config/opencode/opencode.json` as `memory-agent` (takes effect after opencode restart).
- **stdout is the MCP protocol channel** — all logging must go to stderr; `_bootstrap.configure_stderr_logging()` must run before importing `ragcore`.
- The memory index uses its **own** Qdrant path; it must **not** run concurrently with `legal_web` (local-mode lock + heavy models).

## Working directory
Paths are anchored in code, not to CWD: `ROOT_DIR` / `RAGCORE_DIR` / `LEGAL_WEB_DIR` in `ragcore/config/config.py`, `FRONTEND_DIR` in `legal_web/app.py`. `legal_web/app.py` boots correctly from **any** CWD.

## Environment (`.env`)
`legal_web/.env` is required and **gitignored**. `ragcore/config/config.py` loads it by explicit path (no CWD dependency) and raises `ValueError` at import if these are missing:
- `API_KEY`, `BASE_URL`, `Model` (capital `M` — not `MODEL`).
- LangSmith is optional: tracing activates when `LANGSMITH_API_KEY` is set and `LANGSMITH_TRACING=true` (default true). Without the key, trace calls pass through silently (`ragcore/services/langsmith_service.py`).

## Dependencies
- `legal_web/requirements.txt` — authoritative dependency set for this project.
  - Key deps: `fastapi`, `uvicorn`, `openai`, `qdrant-client`, `sentence-transformers`, `flagembedding`, `langchain`, `langchain-core`, `langchain-community`, `langchain-openai`, `langchain-text-splitters`, `pydantic`, `python-dotenv`, `pypdf`, `langsmith`, `python-multipart`.
- Root `requirements.txt` — pinned versions (UTF-8, was UTF-16 LE before a fix).
- `memory_agent/requirements.txt` — only its own dep (`mcp>=2.2,<3`); engine deps are reused from `legal_web/requirements.txt` since it calls `ragcore` in-process.

## Architecture / entrypoints
- `legal_web/app.py` — FastAPI app, CORS (`*`), mounts API router under `API_PREFIX="/api"` and static files at `/`. Lifespan event triggers background model warmup.
- `legal_web/api/routes.py` — endpoints (see below). Services are created via **lazy singleton getters** (`_get_rag_service()`, etc.), not module-level globals, to keep imports fast.
- `ragcore/utils/model_status.py` — shared `STATUS` dict tracking model loading state (`embedding`, `reranker`, `ready`). Polled by frontend loading screen.
- `ragcore/agents/` — Agent-related modules:
  - `router_graph.py` — LangGraph-based intent classifier that auto-routes queries to the correct knowledge base.
  - `session_memory.py` — In-memory conversation memory for query rewriting context.

### API endpoints (all under `/api`)
| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat/stream` | Chat streaming (RAG or LLM-only, JSONL SSE). Accepts `kb_name` and `session_id` params. Model services are instantiated lazily on first call. |
| `POST` | `/documents/upload?kb_name=` | Upload document (.pdf/.txt/.md), process (load/split/embed), store in specified Qdrant collection. |
| `GET` | `/documents/count?kb_name=` | Return chunk count in specified Qdrant collection. |
| `DELETE` | `/documents/clear?kb_name=` | Delete and recreate the specified Qdrant collection (full clear). |
| `GET` | `/kb/list` | List all registered knowledge bases. |
| `POST` | `/kb/create?name=&label=&description=` | Create a new knowledge base (new Qdrant collection). |
| `DELETE` | `/kb/{name}` | Delete a knowledge base (cannot delete default `documents`). |
| `GET` | `/health` | Liveness check (`{"status":"healthy"}`). |
| `GET` | `/status` | Model loading status (`{"embedding":"ready","reranker":"ready","ready":true}`). |

### Services (`ragcore/services/`)
| Service | Role |
|---------|------|
| `rag_service` | Core RAG pipeline: query rewriting → auto-routing → hybrid retrieval (vector + keyword + anchor) → reranker → LLM generation. Delegates to `chat_service` when `use_rag=False`. Supports multi-KB via `get_vector_store(kb_name)`. |
| `chat_service` | Pure LLM streaming (LLM-only comparison mode). |
| `vector_store_service` | Qdrant local mode wrapper (lazy-init, one per KB collection). Search, keyword match, anchor match, CRUD. |
| `document_service` | Load (TextLoader/PyPDFLoader), article-aware splitting, fallback recursive split. Heavy langchain imports are deferred inside methods. |
| `reranker_service` | Cross-encoder reranker (`BAAI/bge-reranker-v2-m3`). Lazy-init on first use. |
| `local_embedding_service` | Text embedding (`BAAI/bge-m3`, 1024-dim). Lazy-init on first use. |
| `langsmith_service` | Optional LangSmith tracing (no-op when key not configured). |
| `kb_registry` | Knowledge base registry — maps KB names to Qdrant collections. Persisted to `legal_web/kb_registry.json`. |

### Lazy-loading strategy
All heavy imports and model loads are deferred to avoid blocking HTTP startup:
- `langchain_text_splitters` → imported inside `DocumentService._recursive_split()` only when needed (non-law documents).
- `langchain_community.document_loaders` → imported inside `DocumentService.load_document()` only on upload/ingest.
- `sentence-transformers` (BGE-M3) → loaded inside `VectorStoreService.embeddings` property on first query.
- `bge-reranker-v2-m3` → loaded inside `RAGService.reranker` property on first RAG query.
- `QdrantClient` → initialized inside `VectorStoreService._init_client()` on first use.
- `ChatService`, `RAGService`, `DocumentService` → singleton getters in `routes.py`, instantiated on first API call.

The `warmup()` function (called from `app.py` lifespan) eagerly triggers BGE-M3 + reranker loading in a background thread so they are ready before the user sends their first message.

## RAG specifics
- Knowledge base: 21 Chinese legal/government documents in `legal_web/data/raw/`. `legal_web/data/SOURCES.md` lists sources.
- **Vector store**: Qdrant local mode (`legal_web/vector_db/`), one collection per knowledge base. Default: `documents`.
- **Embedding**: `BAAI/bge-m3` (1024-dim) via `sentence-transformers`, `normalize_embeddings=True`.
- **Reranker**: `BAAI/bge-reranker-v2-m3` cross-encoder, re-ranks candidate pool before feeding to LLM.
- **Chunking**: Article-aware splitting at "第X条" boundaries (law documents), with title prepended. `ARTICLE_MAX_CHARS=800`. Fallback recursive split for non-law docs.
- **Hybrid retrieval**: vector search (Qdrant cosine, pool=20) + article-number keyword match + anchor keyword match. Merged, deduplicated, then reranker re-scores → adaptive select (top `ADAPTIVE_MAX=8`).
- **Query rewriting**: LLM compresses verbose queries into search-friendly phrases before retrieval (single-mode, unconditional — the <20-char skip was removed, see CONTEXT.md).
- **No-evidence handling**: if best post-reranker distance > `RELEVANCE_THRESHOLD=0.85`, returns refusal message and skips LLM call. `RELEVANCE_THRESHOLD=None` to disable hard cutoff.
- **Prompt strategy**: LLM is instructed to answer based on partial context rather than refusing outright. Only refuses when context is completely unrelated.
- `POST /api/chat/stream` streams **JSON Lines** (`application/jsonl`): each line is `{"type":"content"|"metadata"|"error", ...}`. `content` chunks and `metadata` carry a `sources` array `[{content, source, score, chunk_id}]` for citation display. Scores are post-reranker distances (lower = more relevant).

## Multi-KB & Agent routing
- **KB Registry**: `ragcore/services/kb_registry.py` manages KB metadata. Stored as JSON at `legal_web/kb_registry.json`. Default KB: `documents` (政策法规知识库).
- **Auto-routing**: When `kb_name="auto"`, the LangGraph router agent (`ragcore/agents/router_graph.py`) classifies the query and selects the best KB. Otherwise, uses the explicitly specified KB.
- **Session memory**: `ragcore/agents/session_memory.py` stores recent conversation turns per `session_id`. Used for context-aware query rewriting (future enhancement).
- Create additional KBs via `POST /api/kb/create`, ingest docs into them via `POST /api/documents/upload?kb_name=`.

## Frontend
- Vanilla JS SPA with ES modules, served as static files. No build step.
- `legal_web/frontend/script.js` — main controller. `legal_web/frontend/js/` — modules: `apiService`, `messageHandler`, `ragUI`, `config`, `themeManager`, `emojiManager`, `documentManager`.
- **Loading screen**: On page load, shows a two-step progress indicator (Embedding / Reranker). Polls `GET /api/status` every 1s. When `ready=true`, hides overlay and enables chat input.
- **Source citations**: Collapsed card list under each AI message. Score badges color-coded: green (<0.35), yellow (0.35-0.60), red (>0.60).
- **RAG toggle**: Sidebar switch. Stored as `kbRagMode` in localStorage.
- **KB selector**: Dropdown in sidebar with `🤖 自动选择` option plus all registered KBs. Stored as `kbSelectedKB` in localStorage.
- **Versioning**: JS/CSS files use `?v=N` cache busting. Increment when changing any JS module.

## Tests
- **单元测试**：`tests/unit/`（pytest）——`test_document_service.py`、`test_rag_service.py`、`test_session_memory.py`、`test_memory_corpus.py`、`test_memory_index.py`。运行：`venv\Scripts\python.exe -m pytest tests/unit -q`（89 passed）。
- Smoke test: `venv\Scripts\python.exe legal_web/test_langsmith.py`.
- **启动冒烟（boot 闸门）**：后台起 `legal_web/app.py`，独立探测 `/api/status` → `ready:true`、`/` 与 `/script.js` → 200、`/api/kb/list`、`/api/documents/count?kb_name=documents`，再杀进程树确认端口与 Qdrant 锁释放。命令与结果见 `memory_agent/eval/baseline_A.md`（比"单测 + 导入冒烟"更强的收工锚点）。
- RAG vs LLM-only eval: from repo root run `venv\Scripts\python.exe legal_web/tests/run_eval.py` (backend on :8000, KB built). Parses `legal_web/tests/questions.md` and writes `legal_web/tests/results.md`. Fill `legal_web/tests/failure_analysis.md` for failure cases. `legal_web/tests/score_eval.py` does LLM-as-judge multi-dimension scoring.

## 开发工作流（AI 必走，请求先进来路由）

> 本项目执行者是 AI（用户提供思路、AI 开发）。纪律内嵌在流程节点里，不是孤立清单。详见 `docs/adr/0004-dev-discipline-ai-self-maintenance.md`。

任何工作请求先路由：
- **缺陷** → 根因流程：先测量/复现定位 → 修复 → 留回归证据。
- **新功能** → 功能流程：决策闸门 → 拆单元 → 逐单元实现+提交 → 收尾健康闸门。
- **实验** → 实验流程：`experiments/<name>/`（脚本+数据+结论同处，先写记录再跑）。
- **收工/请求结束** → 健康闸门（每个流程的最后一步，不是可跳过的独立清单）。

### 单元（commit 的粒度）
任何可独立验证、可一句话解释的增量即一个单元：修一个 bug、做一次优化、加一个工具都是单元。判定：一句话能说清 + 能独立验证 → 立即 commit；否则拆小或合并。不要等一个 feature 全做完才提交。

### 决策闸门
大改动前用一句话向用户说明"是什么/为什么/风险"。硬决策（难逆转 / 反直觉 / 真权衡）必须经用户确认，并写进 `docs/adr/`。例行工作自主执行，不需要逐个请示。

### 地图同步
结构一变就更新本文件与 `CONTEXT.md`。AGENTS.md 必须始终描述真实目录树，不允许文档脱离实际。

### 实验留痕
实验只进 `experiments/<name>/`。每个实验目录必须含 `README.md`（问题 → 假设 → 设置 → 数据 → 结论）。无结论的实验不算完成。

### 健康闸门（收工前必跑）
1. `git status` 干净——无未提交工作。
2. 本文件与实际目录树一致。
3. 无垃圾文件（日志、临时产物、迭代残留如 `post_refactor*`、`nul`）。
4. 每个实验目录有结论文件。
5. 改动有决策路径落点（`docs/adr/` 或 `experiments/` 记录）。
6. 每个功能过可解释性闸门：一句话讲不清 = 不该进基线。

## 当前路线图（2026-08 一周冲刺）

- ✅ 0. 机制落地 + 清理（垃圾已删；`eval_service.py` 迁入 `experiments/query-rewrite-optimizer/`；诊断产物 `llm_refusal_trace.txt`/`diagnostic_output.txt` 留待根因调查）。
- ✅ 1. 初始基线 commit（本地 + 远程，单根 `e723e5b`）。
- ✅ 2. 延迟调查（rerank-latency + e2e-latency 两轮实验）：生产 rerank mean ≈15s（CPU 固有，接受为已知限制）；"分钟级"真凶 = 优化器实验的 eval_service（对子查询重排 + pool 100，已记录发现，实验封存不修）；pool 截断延后为 planned optimization；模型加载已加 `local_files_only=True`（消除冷启动 HF 网络卡死）。
- ✅ 3. 拒答根因调查（≤1 天，时间盒）：结论=机制健康（基线误拒率 2.4%、无答案拒答率 91.7%），唯一误拒为 reranker 边界分(0.87>0.85)+anchor 污染，选 A 接受现状并记录（详见 `experiments/refusal-root-cause/`）。
- ✅ 4. 评估×2 → 因选 A 无二次评估，`legal_web/tests/results_scored.md` 即唯一最终证据（拒答 21→12，context_precision 3.4→4.8，source_recall 1.0）。
- ✅ 5. README 简历门面（评测证据 + 工程纪律 + 目录修正）+ 终版整理。
- MCP（延后）：stdio + `list_kbs`/`search`/`ask`，复用 service 层。

## 方向重定向（2026-09-14）

项目升级为 **Agent-Knowledge-Base**：hero = agent 记忆能力包（MCP + skill），`legal_web` 退为适配层实例 / 回归锚点。决策见 `docs/adr/0005`–`0007`，领域语言见 `CONTEXT.md`。

- ✅ 甲⁺ 布局重排（#9 第一轮）：`backend/` 拆为 `ragcore/` + `legal_web/`，新建 `memory_agent/`；T1 锚点复现（71 passed + 导入冒烟 + 启动冒烟，见 `memory_agent/eval/baseline_A.md`）。
- ✅ 更名（#9 第二轮，2026-09-14 完成）：GitHub 仓库已改为 `Alex-tangt/Agent-Knowledge-Base`，`origin` 是干净 URL（原嵌的明文 token 已移除）；本地目录已改名（会话内被 MCP 子进程 CWD 锁住，由用户在会话外完成）。在新路径复跑锚点验收：`pytest tests/unit -q` → 71 passed、legal_web 导入冒烟 → import-ok。venv 采用"移动后原样验证"策略，一律用 `venv\Scripts\python.exe -m ...`（`Scripts\*.exe` 内嵌旧绝对路径已失效，不使用）。详见 `docs/adr/0007`。
- ✅ 读路径最小闭环（#10，2026-09-14）：`memory_agent` 条目级派生索引（复用 `ragcore` BGE-M3 + Qdrant，独立路径）+ stdio MCP `memory_search`/`memory_get`；60 条（20 可写 KB / 40 只读本仓库）。决策见 `docs/adr/0008`。
- 下一步：写入路径（#11 `memory_add` + 去重 + 校验 + git commit；#12 supersede/archive）。

## 后续优化待办（Backlog / 简历谈资池）

非本周范围，延后。每条都是可讲的优化故事：

1. **查询改写质量**：改写为关键词组合可能导致语义检索效果下降（尤其多约束复合句丢约束）。问题节点：查询改写（`rag_service._rewrite_query`）、问题分解（eval_service 多路）。关联：`experiments/query-rewrite-optimizer/` 结论（二元意图判断与关键词改写结构性冲突）。
2. **延迟优化：pool 边界**：`ADAPTIVE_POOL=20` 与候选池实际 ~32（vector+keyword+anchor 合并）的边界是否合理；pre-rerank 候选截断 knob（实测 rerank 线性于池大小，~230ms/对）。关联：`experiments/rerank-latency/`、`experiments/e2e-latency/`。
3. **混合检索融合机制**：vector + keyword + anchor 三类信号的融合/加权是否最优（anchor 命中过多可能淹没向量信号）。问题节点：`ragcore/strategies/legal.py` 的 `_add` 合并逻辑。

## Gotchas
- **Always activate venv first** (`venv\Scripts\activate` on Windows). Running without it may miss installed dependencies.
- **Qdrant local mode locks** the storage directory exclusively. Do not run two Python processes that create `VectorStoreService` concurrently against the same `vector_db/`. If you get "already accessed" errors, kill the other process and delete `legal_web/vector_db/.lock`. The memory index has its own path (`memory_agent/vector_db/`) — but two `memory_agent` processes still conflict with each other, and neither may run alongside `legal_web`.
- **stdio MCP: stdout is the protocol channel.** `ragcore/utils/logger.py` configures logging to `sys.stdout`; `memory_agent` must grab the root logger to stderr *before* importing `ragcore` (`_bootstrap.configure_stderr_logging`). Any stray stdout write corrupts the JSON-RPC stream.
- **Don't name a `memory_agent` module `config.py`** — under `python memory_agent/x.py` it shadows ragcore's top-level `config` package (`ModuleNotFoundError: No module named 'config.config'`). It's `settings.py`; use `memory_agent.`-prefixed absolute imports.
- **First run** after `pip install` downloads BGE-M3 (~2.2GB) and bge-reranker-v2-m3 (~2.2GB) from HuggingFace. Subsequent runs load from cache instantly.
- **`RELEVANCE_THRESHOLD=0.85`** is a generous post-reranker value; the prompt handles most boundary cases. Use `experiments/relevance-calibration/calibrate_relevance.py` to recalibrate if needed.
- **Browser cache** — after frontend changes, increment the `?v=N` query string on JS/CSS links in `index.html` AND in all `import` statements across all JS files. Otherwise browsers serve stale cached versions.
- **Article-aware splitting** requires ≥3 "第X条" markers to activate; documents with fewer markers fall back to recursive splitting.
- `legal_web/fetch_laws.py` — law document scraper. One-off experiments live in `experiments/` (see its README).
- Git history uses loose Conventional-Commit-style prefixes in Chinese (e.g. `feat:`, `chore:`). Match that when committing.

## Agent skills

### Issue tracker

Issues live as GitHub issues in this repo, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles, default strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
