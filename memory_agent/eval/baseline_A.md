# 基准 A（重构前锚点）

> 记录时间：2026-09-14。目的：甲⁺ 重构前的"没搬坏"锚点。
> 位置说明：票据原定 `memory_agent/eval/baseline_A.md`，但该目录在甲⁺ 重构（#9）后才存在；重构时移入。

## 结论

- **单元测试**：`71 passed in 2.57s`。
- **导入冒烟**：在 `backend/` 下 `import app, services, strategies, api.routes, services.rag_service, services.vector_store_service` → `import-ok`（LangSmith 未配置的 WARNING 可忽略）。
- 基线状态视为健康，可开始重构。

## 命令

```powershell
# 单元测试（仓库根）
venv\Scripts\python.exe -m pytest tests/unit -q

# 导入冒烟（legal_web 目录）
..\venv\Scripts\python.exe -c "import app, api.routes, services, strategies, agents; import services.rag_service, services.vector_store_service; print('import-ok')"
```

## 环境

- Python 3.12.2（venv）
- git commit：记录时见 `git rev-parse --short HEAD`

## 为什么不重跑完整 LLM 评测

`legal_web/tests/run_eval.py` 打 HTTP 后端 + LLM 生成 + LLM 裁判：

1. **非确定性**——LLM 生成与裁判每次不同，无法精确复现 `legal_web/tests/results_scored.md`，"数字一致"闸门本身脆弱。
2. **即将被替换**——甲⁺ 重构后 `legal_web` 变适配层、`memory_agent` 走进程内引擎，该管线不复用。
3. 甲⁺ 是机械移动（`git mv` 级），真正的回归证据是**单元测试 + 导入冒烟**，不是重跑一小时 LLM 管线。

当前 canonical 评测数字以 `legal_web/tests/results_scored.md` 为准，不再重跑。新的评测证据由票据 #16（写路径确定性测试）与 #15（BEIR nDCG@10）承担。

## 重构后复跑记录（#9 甲⁺，2026-09-14）

- [x] `pytest tests/unit -q` → **71 passed in 2.42s**
- [x] legal_web 导入冒烟 `import app, api.routes, services, strategies, agents; ...` → **import-ok**
- [x] `config.config` 路径锚点核对：`LEGAL_WEB_DIR` / `VECTOR_DB_PATH` / `UPLOAD_DIR` 均落在 `legal_web/`
- [x] `compileall ragcore legal_web memory_agent tests experiments` → exit 0
- [x] **启动冒烟（boot 闸门）** → 见下

### 启动冒烟（boot 闸门）——比"单测 + 导入冒烟"更强

单测与导入冒烟只到 import 级，覆盖不到 uvicorn lifespan / `StaticFiles` 挂载 / 真实端点。补一个运行时闸门（后台启动 + 子进程内重定向 + 独立探测就绪；勿用 `Start-Process -Redirect*`）：

```powershell
# 启动后独立探测（就绪约 22s）
Invoke-RestMethod http://127.0.0.1:8000/api/status
Invoke-WebRequest  http://127.0.0.1:8000/ -UseBasicParsing
Invoke-WebRequest  http://127.0.0.1:8000/script.js -UseBasicParsing
Invoke-RestMethod  http://127.0.0.1:8000/api/kb/list
Invoke-RestMethod "http://127.0.0.1:8000/api/documents/count?kb_name=documents"
# 收尾：杀进程树，确认 8000 释放、Qdrant 锁释放
```

结果：`ready:true` 约 22s；`/` → 200（index.html）、`/script.js` → 200（`StaticFiles` 挂载正确）；`/api/kb/list` 读到 `legal_web/kb_registry.json` 的 2 个 KB；`/api/documents/count` → `{"count":3799}`，日志 `VectorStoreService initialized with Qdrant local mode` → 确认 `VECTOR_DB_PATH` 落 `legal_web/vector_db`。

**副作用观察**：启动日志出现一次对外 HF 请求（`HEAD https://huggingface.co/BAAI/bge-m3/resolve/refs%2Fpr%2F130/model.safetensors.index.json → 404`），尽管两个加载器都设了 `local_files_only=True`。离线主机上可能表现为超时等待。已单独开 issue #18 讨论，不属本次重构。

结论：甲⁺ 布局重排未改变 `ragcore` 行为，锚点（单测 + 导入冒烟 + 启动冒烟）保持可复现。

## 重构后复跑记录（#26 ragcore 包化，2026-09-15）

`ragcore` 改为真包、退役 sys.path 垫片（ADR-0024）后的锚点复跑：

- [x] `python -m pytest tests/unit -q` → **227 passed in 62.27s**
- [x] **导入冒烟（干净解释器、不靠 CWD）**：`import ragcore, memory_agent, memory_agent.mcp_server`
      在仓库外的 CWD、`PYTHONPATH` 只指向已安装目录时成功。
- [x] **启动冒烟（boot 闸门）**：`ready:true` 约 36s；`/` → 200、`/script.js` → 200、
      `/api/kb/list` → 2 个 KB、`/api/documents/count?kb_name=documents` → `{"count":3799}`；
      杀进程树后 8000 端口释放。
- [x] **MCP 集成冒烟（#26 验收）**：`memory_agent/eval/mcp_install_smoke_26.py` → **9/9**，
      证据见 `memory_agent/eval/mcp_install_smoke_26_results.md`。
- [x] #22 独立性守卫复跑 `memory_agent/eval/config_independence_22.py` → `passed: true`。

跑法（先 `pip install -e ragcore -e memory_agent`）：`tests/unit` 用
`venv\Scripts\python.exe -m pytest tests/unit -q` 从仓库根运行；启动冒烟用
`venv\Scripts\python.exe legal_web/app.py`。

