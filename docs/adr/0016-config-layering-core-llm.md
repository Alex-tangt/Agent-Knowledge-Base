# 0016 配置分层：core / llm 两层 + memory_agent 配置来源独立

Status: accepted

issue #22 落地。解除 `memory_agent` 对 `legal_web/.env` 的传递性依赖——那是目的 **a**
（可独立发布的能力包）的前置，也清掉 ADR-0008 Consequences 里记的"已知债务"。

## 背景

`ragcore/config/config.py` 在 import 期 `load_dotenv(legal_web/.env)` 并校验
`API_KEY/BASE_URL/Model`，缺任一 `raise ValueError`。而 `memory_agent` 只为拿
`services.vector_store_service` 就传递性 import 了它——读路径根本不用 LLM（ADR-0008 D3）。
结果：**缺 LLM 凭证的进程连 `memory_agent.runtime` 都 import 不了**。证据：无
`legal_web/.env` 时 `pytest tests/unit` 8 个 collection error，同根于 `config.py:15`。

## 决策

**D1 core / llm 分层，校验惰性化。** `ragcore/config/` 分两层：

- **core**（`config/config.py`）：目录锚点、模型名、检索阈值、`VECTOR_DB_PATH`/`UPLOAD_DIR`、
  `CORS`/`API_PREFIX`——**无密钥、import 不校验**。任何进程（含只用检索的 `memory_agent`）
  都能安全 import。
- **llm**（`config/llm.py`）：`API_KEY/BASE_URL/Model` + 可选 LangSmith。只有真正要构造
  LLM client 时才 `load_llm_env()` + `require_llm()`；`require_llm()` 缺凭证抛
  `ValueError`（沿用原消息），**只报变量名、不报值**。

**D2 memory_agent 配置来源独立。** `memory_agent` 用 `MEMORY_*` env 命名空间 + 自己的
`memory_agent/.env`（`settings.load_env_file`，`override=False` → 进程环境优先；
`MEMORY_ENV_FILE` 可覆盖路径）。**不 import `config.llm`、不读 `legal_web/.env`。**
llm 层的 `legal_web/.env` 只归适配层。

**D3 LangSmith 惰性读取。** `langsmith_service` 不再在 import 期读 env（否则
`memory_agent` 一 import `vector_store_service` 就会经 trace 装饰器连带读
`legal_web/.env`）。改为首次 `is_enabled` / `client` 访问时经 `config.llm.langsmith_settings()`
读进程环境；适配层在自己的入口先 `load_llm_env()` 把 `.env` 灌进进程环境。

**D4 显式失败点 = 适配层启动。** `legal_web/app.py` 在 import 顶部 `load_llm_env()`
（保住"路径 override 来自 .env"的旧行为），并在 lifespan **首步** `require_llm()`——
缺凭证**启动即失败**，不后移成首个请求的静默错误（原 import 期失败等价物）。

## 理由

分层的判据是**用途**而非"文件好看"：读路径与写路径都不需要 LLM，把 LLM 凭证的校验
绑在 import 上，等于给不需要它的能力加了一道与本机 `.env` 的存在性强耦合。惰性化后：
`memory_agent` 自足；`legal_web` 仍是"缺凭证即启动失败"。

Considered options:
- A core/llm 分层 + 惰性校验（采用）。
- B 只把 `config.py` 的 `raise` 删掉、校验全交给各 client 构造点（弃）：`legal_web` 的
  启动失败会后移成首个请求的静默错误，违背"显式失败"。
- C 保留 import 期校验，另给 memory_agent 一份 `config` 副本（弃）：两份漂移，且没解耦。

## Consequences

- 解除 ADR-0008 的已知债务：memory_agent import ragcore 不再要求 LLM 凭证。
- `legal_web/.env` 的**非 LLM** 变量（如 `UPLOAD_DIR`/`VECTOR_DB_PATH` override）依赖
  适配层入口先 `load_llm_env()`；直接用 core 层的第三方进程只认进程环境。
- `VECTOR_DB_PATH` / `UPLOAD_DIR` 仍是 core（路径无密钥）；若将来要注入密钥到 store，
  另走 llm/secrets 层。
- 校验消息保持中文原文，失败只含变量名（密钥不进日志/异常）。

## 证据

`memory_agent/eval/config_independence_22_results.md`（+ `config_independence_22.py`）：
无 `legal_web/.env` 时导入冒烟通过、`pytest tests/unit` 175 passed、`dotenv` 守卫记录
0 次 `legal_web` 读取、`config.llm` 未被 import、`legal_web` 启动抛 `ValueError`。

Relates: #22、#20、#23、#26、ADR-0008（债务解除）、ADR-0006。
