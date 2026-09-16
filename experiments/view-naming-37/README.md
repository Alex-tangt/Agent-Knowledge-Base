# #37 命名迁移：知识库（kb）→ 视图（view）——验证证据

> 依据 `docs/adr/0025`（Consequences · 命名迁移）与 `CONTEXT.md`（**视图 / 域 / 集合 / 基表 / 收录 / 授权表**）。
> 本票只做**命名**迁移，不改语义（legal"一库一集合" → 视图=过滤 **不在本票**）。

## 问题

代码 / 前端里的 `kb`（`KBRegistry` / `kb_name` / `kb_registry.json` / `kbSelectedKB` …）
与领域词汇「视图（View）」不一致，混淆了「视图（读谓词）」与「集合（物理 ANN）」。

## 设置（范围与不做）

**改**：`ViewRegistry` / `view_registry.json` / 策略 `view_name` / 公开 API `view_name` /
前端选择器（id/class/localStorage/函数名）/ `?v=4`。

**不改**（另有归属，见「残留」）：
- `ragcore/services/rag_service.py`、`ragcore/agents/router_graph.py`——**内部接缝**仍用
  `kb_name` / `kb_list`（`rag_service` 是 handoff 明确「不碰」的文件），经**兼容 shim** 继续可用。
- `memory_agent/**`——那里的 `KB_DIR` / `load_kb_entries` 指**基表 / 域**，与「视图」不是同一概念。
- `AGENTS.md` / `CONTEXT.md`——协调层统一同步。

## 兼容别名（全部显式标注 deprecated / 隐藏）

| 旧名 | 状态 | 落点 |
|---|---|---|
| `ragcore.services.kb_registry`（`KBRegistry` / `KB_REGISTRY_FILE` / `kb_registry`） | 无逻辑 shim，指向 canonical | `ragcore/services/kb_registry.py` |
| `legal_web/kb_registry.json` | 读不到新文件时**一次性迁移**到 `view_registry.json`（旧文件不删） | `view_registry.py::_load` |
| `POST /api/chat/stream` body `kb_name` | `validation_alias` 别名（`view_name` 优先） | `ragcore/models/schemas.py` |
| `?kb_name=`（upload / count / clear） | 兼容别名（`view_name` 优先） | `legal_web/api/routes.py::_resolve_view_name` |
| `GET /api/kb/list`、`POST /api/kb/create`、`DELETE /api/kb/{name}` | 隐藏（`include_in_schema=False`）+ deprecated，转调 canonical handler | `legal_web/api/routes.py` |
| 前端 `localStorage.kbSelectedKB` / `kbRagMode` | 读取回退（canonical = `selectedView` / `ragMode`） | `legal_web/frontend/js/ragUI.js` |

## 数据（2026-09-16）

分支 `feat/37-view-naming`（worktree `wk-37`），起点 `3e9aaa6`（master）。

```
313dd3a feat: 命名迁移 kb→view（核心：ViewRegistry + 策略 view_name + 兼容 shim）
150dd36 feat: 公开 API 参数 kb_name→view_name（保留 kb_name 兼容别名）
b39ff1d feat: 前端 kb→view 命名迁移（localStorage 旧键兼容回退 + ?v=4）
```

### 单测

```
venv\Scripts\python.exe -m pytest tests/unit -q
299 passed in 72.06s   # 基线 291 + 新增 tests/unit/test_view_registry.py 8
```

`test_view_registry.py`：canonical CRUD / 旧文件一次性迁移 / 兼容 shim 同对象 /
策略 `view_name` 解析与 fallback —— 全部锚定 tmp 目录，不碰真实 `legal_web/`。

### 启动冒烟（boot 闸门）

真实 uvicorn + lifespan + `StaticFiles`；**warmup 被 patch 成 no-op**（`memory_agent`
daemon 已持有一份 BGE-M3，规则 7：同一时刻只跑一个吃模型的重活；命名迁移与模型加载无关）。
Qdrant 指向 `vector_db` 的临时副本（不写主树），LLM 用 dummy env（无密钥）。

| 探测 | 结果 |
|---|---|
| `GET /api/health` | `{"status":"healthy"}`（boot ~8s） |
| `GET /` | 200，含「视图选择」，无 `kb-select` 残留 |
| `GET /script.js`、`/js/apiService.js`、`/js/ragUI.js`、`/styles.css` | 200 |
| `GET /api/status` | `{"embedding":"pending","reranker":"pending","ready":false}`（warmup 已 patch） |
| `GET /api/view/list` | 2 视图，label = `政策法规视图` / `技术文档视图` |
| `GET /api/kb/list`（别名声明确认） | 同上（同 2 视图） |
| `GET /api/documents/count?view_name=documents` | `{"count":3799,"view_name":"documents"}` |
| `GET /api/documents/count?kb_name=documents`（别名） | `{"count":3799,"view_name":"documents"}` |
| `GET /openapi.json` paths | 仅 `/api/view/*`（`/api/kb/*` 已隐藏） |
| 杀进程后 `:8013` | 已释放 |

### 其它闸门

- **请求体别名**：`ChatRequest(view_name=…)` / `ChatRequest(kb_name=…)` / 缺省 →
  `tech_docs` / `legacy_view` / `documents`。
- **任意 CWD 导入冒烟**：仓库外 CWD `import app, ragcore.services.view_registry,
  ragcore.services.kb_registry, ragcore.strategies` → `import-ok`。
- **前端语法**：`node --check`（8 个 JS 模块，临时目录内 .mjs）→ 全 OK。
- **工作树**：`git status --short` 干净；`git diff --numstat` 空（无 CRLF 假脏）。

## 结论

命名迁移端到端可用：新名（view）为 canonical，旧名（kb）全部是**显式标注的兼容别名**，
前端选择 / 上传 / 计数 / 清空与别名路径均工作，`?v=4` 已同步。单测 299 passed、启动冒烟绿。

## 残留（协调层 / 后续票）

1. **文档**：`AGENTS.md` / `CONTEXT.md`（协调层统一同步）；`README.md`、
   `legal_web/data/SOURCES.md`、`docs/` 历史报告（`rag_testset_research` / `report_draft` /
   `test_report` 等）里的「知识库」措辞——多为历史证据或指代基表/产品名，未动。
2. **内部接缝**：`rag_service.py` / `router_graph.py` 的 `kb_name` / `kb_list` /
   `_default_kb`（改它需同步 `rag_service`，handoff 列为「不碰」）。
3. **用户可见串**：`rag_service.py::NO_EVIDENCE_MESSAGE`（"知识库中未找到直接依据…"）——
   与 `legal_web/tests/score_eval.py` 的 `NO_EVIDENCE_HINTS` 与历史评测结果绑定，改它要
   连带改评测锚点。
4. **品牌 / 功能名**：前端标题「知识库问答助手」、卡片「知识库文件 / 知识库回答 /
   启用知识库模式」——`CONTEXT.md` 保留「Agent 知识库」作为产品名，故未改。
5. `memory_agent/**` 的 `KB_DIR` / `load_kb_entries` 等——语义为**基表 / 域**，非视图。
