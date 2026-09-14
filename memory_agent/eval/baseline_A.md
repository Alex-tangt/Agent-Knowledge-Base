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

结论：甲⁺ 布局重排未改变 `ragcore` 行为，锚点保持可复现。

