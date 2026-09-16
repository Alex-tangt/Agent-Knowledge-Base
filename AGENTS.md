# AGENTS.md

FastAPI + vanilla-JS RAG 应用，已重构为三模块单仓：`ragcore/`（可复用核心）、`legal_web/`（**【已排除】上一版本遗留的无关产品**——仅保留代码；不优化 / 不依赖 / 不作对象）、`memory_agent/`（agent 记忆能力包，MCP + skill）。目标形状为 **Agent-Knowledge-Base**（hero = 记忆能力包；政策法规问答 web **已排除**，见 `docs/adr/0005`–`0007` 与 2026-09-16 owner 决定）。起点是大学 NLP 课程作业（政策法规问答助手）。Backend serves the frontend as static files, so there is no separate frontend build or server.

## 目录布局

```
ragcore/            # 可复用核心（零 FastAPI 依赖）；真包
  __init__.py  pyproject.toml
  services/  strategies/  models/  config/  utils/  agents/   # 各含 __init__.py
legal_web/          # 【已排除】上一版本遗留的无关产品（仅保留代码）
  app.py  api/  frontend/  data/raw/  tests/
  ingest.py  fetch_laws.py  test_langsmith.py  view_registry.json
  requirements.txt  .env  vector_db/  uploads/
memory_agent/       # 记忆能力包（MCP + skill）；可安装
  __init__.py  pyproject.toml
  mcp_server.py  proxy.py  runtime.py  _bootstrap.py  settings.py  build_index.py
  gateway/  corpus/  memory/  skill/  eval/  requirements.txt  vector_db/  README.md
  # gateway/ = 检索网关（#32）：/mcp 边界 authn + 工具层 authz（强制过滤注入）
tests/unit/         # ragcore 核心 + memory_agent 单测（pytest）
experiments/  docs/
```

**`ragcore` 是真包（ADR-0024）**：`services/`、`config/`、`utils/`、`strategies/`、`agents/`、`models/` 一律以 `ragcore.<subpackage>.*` 导入（如 `from ragcore.services.reranker_service import RerankerService`）；**不再有 sys.path 垫片，也不再有裸 `services/`/`config/` 顶层名**。`ragcore` 与 `memory_agent` 各自有 `pyproject.toml`，用 `pip install -e ragcore -e memory_agent` 安装到 venv（见下）。`agents/`（router_graph + session_memory）属核心——`rag_service` 直接 import 它们。`memory_agent` 用 `memory_agent.` 前缀绝对导入。

## Virtual environment (REQUIRED)
The project uses a venv at the repo root (`venv/`). Always activate it first:
```bash
# From repo root
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r legal_web/requirements.txt
pip install -e ragcore -e memory_agent   # install the two packages (ADR-0024)
```
`venv/` is gitignored. If it doesn't exist, create it: `python -m venv venv`.
`ragcore` / `memory_agent` are local editable packages — after editing their source no reinstall is needed; after renaming/moving modules, re-run the editable install.

## Run / develop
- Activate venv (see above; `pip install -e ragcore -e memory_agent` done), then: `venv\Scripts\python.exe legal_web/app.py` (uses uvicorn on `0.0.0.0:8000`). Open `http://localhost:8000`.
  - Startup takes ~1s to serve the frontend page.
  - Models (BGE-M3 embedding + bge-reranker-v2-m3) load in background (~30-40s); the frontend shows a loading screen with step-by-step progress.
- Build or rebuild the knowledge base: `venv\Scripts\python.exe legal_web/ingest.py` ingests `legal_web/data/raw/*` into Qdrant local mode (`legal_web/vector_db`). Documents can also be added at runtime via the upload endpoint.
- The frontend is served from `/` via `StaticFiles(directory="legal_web/frontend")`. No build step — edit `legal_web/frontend/*.html|css|js` directly.
- There is **no lint, typecheck, or CI config** in this repo. Don't invent those commands.

### memory_agent (记忆能力包)
- **可安装（#26 / ADR-0024）**：`pip install -e ragcore -e memory_agent`（Venv 段已含）。等价的模块/脚本入口：`python -m memory_agent.mcp_server`、`python -m memory_agent.build_index`，以及 console scripts `memory-agent` / `memory-agent-proxy` / `memory-agent-build-index`。脚本入口（`memory_agent/*.py`）安装后从任意 CWD 均可运行。
- Build the derived memory index (loads BGE-M3; ~6 min per 60 entries on CPU): `venv\Scripts\python.exe memory_agent/build_index.py` → builds a new generation `memory_agent/vector_db/gen-N/` and atomically switches the `CURRENT` pointer (issue #13; gitignored).
- **拓扑（#19 / ADR-0013）：一个常驻 daemon + 每会话一个 stdio 代理。** 所有会话共享 daemon 里那一份 BGE-M3（~3.9GB 只付一次），代理每会话仅几十 MB。
  - 启动 daemon：`venv\Scripts\python.exe memory_agent/mcp_server.py --transport http`（默认 `127.0.0.1:8765`，默认 eager 预热；`--no-warmup` 可关）。`GET /health` 是就绪探测。
  - 代理（opencode 的 `local` 命令，已注册为 `memory-agent`）：`venv\Scripts\python.exe memory_agent/proxy.py`——幂等确保 daemon 在跑（带启动权文件锁，不会 N 会话重复拉起），再把本会话 stdio 转发到 `/mcp`。运维：`proxy.py --status` / `--ensure` / `--stop`（手动停 daemon 回收 ~3.9GB；不做自动空闲卸载，见 ADR-0013 D2）。日志 `memory_agent/vector_db/daemon.log`（gitignored）。
  - 单会话/手动仍可 `mcp_server.py`（默认 `--transport stdio`）。
- Tools: `memory_search`, `memory_get`, `memory_add`, `memory_supersede`, `memory_archive` (后两个是破坏性变更，默认只返回 preview，需 `confirm=true` 才落盘), plus `memory_reindex` (分块全量重建：`cursor=None` 开始，拿 cursor 续调到 `done=true`) and `memory_index_status`. **收录（DDL，非写记忆）** = `memory_ingest_list` / `memory_ingest_include` / `memory_ingest_exclude`（#36）。写入**不再**同步刷索引（D13）：`index` 字段返回 `mode=lazy`，索引由下一次 `memory_search` 的指纹检查追平。
- **检索网关（#32 / ADR-0018 D2）**：`memory_agent/gateway/`——`/mcp` 边界 authn（单租户 = 进程配置；多租户形状 = `MEMORY_AUTH_TOKENS` 的 Bearer token 映射，`require_token` 默认开）→ 请求期身份上下文（ContextVar）→ 工具层 authz **无条件注入**白名单过滤（`tenant` + `classification`/`residency`，**只可收窄**，越权 `AuthorizationError`）。`memory_get` 做可见性预检；写工具要 `owner/admin/writer` 角色。**proxy 只传输、不是信任源**。审计 JSONL 记 `tools/call` + 身份（凭证绝不落盘）。证据 `memory_agent/eval/gateway_authz_32.py` + `..._results.md`（13/13）；隔离绕过对抗套件是 **#34**。
- **Skill**: source `memory_agent/skill/SKILL.md` (ships with the package) → install to `~/.config/opencode/skills/memory-agent/`; it tells the agent when to search/get/add and that supersede/archive need explicit user consent. See ADR-0012.
- **并发**：单 daemon 服务 N 会话，进程内串行化——Qdrant local mode 同进程也不能并发开 client，`VectorStoreService._session` 有 `RLock`；`memory/locks.py` 的 `INDEX_LOCK`/`WRITE_LOCK` 管索引与写入临界区（单写者）。见 ADR-0013 D3。
- **stdout 是 MCP 协议通道**（stdio 模式与代理都是）：所有日志必须走 stderr。`_bootstrap.configure_stderr_logging()` 必须在 import `ragcore` 之前跑；`proxy.py` 不 import ragcore，诊断一律 stderr。
- The memory index uses its **own** Qdrant path (`memory_agent/vector_db/<gen>/qdrant`, resolved via the `CURRENT` pointer), and the client is opened **per operation** (no long-held lock) — it can coexist with `legal_web`; see ADR-0008 D5. Full rebuilds build a new generation and atomically swap the pointer; an interrupted rebuild never leaves "empty index + stale manifest" (ADR-0011).
- **Write-path sandbox suite (#16)**: `venv\Scripts\python.exe memory_agent/eval/write_path_sandbox.py` — clones the real KB (committed state) into a temp dir, builds the index with `MEMORY_INDEX_DIR` in temp and `MEMORY_READONLY_ROOTS=""`, then asserts only external behavior (MCP tool responses + file/git state): dedup/report-only, `allow_duplicate`, supersede, archive, frontmatter compliance, rollback. 25 checks; evidence + pass matrix in `memory_agent/eval/write_path_sandbox_results.md`. The real KB is read-only to it (`git status`/`rev-parse`) and stays byte-identical. 1d 断言已随 #36/D13 改为「写后不嵌入、`mode=lazy`」。
- **个人模式验收 (#36)**: `venv\Scripts\python.exe memory_agent/eval/personal_mode_36.py` — 临时 KB/registry/overlay/索引 + **Stub 嵌入**（不加载 BGE-M3），单一 `MemoryIndex` 实例贯穿全程模拟**常驻 daemon 不重启**：外部增/改/删文件、overlay 加/减、D13 惰性追平、exclude 预览+确认且不误删他人条目。**15/15**；真实语料指纹（172 文件）**51ms**。证据 `memory_agent/eval/personal_mode_36_results.md` + `..._results.json`。
- **确定性检索评测基座（#24 / 叙事 #21）**：对象 = **记忆检索**（不是法律 RAG）；目标链路 = 向量 + 关键词 + rerank，走 `MemoryIndex.search` → `MemoryRetriever` → `ragcore` 策略 + `RerankerService`，不调 LLM。评测集 `memory_agent/eval/retrieval_eval_set.json`（51 条 = 45 有答案 + 6 无答案，条目级二值）；harness `memory_agent/eval/retrieval_eval.py`（`--mode vector|hybrid|hybrid-rerank`，`--trace` 断点续跑，`meta.run_hash` 验确定性）。基线见 `memory_agent/eval/retrieval_baseline.md`。无答案 query 只作描述性观察、不校阈值（ADR-0017）。
- **只读语料 = 来源注册表默认 ∪ 显式 overlay（只索引 `.md`，不索引代码；#7/#17 → #36 / ADR-0025 D8）。** 注册表在 gitignored 的 `memory_agent/readonly_repos.json`（`[{label, path, owner?}]`，相对路径按仓库根解析；模板 `readonly_repos.example.json`）；overlay 在 gitignored 的 `memory_agent/overlay.json`（`{"include": [路径模式], "exclude": [路径模式]}`；模板 `overlay.example.json`）。**两者都在运行时重读**（`corpus/loader.py::resolve_selection`），改配置 / overlay **无需重启 daemon**。不同来源同名文件用 `<label>/` 前缀消歧义（`source`，只读条目 id = `repo:<label>/<rel>`）。收录是 **DDL**（决定基表 extent）：接口 = `memory_ingest_list/_include/_exclude`；受限身份只能收自己拥有的域（`MEMORY_AUTH_OWNERS` / token `owners`）。移除（exclude）走**预览 + 确认**，否则孤儿点会被删。**孤儿安全**：注册表 / overlay 读不出或来源根不可达 → `complete=false`，`refresh` 不删条目（只报 `deferred_removed`），不误删他人条目。噪声排除见 `corpus/loader.py::EXCLUDE_DIR_NAMES`（`outputs`/`dataset`/`.scratch`/`.playwright-cli` 等）。
- **env knobs** (`memory_agent/settings.py`, read at import): `MEMORY_ENV_FILE` (override the `.env` path; default `memory_agent/.env`, loaded with process-env priority), `AGENT_KB_DIR` (truth source), `MEMORY_KB_OWNER` (可写 KB 的域 owner，缺省不声明), `MEMORY_INDEX_DIR` (index root), `MEMORY_READONLY_ROOTS` (`os.pathsep`-separated; **empty = no read-only corpus**, used by the sandbox), `MEMORY_READONLY_REPOS_CONFIG` (override the repo-list JSON path), `MEMORY_OVERLAY_CONFIG` (override the overlay JSON path；缺省 `memory_agent/overlay.json`), `MEMORY_REINDEX_BATCH`, `MEMORY_DEDUP_THRESHOLD` (default **0.88**, calibrated in #16 → ADR-0009), `MEMORY_WARMUP`, `MEMORY_RETRIEVAL_POOL` (混合召回候选池，默认 **14**——#21 实测 12/14/16/20 非单调，14 质量+延迟双优，见 ADR-0022), `MEMORY_RERANK` (是否在检索链路启用交叉编码器重排，默认 **关**——daemon 已有 BGE-M3，避免再加一份内存), `MEMORY_RERANK_MODEL`, `MEMORY_RERANK_MAX_CHARS` (送排正文截断，默认 **512**；BGE reranker 默认 `max_seq_length=8192`，整条 6000 字条目会慢一个数量级), `MEMORY_RERANK_BACKEND`（`torch`|`onnx`，默认 `torch`=m3；`onnx` = 零新依赖直跑导出的 ONNX 图，如 jina int8，见 #35 / ADR-0022 #35 复验）, `MEMORY_RERANK_ONNX_FILE` / `MEMORY_RERANK_ONNX_MAX_LENGTH` / `MEMORY_RERANK_ONNX_BATCH` / `MEMORY_RERANK_ALLOW_DOWNLOAD`, `MEMORY_SPARSE_BACKEND`（`tfidf`|`bm25`，本地词法稀疏编码器，默认 `tfidf`；`bm25` 需 `memory-agent[bm25]`，见 #40 / ADR-0019 D14）, `MEMORY_SPARSE_BM25_MODEL`, 网关 authn/authz（#32）: `MEMORY_AUTH_PRINCIPAL`（默认 local）/`MEMORY_AUTH_TENANT`（默认空 = 单租户不绑定）/`MEMORY_AUTH_ROLE`（默认 owner）/`MEMORY_AUTH_CLASSIFICATIONS`、`MEMORY_AUTH_RESIDENCIES`（逗号分隔允许集，空 = 全集）/`MEMORY_AUTH_OWNERS`（逗号分隔可写域 owner 集合，空 = 不限制；#36）/`MEMORY_AUTH_TOKENS`（`<token> -> 身份` JSON，身份可带 `owners`；配了默认强制 Bearer）/`MEMORY_AUTH_REQUIRE_TOKEN`/`MEMORY_AUDIT_LOG`（默认 `<MEMORY_INDEX_DIR>/audit.log`）。
- **rerank 送排 token 上限（#28）**：core 层 `ragcore/config/config.py` 的 `RERANK_MAX_SEQ_LENGTH`（env 同名，默认 **512**；`none`/`off`/`0`/空串 = 不设上限、回退模型默认 ≈8192），`RerankerService` 默认取它，legal 与 memory 同时生效。只截断**送进重排器**的 query+doc，不改回给 LLM 的正文。实测：对 memory 严格 no-op（pair ≤381 token），对 legal（块 ≤~820 token）1024/512 也不改排名、无延迟收益；要收益得下到 ~256 或裁候选池。证据 `experiments/rerank-latency-survey/maxlen_results.md` + ADR-0020。

## Working directory
Paths are anchored in code, not to CWD: `ROOT_DIR` / `RAGCORE_DIR` / `LEGAL_WEB_DIR` in `ragcore/config/config.py`, `FRONTEND_DIR` in `legal_web/app.py`. `legal_web/app.py` boots correctly from **any** CWD.

## Environment (`.env`)
配置按**用途分层**（#22 / ADR-0016）：
- **core 层**（`ragcore/config/config.py`，另有 `config/hf.py` 缓存感知离线开关 #18）：路径 / 模型名 / 检索阈值——**无密钥、import 不校验**；`memory_agent` 只走这层。
- **llm 层**（`ragcore/config/llm.py`）：`legal_web/.env`（**gitignored**，适配层入口显式 `load_llm_env()` 加载，进程环境优先）+ `require_llm()` 校验 `API_KEY` / `BASE_URL` / `Model`（capital `M`，不是 `MODEL`）。`legal_web` 启动（lifespan 首步）缺凭证即失败；`require_llm()` 失败只报变量名、不回显值。
- LangSmith 可选：`LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true`（默认 true）才启用；`langsmith_service` **惰性读取**，缺 key 时 trace 静默直通。
- `memory_agent` 用自己的 `MEMORY_*` + `memory_agent/.env`（`MEMORY_ENV_FILE` 可覆盖路径），**不读 `legal_web/.env`**；模板 `memory_agent/.env.example`（无密钥、可提交）。

## Dependencies
- `legal_web/requirements.txt` — authoritative dependency set for this project.
  - Key deps: `fastapi`, `uvicorn`, `openai`, `qdrant-client`, `sentence-transformers`, `flagembedding`, `langchain`, `langchain-core`, `langchain-community`, `langchain-openai`, `langchain-text-splitters`, `pydantic`, `python-dotenv`, `pypdf`, `langsmith`, `python-multipart`.
- Root `requirements.txt` — pinned versions (UTF-8, was UTF-16 LE before a fix).
- `memory_agent/requirements.txt` — only its own dep (`mcp>=2.2,<3`); engine deps are reused from `legal_web/requirements.txt` since it calls `ragcore` in-process.
- `memory_agent[bm25]`（可选 extra，`pyproject.toml`）— `fastembed`，**只有** `MEMORY_SPARSE_BACKEND=bm25` 才需要（#40 / ADR-0019 D7）；不压默认包体。
- 当前 venv 已装 `fastembed`（#40 本地 BM25 实验）。

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
| `POST` | `/documents/upload?view_name=` | Upload document (.pdf/.txt/.md), process (load/split/embed), store in specified Qdrant collection. （`kb_name` 为兼容别名） |
| `GET` | `/documents/count?view_name=` | Return chunk count in specified Qdrant collection. （`kb_name` 别名） |
| `DELETE` | `/documents/clear?view_name=` | Clear all points in the specified Qdrant collection (keeps the collection). （`kb_name` 别名） |
| `GET` | `/view/list` | List all registered views. （`/kb/list` 为隐藏别名） |
| `POST` | `/view/create?name=&label=&description=` | Create a new view (new Qdrant collection). （`/kb/create` 别名） |
| `DELETE` | `/view/{name}` | Delete a view (cannot delete default `documents`). （`/kb/{name}` 别名） |
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
| `view_registry` | View registry — maps view names to Qdrant collections. Persisted to `legal_web/view_registry.json`（旧 `kb_registry.json` 一次性迁移，不删）。旧 `kb_registry.py` 为无逻辑兼容 shim。 |

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
- **No-evidence handling**: if best post-reranker distance > `RELEVANCE_THRESHOLD=0.85`, returns refusal message and skips LLM call（**硬闸门与阈值语义不变**；措辞已软化，ADR-0023）. `RELEVANCE_THRESHOLD=None` to disable hard cutoff.
- **Prompt strategy**: LLM is instructed to answer based on partial context rather than refusing outright. Only refuses when context is completely unrelated.
- `POST /api/chat/stream` streams **JSON Lines** (`application/jsonl`): each line is `{"type":"content"|"metadata"|"error", ...}`. `content` chunks and `metadata` carry a `sources` array `[{content, source, score, chunk_id}]` for citation display. Scores are post-reranker distances (lower = more relevant). `metadata` 另含 **`best_distance`**（最优 post-rerank 距离，可观测置信度；#27）。

## Multi-view & Agent routing
- **View Registry**: `ragcore/services/view_registry.py` manages view metadata. Stored as JSON at `legal_web/view_registry.json`（旧 `kb_registry.json` 一次性迁移）。Default view: `documents` (政策法规视图).
- **Auto-routing**: When `view_name="auto"`, the LangGraph router agent (`ragcore/agents/router_graph.py`) classifies the query and selects the best view. Otherwise, uses the explicitly specified view.
- **Session memory**: `ragcore/agents/session_memory.py` stores recent conversation turns per `session_id`. Used for context-aware query rewriting (future enhancement).
- Create additional views via `POST /api/view/create`, ingest docs into them via `POST /api/documents/upload?view_name=`.

## Frontend
- Vanilla JS SPA with ES modules, served as static files. No build step.
- `legal_web/frontend/script.js` — main controller. `legal_web/frontend/js/` — modules: `apiService`, `messageHandler`, `ragUI`, `config`, `themeManager`, `emojiManager`, `documentManager`.
- **Loading screen**: On page load, shows a two-step progress indicator (Embedding / Reranker). Polls `GET /api/status` every 1s. When `ready=true`, hides overlay and enables chat input.
- **Source citations**: Collapsed card list under each AI message. Score badges color-coded: green (<0.35), yellow (0.35-0.60), red (>0.60).
- **RAG toggle**: Sidebar switch. Stored as `kbRagMode` in localStorage.
- **View selector**: Dropdown in sidebar with `🤖 自动选择` option plus all registered views. Stored as `selectedView` in localStorage（旧键 `kbSelectedKB` 兼容回退）。
- **Versioning**: JS/CSS files use `?v=N` cache busting. Increment when changing any JS module.

## Tests
- **单元测试**：`tests/unit/`（pytest）——`test_document_service.py`、`test_rag_service.py`、`test_session_memory.py`、`test_memory_corpus.py`、`test_memory_index.py`、`test_memory_index_qdrant.py`、`test_memory_reindex.py`、`test_memory_writer.py`、`test_memory_lazy_warmup.py`、`test_memory_concurrency.py`、`test_memory_proxy.py`、`test_mcp_server_cli.py`、`test_config_layering.py`、`test_vector_store_clear.py`、`test_vector_store_filter.py`、`test_vector_store_locking.py`、`test_retrieval_strategy.py`、`test_memory_retrieval.py`、`test_memory_store_port.py`、`test_eval_metrics.py`、`test_reranker_service.py`、`test_gateway_authz.py`、`test_hf_offline.py`、`test_memory_selection.py`、`test_memory_admission.py`（后两个是 #36 收录 / 运行时选择）、`test_memory_onnx_reranker.py`（#35 ONNX 重排器）、`test_memory_bm25.py`（#40 BM25 稀疏接缝）。运行：`venv\Scripts\python.exe -m pytest tests/unit -q`（342 passed）。
- **写路径 sandbox 套件（#16，真实 KB 版，需 BGE-M3）**：`venv\Scripts\python.exe memory_agent/eval/write_path_sandbox.py`——不在 `tests/unit` 内（运行时证据），证据见 `memory_agent/eval/write_path_sandbox_results.md`。
- **个人模式验收（#36，无需模型；Stub 嵌入）**：`venv\Scripts\python.exe memory_agent/eval/personal_mode_36.py`——外部改文件 / 改 overlay 免重启、D13 惰性追平、exclude 预览+确认不误删。证据见 `memory_agent/eval/personal_mode_36_results.md`（15/15，真实语料指纹 172 文件 / 51ms）。
- **网关 authn/authz 套件（#32，无需模型）**：`venv\Scripts\python.exe memory_agent/eval/gateway_authz_32.py`——外部行为断言（身份绑定 / 越权拒绝 / tenant+ABAC 真实 Qdrant 过滤 / 审计不含凭证），证据见 `memory_agent/eval/gateway_authz_32_results.md`（13/13）。
- **记忆检索评测基座（#24，需 BGE-M3 [+ reranker]）**：`venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank`——不在 `tests/unit` 内（运行时证据），基线见 `memory_agent/eval/retrieval_baseline.md`。逐题计时口径见 `experiments/rerank-latency-survey/maxlen_results.md`。
- **rerank token 上限 A/B（#28，需 BGE-M3 [+ reranker]）**：`venv\Scripts\python.exe experiments/rerank-latency-survey/bench_rerank_maxlen.py memory-census|legal-anchor`——结论 `experiments/rerank-latency-survey/maxlen_results.md` + ADR-0020。
- Smoke test: `venv\Scripts\python.exe legal_web/test_langsmith.py`.
- **启动冒烟（boot 闸门）**：后台起 `legal_web/app.py`，独立探测 `/api/status` → `ready:true`、`/` 与 `/script.js` → 200、`/api/view/list`、`/api/documents/count?view_name=documents`，再杀进程树确认端口与 Qdrant 锁释放。命令与结果见 `memory_agent/eval/baseline_A.md`（比"单测 + 导入冒烟"更强的收工锚点）。
- RAG vs LLM-only eval: from repo root run `venv\Scripts\python.exe legal_web/tests/run_eval.py` (backend on :8000, KB built). Parses `legal_web/tests/questions.md` and writes `legal_web/tests/results.md`. Fill `legal_web/tests/failure_analysis.md` for failure cases. `legal_web/tests/score_eval.py` does LLM-as-judge multi-dimension scoring.

## 开发纪律（AI 必走；2026-09-16 重整）

> 本项目执行者是 AI（用户提供思路、AI 开发）。纪律内嵌在流程节点里，不是孤立清单。
> **本文只放长期规矩**；"两条轨道 / 冻结区 / 优先级"属**计划**（见"当前计划"）。
> 详见 `docs/adr/0004-dev-discipline-ai-self-maintenance.md`。

任何工作请求先路由：
- **缺陷** → 根因流程：先测量/复现定位 → 修复 → 留回归证据。
- **新功能** → 功能流程：决策闸门 → 拆单元 → 逐单元实现+提交 → 收尾健康闸门。
- **实验** → 实验流程：`experiments/<name>/`（脚本+数据+结论同处，先写记录再跑）。
- **收工/请求结束** → 健康闸门（每个流程的最后一步，不是可跳过的独立清单）。

### 角色分离（2026-09-16）
- **架构 / 规划层**（owner + 架构会话）：收敛决策 → 写 ADR → 拆票 → **验收合并** → 地图同步。
- **执行 / 实验层**（任务会话）：按 issue 实现或实验，**不做架构决策**；碰到决策**停下来**提给架构层；**不得自行改动架构级东西**（接口、数据模型、ADR 结论）。

### 单元（commit 的粒度）
任何可独立验证、可一句话解释的增量即一个单元：修一个 bug、做一次优化、加一个工具都是单元。判定：一句话能说清 + 能独立验证 → 立即 commit；否则拆小或合并。不要等一个 feature 全做完才提交。

### 决策闸门
大改动前用一句话向用户说明"是什么/为什么/风险"。硬决策（难逆转 / 反直觉 / 真权衡）必须经用户确认，并写进 `docs/adr/`。例行工作自主执行，不需要逐个请示。

### 决策落点（对话 / ADR / issue 三方分工）

同一件事分三处，不许混：

| 用途 | 场地 |
|---|---|
| 收敛（辩论 / 权衡 / 拍板） | **与用户的对话**（或一个专门 RFC 文档） |
| 决策落点（为什么） | **`docs/adr/`** |
| 执行单元（做什么 / 验收 / 阻塞） | **issue**——验收标准 + blocked-by + 指向 ADR 的链接，**不承载辩论** |
| 实现期问答 / 证据 | **issue comment**——阻塞、实测数字、跨会话交接 |

规则：
- **不在 issue 里辩论方向。** issue 只做索引：状态、验收、blocked-by、ADR 链接。
- 硬决策在对话里拍板后，**由 AI 当场起草 ADR（`status: proposed`）**，用户只做"接受 / 改"，不用动笔。
- 研究 / 实验产出进 `experiments/`（一页结论），结论再进 ADR。
- **ADR 粒度 = 一个决策簇（≈ 一个票据 / 一个阶段），内含 D1/D2/D3**，不是"一条决策一 ADR"（先例：ADR-0008/0009/0011）。修订既有决策时新 ADR 标 `amends` / `supersedes`。
- **同类相聚（2026-09-15）**：**同类决策优先并入同簇 ADR（就地 amend 一节），不新开零散 ADR**。只有跨簇/新阶段才新开。
- **ADR 活跃集（2026-09-16）**：`superseded` / `deferred` 的 ADR **不参与规划**。
- **越界（2026-09-16）**：改动属于别的模块 / 会话的职责 → **提 issue**，不自行改。

### 地图同步
结构一变就更新本文件与 `CONTEXT.md`。AGENTS.md 必须始终描述真实目录树，不允许文档脱离实际。

### 实验留痕
实验只进 `experiments/<name>/`。每个实验目录必须含 `README.md`（问题 → 假设 → 设置 → 数据 → 结论）。无结论的实验不算完成。

### 廉价测量优先（先 census，后大评测）

**先做分钟级可行性测量，再排多小时评测。** 2026-09-15 严重误判（误判 + 走弯路）先例：把 memory 的 rerank 截断收益（226s→15s，长条目 6000 字）**外推到 legal 语料**，认定「送排长度」是主杠杆，排了 token 上限 A/B（多趟 ≈880s/趟）——而一次 **token 长度 census**（几分钟）就能定死：memory 候选对 max **381** token、legal 块 ≤~**820** token → 上限 8192/1024/512 在两套语料上**全是 no-op**。证据 `experiments/rerank-latency-survey/maxlen_results.md`、ADR-0020。

纪律：
- **杠杆是语料相关的**——一个语料上的收益不外推到另一个语料。
- 排评测前先问：**有没有几分钟就能量出分布/曲线的办法**（长度分布、recall@k、命中位置分布）？有就先做，再决定要不要排重评测。
- **倍数回算过再写**（226/15≈15x，不是 20x）。
- **已定数值不重跑（2026-09-16）**：很多数值是**固定**的；只在新假设或明确触发条件时重测，**别反复实验确认**。
- **census 能「排除」，不能「证明」**：离线上限论证 ≠ 端到端实测。2026-09-15 池裁剪先例：离线判「池 10/12 无损」（只验首个 gold 在 top-k），实测 pool=12 的 **nDCG@10 掉 0.22%、守门未过**（多 gold 题被挤出）。**要说"可用"，必须跑一次真实链路。**

### 长耗时活不进主会话（规划层不可阻塞）

**主会话只做规划与协调，绝不亲自跑长耗时任务**——大评测 / 训练 / 大扫描一律**独立会话 + `git worktree`**。**派 subagent 也不行**：opencode 里 subagent 是**强阻塞**（返回前主会话无法推进），照样卡住规划层。2026-09-15 教训：主会话连续陷进池 / 延迟评测链，项目规划与协调长时间停摆。

### 交接（2026-09-16）
- **交接书先给 owner 审核，再派**。
- 交接书放临时目录、**引用而非重复**、**redact 密钥**。

### 健康闸门（收工前必跑）
1. `git status` 干净——无未提交工作（那批 CRLF-only 的 ` M` 假脏除外：`git diff --numstat` 应为空）。
2. 本文件与实际目录树一致。
3. 无垃圾文件（日志、临时产物、迭代残留如 `post_refactor*`、`nul`）。
4. 每个实验目录有结论文件。
5. 改动有决策路径落点（`docs/adr/` 或 `experiments/` 记录）。
6. 每个功能过可解释性闸门：一句话讲不清 = 不该进基线。

### Git 工作纪律（并行会话）

**一个工作树只有一个写者。** git 的 working tree 是单 HEAD + 单 index，`checkout` / `switch` / `reset` / `rebase` / `stash` 全是**仓库级**操作——并行会话共用一个工作树时，谁切分支就把别人眼前的文件换掉。2026-09-14 实测踩过：三个"并行"会话共用一个工作树，结果提交落到别人的分支上、工作内容被切换覆盖，排查了很久。

- **要并行 → 一会话一 `git worktree`（单仓库即可，不需要多 clone）**：
  `git worktree add ..\wk-<n> feat/<n>-<slug>`；用完 `git worktree remove ..\wk-<n>`。
- **工作树里一律用主树 venv 的绝对路径**（`venv/` 是 gitignored，工作树里没有）：
  `D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe -m pytest tests/unit -q`。
- **MCP 注册是绝对路径**（指向主树的 `memory_agent/mcp_server.py`）：会话在哪个工作树，跑的都是主树那份 MCP 代码；改 MCP 代码要生效，改主树并重启会话。
- **只提交自己的文件**：`git add -- <自己的路径>`；**禁止** `git add -A` / `git add .` / `git commit -a`——工作树常年带着别的会话的脏改动。
- **那批 CRLF-only 的 ` M` 不要提交**：它们 `git diff --numstat` 为空（无内容改动），是 autocrlf 的 stat 假脏。
- 提交前先看 staged 集合：`git status --short` + `git diff --cached --stat`。
- **分支一票一条**：`feat/<n>-<slug>`，关闭票据后合回 `master`。提交已在别的分支上要挪，用 `cherry-pick`，**不改写共享分支历史**。
- 一个单元 = 一个 commit（见"单元"），消息沿用本仓库的宽松前缀（`feat:` / `fix:` / `docs:` / `chore:`，中文描述）。
- 密钥 / token 永不进提交。

### 安全
- 密钥 / token **永不落盘或提交**；凭据走**进程环境**；错误信息不回显凭证。

## 当前计划（2026-09-16 重整）

**两条轨道 + 一个冻结区**（这是**计划**，不是纪律）：

- **主线 = 个人模式**（唯一开发重点）：**#41 一步安装 + 本机第二消费者** · **agent loop**（待开票）· 收尾。
  **检索沿用现状、主线不碰检索**（owner 决定 2026-09-16「B」）。
- **并行轨 = 检索优化**（**执行 / 实验**会话，自负验收合并）：**#21**（伞）· **#35**（deferred）· **#40**（deferred）。
  跨轨**只提子 issue**；固定 / 已定数值不重跑。
- **冻结区 = 共享 / 云**：**#38 联邦 · #39 租户泄漏修复 · #34 隔离套件 · #33 后续 · ADR-0015 / 0018** 移出计划（**0018 标 deferred**；#39 是已定位的真实缺陷，解冻时第一件修）。
- **明确排除：`legal_web`**（上一版本遗留的无关产品；仅保留代码）。

## 当前路线图（2026-08 一周冲刺）

- ✅ 0. 机制落地 + 清理（垃圾已删；`eval_service.py` 迁入 `experiments/query-rewrite-optimizer/`；诊断产物 `llm_refusal_trace.txt`/`diagnostic_output.txt` 留待根因调查）。
- ✅ 1. 初始基线 commit（本地 + 远程，单根 `e723e5b`）。
- ✅ 2. 延迟调查（rerank-latency + e2e-latency 两轮实验）：生产 rerank mean ≈15s（CPU 固有，接受为已知限制）；"分钟级"真凶 = 优化器实验的 eval_service（对子查询重排 + pool 100，已记录发现，实验封存不修）；pool 截断延后为 planned optimization；模型加载已加 `local_files_only=True`（不下载权重）。**#18 已修**：`local_files_only` 不挡 HF 元数据解析，弱网下冷启动会被拖到分钟级甚至加载失败 → 改为「缓存感知自动离线」（见下）。
- ✅ 3. 拒答根因调查（≤1 天，时间盒）：结论=机制健康（基线误拒率 2.4%、无答案拒答率 91.7%），唯一误拒为 reranker 边界分(0.87>0.85)+anchor 污染，选 A 接受现状并记录（详见 `experiments/refusal-root-cause/`）。
- ✅ 4. 评估×2 → 因选 A 无二次评估，`legal_web/tests/results_scored.md` 即唯一最终证据（拒答 21→12，context_precision 3.4→4.8，source_recall 1.0）。
- ✅ 5. README 简历门面（评测证据 + 工程纪律 + 目录修正）+ 终版整理。
- MCP（延后）：stdio + `list_kbs`/`search`/`ask`，复用 service 层。

## 方向重定向（2026-09-14）

项目升级为 **Agent-Knowledge-Base**：hero = agent 记忆能力包（MCP + skill）；`legal_web` **【已排除】**（上一版本遗留的无关产品，仅保留代码）。决策见 `docs/adr/0005`–`0007`，领域语言见 `CONTEXT.md`。

- ✅ 甲⁺ 布局重排（#9 第一轮）：`backend/` 拆为 `ragcore/` + `legal_web/`，新建 `memory_agent/`；T1 锚点复现（71 passed + 导入冒烟 + 启动冒烟，见 `memory_agent/eval/baseline_A.md`）。
- ✅ 更名（#9 第二轮，2026-09-14 完成）：GitHub 仓库已改为 `Alex-tangt/Agent-Knowledge-Base`，`origin` 是干净 URL（原嵌的明文 token 已移除）；本地目录已改名（会话内被 MCP 子进程 CWD 锁住，由用户在会话外完成）。在新路径复跑锚点验收：`pytest tests/unit -q` → 71 passed、legal_web 导入冒烟 → import-ok。venv 采用"移动后原样验证"策略，一律用 `venv\Scripts\python.exe -m ...`（`Scripts\*.exe` 内嵌旧绝对路径已失效，不使用）。详见 `docs/adr/0007`。
- ✅ 读路径最小闭环（#10，2026-09-14）：`memory_agent` 条目级派生索引（复用 `ragcore` BGE-M3 + Qdrant，独立路径）+ stdio MCP `memory_search`/`memory_get`；60 条（20 可写 KB / 40 只读本仓库）。决策见 `docs/adr/0008`。
- ✅ 写入网关（#11，2026-09-14）：MCP `memory_add`——写前检索去重（命中近似只报告、不写）、frontmatter 镜像 `kb.py check` 校验（另强制 domain↔type）、路径级单文件 git commit（只提交本条目，避开并发会话的脏改动）。决策见 `docs/adr/0009`；单测 `tests/unit/test_memory_writer.py`（106 passed 全绿）。
- ✅ 生命周期工具（#12，2026-09-14）：MCP `memory_supersede`（新建 + 双向标注 `supersedes`/`superseded_by`，新旧同一次 commit）与 `memory_archive`（置 `status: archived` + `archive_reason`，永不删文件）；两者默认只返回 `confirmation_required` 预览，需 `confirm=true` 才落盘。决策见 `docs/adr/0010`；单测 `tests/unit/test_memory_writer.py`（117 passed 全绿）。
- ✅ skill 骨架（#14，2026-09-14）：`memory_agent/skill/SKILL.md`（源，安装到全局 `~/.config/opencode/skills/memory-agent/`）——读/写/生命周期工具用法 + 破坏性确认规则（先预览、用户同意后才 `confirm=true`）。决策见 `docs/adr/0012`；行为验收在 #17 dogfood，不在本票。
- ✅ 惰性预热（#19 第一步，2026-09-14）：MCP 服务**默认不再预热** BGE-M3（要低延迟可设 `MEMORY_WARMUP=1`）。实测启动私有内存 **3953MB → 54MB**。根因：每个 opencode 会话各拉起一份 MCP、各吃 ~3.9GB（BGE-M3 权重 2.17GB + torch 运行时 + 加载峰值），三条并行会话把系统 commit 打满 → `Out of memory` / 卡死 / `uv_spawn` 失败。#19 已关闭（共享单实例根治，见下）；「换小模型」为独立正交备选，另行评估。
- ✅ 索引一致性（#13，2026-09-14）：代目录 + `CURRENT` 指针原子切换（中断不留「空索引 + 陈旧 manifest」，`search` 自洽核对失败显式报错）；按条目增量刷新（hash 未变跳过、孤儿点按稳定 `uuid5(entry_id)` 删除）、写后自动刷新钩子；分块 `memory_reindex(cursor,batch)` 全量重建 + `memory_index_status`。决策见 `docs/adr/0011`；单测 `test_memory_reindex.py`（135 passed 全绿）；真实重建 gen-1：68 条 = 68 点，`refresh` 68 skipped / 0 embedded。
- ✅ 共享单实例（#19 根治，2026-09-14）：拓扑改为「一个常驻 HTTP daemon（持有唯一一份 BGE-M3）+ 每会话一个 stdio 代理」——N 会话从 `N × 3.9GB` 降为 `1 × 3.9GB + N × 几十MB`。`mcp_server.py --transport http` + `/health` + DNS-rebinding 防护；`proxy.py` 幂等拉起（启动权文件锁，避免冷启动竞态）+ 透明转发；进程内串行化 store/索引/写入。决策见 `docs/adr/0013`；验收（真实模型）见 `memory_agent/eval/issue19_acceptance.md`（最终 `154 passed`）。opencode 接入从「跑 `mcp_server.py`」改为「跑 `proxy.py`」（`~/.config/opencode/opencode.json`，重启生效）。
- ✅ 写路径 sandbox 套件（#16，2026-09-14）：真实 KB 克隆 + 隔离索引，25/25 通过且两次运行一致、真实 KB 前后逐字不变；顺带校准 `DEDUP_THRESHOLD` 0.92→0.88（`experiments/dedup-threshold-calibration/`，回写 ADR-0009）。证据见 `memory_agent/eval/write_path_sandbox_results.md`。
- ✅ #17 dogfood（2026-09-14）：经 MCP 真写一条决策 + 双通道盲测 recall（独立进程 + 独立子代理，同 score），证据见 `memory_agent/eval/dogfood_17.md`；关闭 **#17**。检索质量（#15）归独立叙事 **#21**，不占 MVP 收尾。
- ✅ 三仓库只读语料（#7 收尾，2026-09-15）：用户故事 #17 落地——只读语料从"本仓库"扩到 **三个项目仓库的 Markdown 文档**（只索引 `.md`，不索引代码）。仓库清单 gitignored（`readonly_repos.json` + `.example`），`<label>/` 前缀消歧义，噪声目录排除。索引 `gen-2`：**134 条 = 26 可写 KB + 108 只读文档**（agent-knowledge-base / kg-triplet-sft / agent-infra）。MCP 接缝验收 **15/15**（`readonly_corpus_17.py` + `_results.md`）。顺带修掉 MCP 工具错误消息被吞的缺陷（`ValueError` → `ToolError`）。决策见 `docs/adr/0014`。**#7 全部用户故事落地，epic 已关闭。**

### MVP 收官（2026-09-15）

- ✅ **MVP 完成**：epic #7 已关闭；#8–#14、#16–#19 全部 closed。`#15`（BEIR nDCG@10）移出 MVP，并入独立叙事 **#21**（RAG 检索优化）。
- 收尾锚点：`pytest tests/unit -q` → **163 passed**；写路径 sandbox 25/25；只读语料接缝 15/15；dogfood 真写 + 跨会话 recall（证据见 `memory_agent/eval/`）。
- 交付物：记忆能力包 = stdio 代理 + 常驻 daemon（单实例 BGE-M3）+ 8 个 MCP 工具 + skill。
- **Post-MVP 规划（架构师负责）**：RFC **#20** 双平面（私有本地 + 多租户企业云 + 数据库式治理）；检索优化叙事 **#21**（对象=记忆检索，法律链路降为回归锚点）。P0 基座期票：**#22** config 分层、**#23** `VectorStore` 端口 + `classification/residency`、**#24** 检索评测基座、**#25** 云向量库 spike、**#26** 独立包化（blocked by #22/#23）。执行序 **P1 独立包 → P2 云基座 → P3 联邦治理**；通用 agent demo 后延。

### P0 基座期

- ✅ config 解耦（#22，2026-09-15）：`ragcore/config` 拆 core / llm 两层——core 无密钥、import 不校验；llm 层惰性 `require_llm()`。`memory_agent` 用 `MEMORY_*` + 独立 `memory_agent/.env`（进程环境优先），**不读 `legal_web/.env`**；`langsmith_service` 改惰性读取；`legal_web` 启动（lifespan 首步）缺凭证显式失败。无 `legal_web/.env` 时单测 **175 passed**（改动前 8 collection error）、`dotenv` 守卫记录 0 次 legal_web 读取、`config.llm` 未被 import。决策见 `docs/adr/0016`，证据 `memory_agent/eval/config_independence_22_results.md`。后续：**#23** → **#26**（#24 / #25 可并行）。
- ✅ 云向量库多租户/权限 spike（#25，2026-09-15，**已合 master**）：一手对标 Zilliz / Qdrant / Pinecone / Weaviate + **真实云端 smoke**。结论：云托管免费档**无**服务端强制的行级隔离、**无**可用 RBAC / 审计（Zilliz 实测 + 官方文档 + CLI help 三证；四家云均无行级），唯一能把权限下到 tenant 的是 Weaviate Cloud。证据 `experiments/cloud-vector-db-spike/`。据此拍板：**D1=B** 共享集合 + `tenant` 字段 + 网关强制过滤（预留分层）、**D2** 网关唯一强制（store RBAC 仅加分项）、**D3** 分层租户（组织 = 隔离/计费边界，团队 = 组内视图）、**D4** 维持 Zilliz + Weaviate 作升级备选。决策见 `docs/adr/0018`（accepted）。
- ✅ 记忆检索评测基座（#24，2026-09-15，**已合 master**）：确定性评测集（51 条 = 45 有答案 + 6 无答案，条目级二值）+ harness（`--mode vector|hybrid|hybrid-rerank`、`--trace` 断点续跑、`run_hash` 验确定性），**不调 LLM**；第一步把记忆检索接 `ragcore` 策略接缝（hybrid + rerank）。基线 **hybrid-rerank nDCG@10=0.9658 / MRR=0.9778 / recall@1=0.8593 / 0 miss**（`memory_agent/eval/retrieval_baseline.md`）。关键发现：ragcore legal 式「关键词优先」融合**对记忆检索有害**（纯 hybrid recall@1 0.64→0.25，rerank 能救回但融合本身是 #21 优化点）；rerank 默认 `max_seq_length=8192` 拖慢两个数量级 → 新增 `MEMORY_RERANK_MAX_CHARS=512`。人工抽检 2026-09-15 用户确认。单测 **210 passed**。→ **解锁 #23**。
- ✅ 记忆存储端口 + 驻留/密级（#23，2026-09-15）：抽 `VectorStore` 端口（`add/search/delete/count/clear/warmup`，`search` 带 `tenant`+`payload_filter`）——`memory_agent/memory/ports.py`；本地 Qdrant 薄适配器 + 工厂 `memory_agent/memory/store.py`（`MemoryIndex`/`Reindexer` 只经工厂拿存储，不再 import Qdrant 细节）。条目 payload 镜像 `classification`（缺省 private）/ `residency`（缺省 local）可选 frontmatter 字段；检索命中带 `classification`/`residency`/`provenance={plane,tenant}`，绑定租户只可收窄不可放宽（ADR-0018 D2）。单测 **218 passed**。决策见 `docs/adr/0019`。→ **解锁 #26**。
- **P0 基座期 ✅ 完成**（#22 / #23 / #24 / #25）。
- **P1 ✅ 完成**：**#26 独立包化**（ragcore 真包 + `memory_agent` 可安装；`docs/adr/0024`；合并 `df85e4d`）。验收：**227 passed** + **启动冒烟**（ready ~44s、`/` 与 `/script.js` 200、kb/list 2、documents/count 3799）+ **MCP 安装冒烟 9/9**（`memory_agent/eval/mcp_install_smoke_26_results.md`）+ 任意 CWD import。**前置**：`pip install -e ragcore -e memory_agent`（本次补齐了 venv 里缺失的 editable 安装）。
- **并行**：**#29 rerank 横评**（回答"rerank 值不值得默认开"；worktree `wk-29`）。
- ✅ 默认检索融合对照与选型（#30，2026-09-15）：默认（rerank 关）融合由「关键词优先」改为**加法关键词增强** `score = 余弦 + 0.05 × (命中词数/关键词数)`——记忆检索 recall@1 **0.2500 → 0.7074**（纯向量 0.6407）、nDCG@10 0.5739→**0.8817**、MRR 0.4936→**0.8731**；β 平台 **[0.05,0.08]**。**RRF / 等权归一化在本语料反而 < 纯向量**（关键词路低精度：CJK 二元组一题命中 ~55 噪声条）。rerank 增量 **+0.1741 recall@1 / +0.0892 nDCG@10**，且**旧/新融合在 rerank 下逐位相同**（候选并集相同、交叉编码器与融合顺序无关）→ 修融合只影响**默认（rerank 关）**体验；rerank 是否默认开是延迟/内存权衡（供 #29）。池默认 **14 确认**（`ADR-0022` provisional 解除）。决策就地 amend `docs/adr/0022`（D4–D6）；证据 `experiments/fusion-selection/`；单测 **231 passed**。
- **排队**：**#27 软拒答**（收窄为"保留硬闸门 + 只软化措辞"，`docs/adr/0023`）。
- **#15 BEIR 已关闭**（被 #24 的三档消融取代，对外可比性需要时再开）。
- **P2 前置 ✅ 已决**（#31）：**就地 amend** `docs/adr/0018`（D2.1–D2.4：authn 在 `/mcp` 边界 → 会话上下文 → 工具层 authz；proxy 非信任源）+ `docs/adr/0019`（D4–D6：检索归 store、各平面用各自原生、分数/阈值/评测按平面；本地走 Qdrant 原生 sparse+RRF + fastembed）。证据 `experiments/qdrant-local-mode-capabilities/`。**P2 已拆票**（2026-09-15）：**#32 ✅ 网关** · **#33 云 store 适配器**（blocked by #30 → **已解锁**）· **#34 隔离绕过套件**（blocked by #32 → **已解锁**）；均以两 ADR 为准。
- ✅ **网关 authn/authz（#32，feat/32-gateway-authz）**：`memory_agent/gateway/`——`/mcp` 边界 authn（进程配置 / Bearer token）→ 请求期身份上下文 → 工具层**强制过滤注入**（tenant + classification/residency，白名单构造、**只可收窄**、越权拒绝）；审计 JSONL；**proxy 非信任源**。就地 amend `docs/adr/0018`（D2.5–D2.8）/ `docs/adr/0019`（D3 多值 `payload_filter` → Qdrant `MatchAny`）。验收：**248 passed** + 套件 13/13（`memory_agent/eval/gateway_authz_32_results.md`）。→ 解锁 **#34 隔离绕过套件**。
- env knob 变更：`MEMORY_RETRIEVAL_POOL` 默认 **20 → 14**（`docs/adr/0022`：质量 + 延迟双优）。
- **合并后锚点（2026-09-15，#30 + #32 + #18）**：`pytest tests/unit -q` → **261 passed**；网关套件 **13/13**；启动冒烟不再需要手动设 `HF_HUB_OFFLINE=1`（#18 已修：缓存命中自动离线）。
- ✅ **个人模式架构锁定（ADR-0025，2026-09-16 accepted）**：Post-MVP 形状由"双平面（本地/企业云）"改为 **所有权轴（个人/共享）× 部署轴（本地/托管）**；本质 = **以「文件 + git」为基表、ANN 为派生索引的知识数据库**（基表 / 域 / 视图 / 集合；**读视图、写域**；中央索引就地索引；**收录 = 来源注册表默认 ∪ 独立 overlay**；**查询时惰性刷新**，无 watcher；多写者仅在"共写域"时需真 DB）。取代 ADR-0015 D1 主轴；ADR-0006 收窄到单写者；ADR-0011 写后钩子改为惰性（D13）。词汇见 `CONTEXT.md`（视图 / 域 / 集合 / 基表 / 来源注册表 / 收录 / 授权表）。新票：**#36 个人模式落地**（动态语料 + overlay 收录 + 惰性刷）、**#37 命名迁移**（库 → 视图）。
- ✅ **个人模式落地（#36，feat/36-personal-mode）**：`settings.READONLY_ROOTS` 的 import-时求值改为**运行时重读**（`loader.resolve_selection`：注册表默认 ∪ overlay include/exclude）；条目带 `owner`/`root`/`mtime_ns`/`size`；`memory_search` 前 **stat-only 指纹**比对（172 文件 / 51ms）→ 变更才增量重嵌（D9）；注册表/overlay 读不出或来源根不可达 → `complete=false` 不删条目（孤儿安全，修 ADR-0014 风险）；写后**不再**同步刷（D13），`memory_reindex` 仍全量重建；收录工具 `memory_ingest_list/_include/_exclude`，受限身份按 `MEMORY_AUTH_OWNERS` 只能收自己拥有的域。验收：**291 passed** + `personal_mode_36.py` 15/15（`memory_agent/eval/personal_mode_36_results.md`）。→ 解锁 **#37 命名迁移**。

## 后续优化待办（Backlog / 简历谈资池）

非本周范围，延后。独立叙事 **#21** 收拢以下检索优化（原 #15 BEIR 亦并入）。每条都是可讲的优化故事：

1. **查询改写质量**：改写为关键词组合可能导致语义检索效果下降（尤其多约束复合句丢约束）。问题节点：查询改写（`rag_service._rewrite_query`）、问题分解（eval_service 多路）。关联：`experiments/query-rewrite-optimizer/` 结论（二元意图判断与关键词改写结构性冲突）。
2. **延迟优化：pool 边界**：`ADAPTIVE_POOL=20` 与候选池实际 ~32（vector+keyword+anchor 合并）的边界是否合理；pre-rerank 候选截断 knob（实测 rerank 线性于池大小，~230ms/对）。关联：`experiments/rerank-latency/`、`experiments/e2e-latency/`。**#28 已试「送排 token 上限」杠杆：1024/512 对 legal 不改排名也无收益（块 ≤~820 token），仅 256 有 ~1.4x；默认取 512 作兜底，降延迟仍走裁池**（ADR-0020、`experiments/rerank-latency-survey/maxlen_results.md`）。
3. **混合检索融合机制**：vector + keyword + anchor 三类信号的融合/加权是否最优（anchor 命中过多可能淹没向量信号）。问题节点：`ragcore/strategies/legal.py` 的 `_add` 合并逻辑。**#30 已修默认（memory）融合**——关键词优先 → 有界加法增强（`docs/adr/0022` D4），legal 的 anchor 加权融合仍待评估。
4. **记忆 rerank（保留方案，未启用）**：消费者口径（`memory_search` 默认 k=5）下 rerank 增量仅 recall@5 **+5.0pp**（≈2/45，显著性未验），代价却是 NC 许可（jina int8）+ `optimum` 把 `transformers` 降级（整仓）。**已拍板不设默认、推迟实现、留缺口**——方案与触发条件见 `docs/adr/0022` #29 追加 / **#35（deferred）**。想提排序优先走免费杠杆（返回条数 k、BGE-M3 原生 sparse/colbert，见 ADR-0019 方向）。证据 `experiments/rerank-model-survey/`。

## Gotchas
- **Always activate venv first** (`venv\Scripts\activate` on Windows). Running without it may miss installed dependencies.
- **Qdrant local mode 的锁按操作持有**（`VectorStoreService` 每次操作开/关一个 client，见 ADR-0008 D5）。`legal_web` 与 `memory_agent` 现在可以并存；只有两个进程的重活**恰好撞在同一瞬间**才会短暂争锁，靠内置退避重试兜住。若仍报 "already accessed"：确认没有残留进程，必要时删 `.lock`。**同进程内也不能并发开 client**（单 daemon 服务 N 会话时）——`VectorStoreService._session` 用模块级 `RLock` 串行化，见 ADR-0013 D3。
- **stdio MCP: stdout is the protocol channel.** `ragcore/utils/logger.py` configures logging to `sys.stdout`; `memory_agent` must grab the root logger to stderr *before* importing `ragcore` (`_bootstrap.configure_stderr_logging`). Any stray stdout write corrupts the JSON-RPC stream. `proxy.py` 同理：它不 import ragcore，所有诊断写 stderr，stdout 只留给 stdio 协议。
- **Qdrant local mode 清空集合不要丢集合**：实测 `delete_collection` / `recreate_collection` 只摘元数据，同名 `create_collection` 会让磁盘上的旧点**复活**（3 → 0 → 3，静默失效）。`VectorStoreService.clear_all_documents` 改用空 filter 的 `FilterSelector` 删光点；回归见 `tests/unit/test_vector_store_clear.py`。
- **包命名空间（ADR-0024）**：`ragcore` 是真包——一律 `from ragcore.services.reranker_service import ...` 这样带 `ragcore.` 前缀导入；**不要再加 sys.path 垫片，也不要再用裸 `services/`、`config/` 顶层名**。`memory_agent` 依赖已安装的 `ragcore`（`pip install -e ragcore -e memory_agent`），并继续用 `memory_agent.` 前缀绝对导入。
- **First run** after `pip install` downloads BGE-M3 (~2.2GB) and bge-reranker-v2-m3 (~2.2GB) from HuggingFace. Subsequent runs load from cache instantly.
- **HF 外呼（#18 已修）**：`local_files_only=True` 只挡文件下载、**不挡** HF 元数据/revision 解析（会发 `GET /api/models/<repo>` 等）；弱网/代理不稳时冷启动被拖到分钟级甚至 `ValueError` 失败。现在 `ragcore/config/hf.py::ensure_hf_offline()` 在 import HF **之前**按「模型是否已缓存」自动切离线（`HF_HUB_OFFLINE=1`+`TRANSFORMERS_OFFLINE=1`），两个模型服务各自调用；有模型缺失则保持联网并告警（首次下载仍可用）。显式设置 `HF_HUB_OFFLINE`（含 `0`）不覆盖。复现与数据见 `experiments/hf-offline-warmup/`；因此启动冒烟**无需**再手动设离线。
- **`memory_search` 前会做 stat-only 指纹检查**（#36 / ADR-0025 D9）：无变更时只 stat（172 文件 ~51ms），有变更才读语料 + 增量重嵌；`memory_get` 直接读真相源、不触发刷新。写入工具返回的 `index` 字段是 `mode=lazy`（D13，不再嵌入）。`refresh()` 返回值新增 `deferred_removed` / `complete`。改 `readonly_repos.json` / `overlay.json` 免重启，但来源根暂时不可达时条目**不会被删**（`complete=false`，只报 `deferred_removed`）。
- **`RELEVANCE_THRESHOLD=0.85`** is a generous post-reranker value; the prompt handles most boundary cases. Use `experiments/relevance-calibration/calibrate_relevance.py` to recalibrate if needed.
- **别把「一个语料的杠杆」外推到另一个语料**（2026-09-15 严重误判）：memory 的 rerank 截断收益（226s→15s，长条目）不能外推到 legal（块 ≤820 token，长度从来不是约束）。**先做分钟级 census（长度分布 / recall@k / 命中位置）再排多小时评测**。详见「开发工作流 · 廉价测量优先」。
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
