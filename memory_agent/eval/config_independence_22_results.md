# #22 config 解耦验收证据（core/llm 分层 + memory_agent 独立 .env）

Status: **通过**（2026-09-15，worktree `feat/22-config-decouple`）
脚本：`memory_agent/eval/config_independence_22.py`（可重跑）
决策：`docs/adr/0016-config-layering-core-llm.md`

## 结论

| 验收项 | 结果 |
|---|---|
| 无 `legal_web/.env` 时 `import memory_agent.runtime` + 构建/检索/写入路径可跑 | ✅ 导入冒烟通过；`pytest tests/unit` **175 passed** |
| `memory_agent` 从不读 `legal_web/.env` | ✅ 子进程 `dotenv` 守卫记录 0 次 `legal_web` 读取 |
| `memory_agent/.env` 被加载（进程环境优先） | ✅ 哨兵 env 生效；单测覆盖 override=False |
| `legal_web` 缺凭证仍**显式**失败 | ✅ 启动（lifespan 首步 `require_llm()`）抛 `ValueError` |
| `pytest tests/unit -q` 全绿 | ✅ **175 passed**（163 + 12 新增） |
| 云端密钥不进日志/异常 | ✅ 校验失败只报变量名；`.env.example` 无密钥；`git check-ignore memory_agent/.env` 命中 |

## 关键实验：把 `legal_web/.env` 从等式中拿掉

worktree 里**没有** `legal_web/.env`（gitignored）。同一份测试套件：

- **改动前**（`master`）：`pytest tests/unit -q` → **8 collection errors**，全部同根因：

  ```
  ragcore\services\vector_store_service.py:16: in <module>
      from config.config import (
  ragcore\config\config.py:15: in <module>
      raise ValueError("API_KEY环境变量未设置！请检查.env文件配置")
  ```

  即：memory_agent 的读路径被一个它根本用不到的 LLM 凭证校验整条挡死。

- **改动后**（本分支）：`175 passed in 31.40s`。

## 守卫机制（怎么证明"没读"）

子进程注入 `sitecustomize.py`，在 `dotenv.load_dotenv` 上挂钩：记录每次调用的解析路径，
凡路径含 `legal_web` 立即 `AssertionError`。同时从子进程环境剥掉
`API_KEY/BASE_URL/Model/LANGSMITH_*`，并用 `MEMORY_ENV_FILE` 把 memory_agent 的 `.env`
指向临时哨兵文件。

脚本实测输出（摘要）：

```json
{
  "import_smoke": {
    "imported_runtime": true,
    "imported_vector_store": true,
    "llm_layer_imported": false,
    "own_env_loaded": "own-env-loaded"
  },
  "pytest_summary": ["175 passed in 31.40s"],
  "legal_web_reads": [],
  "explicit_failure_ok": true,
  "passed": true
}
```

`llm_layer_imported: false` = memory_agent 连 llm 层（`config.llm`）都没 import——
core / llm 的分界在 import 图上成立，不只是"恰好没调用"。

## `legal_web` 启动显式失败（实测）

```
$ venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'legal_web'); sys.path.insert(0,'ragcore');
    from fastapi.testclient import TestClient; import app as m;
    TestClient(m.app).__enter__()"
2026-09-15 ... - utils.logger - INFO - Application startup
ValueError  # API_KEY 缺失，lifespan 首步 require_llm() 抛出，不后移成静默
```

## 安全

- 本文件与脚本**不含任何凭据值**；`MEMORY_CLOUD_*` 等只以变量名出现。
- `.env.example` 可提交、无密钥；`memory_agent/.env`、`legal_web/.env` 均被 `.gitignore` 忽略
  （`git check-ignore` 分别命中）。
- `require_llm()` 失败只输出变量名，不回显值（单测 `test_require_llm_error_does_not_leak_value`）。
