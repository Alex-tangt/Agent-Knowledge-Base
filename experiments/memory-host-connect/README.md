# M6 真实 agent 宿主接入：opencode + DeepTutor → 同一个隔离服务实例

日期：2026-10-10。执行线：**host-connect**（worktree `wk-host`，分支 `feat/memory-mvp-hosts`）。
写域：`experiments/memory-host-connect/`（本目录）。
状态：**M6 已实测完成（2026-10-10 15:27–15:45 CST）——判据 PASS；范围级读/写权限已于 15:52 在宿主侧补齐
（切 authz profile + 只读身份，见 `M6-report.md` §9）。**

> **实测结果与证据见 [`M6-report.md`](M6-report.md)**（本文是运行手册与口径，报告是结果）。
> 速览：两宿主接入同一实例（先 `auth_mode=dev` 跑共享读写，后 `auth_mode=api_key` 跑范围权限）；
> 跨宿主 `search` 命中 + `read` **逐字节相同**；两宿主各完成一次允许的写入；**云调用 0 / 费用 0**；
> 授权子树内只读身份**写被 403 `PERMISSION_DENIED`**、读正常。

对应验收：`experiments/memory-framework-spike/mvp-protocol.md` **M6**「至少两个实际 agent 宿主接入同一服务，
完成共享内容读取与一次允许的写入；记录启用步骤、依赖/存储体积、启动/常驻资源、模型调用和维护步骤」；
接入方顺序依据 `docs/adr/0032-personal-memory-base-adopt-then-productize.md` **D6**（本轮第二接入方 = DeepTutor）。

---

## 1. 环境事实（已只读核实，2026-10-10）

| 事实 | 值 | 核实方式 |
|---|---|---|
| 候选服务 | OpenViking Python 包 **0.4.23**；`ov` 二进制自报 **0.4.24.dev0**（两条版本线） | `venv-ov\Lib\site-packages\openviking-0.4.23.dist-info`；`ov --version` |
| 服务端入口 | `D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\openviking-server.exe`（`--config/--host/--port`） | `openviking-server.exe --help` |
| 服务端 MCP 端点 | **`/mcp`**（streamable HTTP，`stateless_http=True`），注册 **16 个工具**：`find search read write edit list tree remember add_resource add_skill list_watches cancel_watch grep glob forget health` | `openviking\server\app.py:788-808`（注释即权威清单）+ `server\mcp_endpoint.py` 的 `@mcp.tool` |
| 服务端身份 | HTTP 头 `X-OpenViking-Account` / `X-OpenViking-User`，凭证走 `x-api-key` 或 `Authorization`；localhost 默认可走 dev/trusted（无需 key） | `server\mcp_endpoint.py:178-231`；`server\auth\plugins\dev.py` |
| **DeepTutor 安装位置** | 包 **1.6.8** 在 `D:\Users\Tan\AppData\Local\Programs\Python\Python312\Lib\site-packages\deeptutor`（系统 Python **3.12.2**），CLI = 同目录 `Scripts\deeptutor.exe` | `Get-Command deeptutor` |
| **DeepTutor 运行时 home** | `D:\Study\my-deeptutor`（只有 `data\`） | 目录列举 |
| **DeepTutor 配置加载器** | `deeptutor.services.mcp.config.load_mcp_config()` → `MCPConfig`（pydantic）；路径 `mcp_config_path()` = `<DEEPTUTOR_HOME>/data/user/settings/mcp.json` | `services\mcp\config.py:115-131`、`multi_user\paths.py:89-150`、`services\path_service.py:211` |
| **DeepTutor mcp.json 形状** | `{"servers": {<name>: MCPServerConfig}}`；`MCPServerConfig` = `type(stdin/sse/streamableHttp) command args env cwd url headers tool_timeout enabled_tools disabled_tools enabled auth catalog_entry` | `services\mcp\config.py:31-98` |
| DeepTutor MCP 连接路径 | `MCPConnectionManager.ensure_started()/status()/call_tool()`；`probe_server()` 做一次性 connect+list_tools；streamableHttp 走官方 `mcp.client.streamable_http.streamable_http_client` | `services\mcp\manager.py:262-472, 831-845, 893-931` |
| DeepTutor 单轮入口 | `deeptutor run <capability> "<message>" --format json` | `deeptutor_cli\main.py:81-121` |
| DeepTutor home 解析 | 显式参数 > `DEEPTUTOR_HOME` > CWD（`runtime\home.py:28-43`） | 同左 |
| **opencode** | 版本 **1.18.35**；配置目录 `%USERPROFILE%\.config\opencode\`（`config.json`/`opencode.json`/`opencode.jsonc`）；有 `opencode mcp list` 与 `opencode run --format json -m provider/model --auto --dir` | `opencode --version` / `--help` / 隔离 HOME 实测 |
| **本机可用本地模型** | Ollama `qwen3:0.6B` / `qwen2.5:3b-instruct` / `qwen3:8b`（`http://127.0.0.1:11434/v1`） | `ollama list` |
| 隔离实例 | 端口 **8782**，配置与数据在 `%USERPROFILE%\.openviking-mvp-host\`（= `C:\Users\Tan\.openviking-mvp-host\`） | task-2 配方（task-1 提供） |

> ⚠️ **安全观察（未复现、未落盘）**：用户真实 `D:\Study\my-deeptutor\data\user\settings\model_catalog.json`
> 里有一条**明文云模型 API key**。本线**不复制**该文件（隔离副本另写一份只有本地 Ollama 的目录），
> 证据里也不会出现任何 key 值。建议 owner 知悉该明文存储。**本仓库/本线不改用户真实配置。**

---

## 2. 隔离配方（本线专用，绝不与 8781 / 8783 两条线共用）

```
%USERPROFILE%\.openviking-mvp-host\            ← 服务实例（task-1 负责：ov.conf + data\）
    hosts\                                     ← 本线（host-connect）独占
        opencode-home\.config\opencode\opencode.json   宿主①隔离配置（隔离 HOME）
        opencode-project\                              宿主①工作目录
        deeptutor-home\data\user\settings\mcp.json     宿主②隔离配置（隔离 DEEPTUTOR_HOME）
        deeptutor-home\data\user\settings\model_catalog.json  本地 Ollama，无 key
```

硬约束落实方式：

| 约束 | 落实 |
|---|---|
| 不改 `~/.config/opencode/opencode.json` | opencode 子进程 `USERPROFILE`/`HOME`/`XDG_CONFIG_HOME` 全指向隔离 home；真实注册只读哈希 |
| 不改用户真实 DeepTutor 配置 | `DEEPTUTOR_HOME` 指向隔离 home；真实 `mcp.json`/`model_catalog.json` 只读哈希 |
| 不碰真实 KB / 索引 / 8765 daemon / `~/.openviking` | 全部 URL 只指向 8782；`verify_isolation.ps1` 断言隔离副本不含 8781/8783/8765 |
| 不启用云模型 | 两宿主模型剖面都指本机 Ollama（`binding=ollama` / opencode `provider.ollama`）；云调用数 = 0 |
| 密钥永不落盘/回显 | 隔离副本无任何 key；`host_deeptutor.py` 与 `collect_cost.ps1` 统一脱敏 `sk-*` / `Bearer *` |
| 不 `git add -A` | 只 `git add -- experiments/memory-host-connect/` |

---

## 3. 运行手册（已按此实跑；完整可复现序列见 `M6-report.md` §7）

```powershell
cd D:\python_work\work2026-4\wk-host\experiments\memory-host-connect
$py  = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\python.exe"
$dtpy = "D:\Users\Tan\AppData\Local\Programs\Python\Python312\python.exe"

# 0) 本线实例（8782；dev profile；配置/数据在 %USERPROFILE%\.openviking-mvp-host\）
& $py host_server.py start --profile dev
& $py host_server.py status          # 期望 auth_mode=dev

# 1) 生成两宿主隔离配置（幂等；末尾自带「真实配置零写入」核验）
powershell -File setup_isolation.ps1 -OllamaModel "qwen2.5:3b-instruct-q4_k_m"

# 2) 宿主① opencode：连接状态 + 真 agent 轮次（本地 Ollama，非云）
powershell -File host_opencode.ps1 -Model "qwen2.5:3b-instruct-q4_k_m"
#   证据 → evidence\opencode\{mcp-list.txt, agent-turn.jsonl, run-meta.json}

# 3) 宿主② DeepTutor：宿主自身配置加载器 + MCP 客户端路径（读 + 写 + 读回 + 跨宿主逐字节比对）
& $dtpy host_deeptutor.py `
    --home "$env:USERPROFILE\.openviking-mvp-host\hosts\deeptutor-home" `
    --authn-mode "dev (server auto-detected on localhost; ROLE=root)" `
    --peer-disk-path "$env:USERPROFILE\.openviking-mvp-host\data\viking\mvp-host\resources\mvp-host-connect\opencode.md" `
    --json-out evidence\deeptutor\connect.json
#   附加（本线未跑，原因见 M6-report §5.3）：powershell -File host_deeptutor_agent.ps1 -Model qwen3:8b

# 4) 身份 / 范围对照（账户隔离 vs 账户内共享）
& $py scope_probe.py --json-out evidence\scope-probe.json

# 5) 成本账
powershell -File collect_cost.ps1

# 6) 隔离与真实配置核验（收工前必跑）
powershell -File verify_isolation.ps1

# 7) 补充：范围级读/写权限（宿主侧只读身份）——切 authz + 登记身份 + ACL + 两角色对照
powershell -File run_supplement_authz.ps1
#   root key 只在那个 shell 的进程环境里（不落盘）；重跑会换 key 并重新 provision。
#   回到 dev 形态：& $py host_server.py start --profile dev
```

`setup_isolation.ps1` 之外的每个脚本都可单独重跑；证据文件每次覆盖，便于「同一命令重跑得同样结果」。

### 3.1 解阻前已跑的 pre-flight（`evidence/preflight/`）

服务未起时能验的先验掉了，结果如下（都是 task-1 解阻前完成的）：

| 检查 | 命令 | 结果 |
|---|---|---|
| 隔离副本生成 + 真实配置零写入 | `setup_isolation.ps1` | **PASS**：`opencode.json` `a2157e84…`、DeepTutor `mcp.json` `7073b474…`、`model_catalog.json` `faf58381…` 写前写后一致（`evidence/real-config-baseline.json`） |
| 独立复核隔离 | `verify_isolation.ps1` | **PASS**（exit 0）：三条真实配置哈希一致 + 隔离副本不含 8781/8783/8765 + 两个副本 URL 都指向 8782 |
| **opencode 自身**读隔离配置并连接 | `host_opencode.ps1 -SkipAgentTurn` | 读到隔离 `opencode.json`，识别 `openviking-mvp` 并尝试连 `http://127.0.0.1:8782/mcp` → `failed`（服务未起，符合预期）。证据 `preflight/opencode-mcp-list.preflight.txt` |
| **DeepTutor 自身**配置加载器 | `host_deeptutor.py --dry-run` | **PASS**：`load_mcp_config()` 解出 `type=streamableHttp`、url=8782、`enabled_tools`=读+write、`disabled_tools`=forget/add_skill/add_resource；`tool_allowed` 白名单判定 PASS（`preflight/deeptutor-config-check.json`，deeptutor 1.6.8 / Python 3.12.2） |
| DeepTutor 连接路径**负控** | `host_deeptutor.py`（服务未起） | **预期失败且错误明确**：`probe_server ok=false err=ConnectError: All connection attempts failed`（`preflight/deeptutor-connect-negative.json`，exit 3） |
| 成本初值 | `collect_cost.ps1` | `venv-ov` **1058 MB** / 497 顶层目录；`ov` 二进制 0.4.24.dev0；mcp SDK **1.30.0**；opencode 1.18.35；DeepTutor 1.6.8；Ollama 三个本地模型（`evidence/cost/cost.json`） |

**pre-flight 抓到并已修掉的真实缺陷**：宿主 `NO_PROXY` 含 `[::1]` 时，DeepTutor 的 httpx 在**发出连接之前**
就抛 `InvalidURL: Invalid port: ':1]'`（= mvp-protocol「四个启动坑」②）。现在 `host_deeptutor.py` 在
import/连接前做 `sanitize_no_proxy()`（记录 before/after 到证据），opencode 侧在 PowerShell 层做同样清理。

### 3.2 Lead 已裁决的口径（2026-10-10，写死）

**① 两宿主的差别（证据必须写清，不许含糊成「两个都一样」）**

| | 宿主① opencode | 宿主② DeepTutor |
|---|---|---|
| 驱动方 | **agent 驱动**：本地 Ollama 模型的真 agent 轮次，**模型自己决定**调哪些 MCP 工具 | **宿主客户端驱动**：DeepTutor 自身 `probe_server` / `MCPConnectionManager` 路径替它调用 |
| 主证据 | `evidence/opencode/agent-turn.jsonl` + `run-meta.json`（`driver` 字段） | `evidence/deeptutor/connect.json`（`driver` 字段） |
| 附加（不要求） | — | `deeptutor run chat` 真 agent 轮次（`host_deeptutor_agent.ps1`），跑通则作**附加**证据 |
| 云凭据 | 不用 | **不得动用**；需要云凭据才能做的部分记「未测 + 原因」 |

**② authn 按事实记录，不把「没设 key」写成「已授权」**：MVP 隔离实例按 localhost、无 key 走；
身份区分用宿主自带机制（`X-OpenViking-Account` / `X-OpenViking-User`）。最终 authn 模式作为一条
**事实**写进证据（`connect.json` 的 `authn.mode` / `run-meta.json` 的 `authn.mode`；用 `--authn-mode`
传入 task-1 给出的实际值）。若服务要求 key 才能区分身份/范围，按 task-1 给的注入方式（只记变量名/头名）注入。

**③ 写入口以实例 conf 的实际可写域为准**：默认写 `viking://resources/mvp-host-connect/<host>.md`；
若与 task-1 实例允许的域不一致，改到实例允许的路径并在**证据里记明差异**（`--write-uri` 可覆盖）。

---

## 4. M6 逐项 → 证据映射（**已实测**，详见 `M6-report.md` §3）

| M6 要证明什么 | 证据文件 | 实测结果 |
|---|---|---|
| 宿主① 接入同一服务 | `evidence/opencode/mcp-list.txt` | **PASS**：`✓ openviking-mvp connected http://127.0.0.1:8782/mcp` |
| 宿主① 共享读取 | `evidence/opencode/agent-turn.jsonl` | **PASS**：agent 主动调用 `openviking-mvp_write`；同账户资源对宿主②可见 |
| 宿主① 一次允许的写 | 同上 | **PASS**：`Wrote 75 bytes … semantic=complete`（模型随后循环重写，见 §3.1；判据是原始事件流） |
| 宿主② 接入同一服务 | `evidence/deeptutor/connect.json` → `probe_server(list_tools)` | **PASS**：`ok=true`，**20 个工具**（服务器实际注册数；app.py 注释里的 16 是旧值） |
| 宿主② 共享读取 | `connect.json` → `search` / `cross_host` | **PASS**：`search` 命中含宿主①条目；`read` 取回宿主①的字节，**byte_identical=true** |
| 宿主② 一次允许的写 | `connect.json` → `tool_calls[write]` + `readback_matches_write` | **PASS**：写 82 B → 读回 82 B 内容一致 |
| 身份与范围生效 | `evidence/scope-probe.json` + 配置 `headers`/`enabled_tools` | **PASS**：账户隔离 / 账户内共享 / 无身份拒绝 实测；**宿主侧只读身份**已在 authz profile 补齐（`evidence/supplement-authz/`，`M6-report` §9） |
| 依赖/存储体积 | `evidence/cost/cost.json` → `dependencies` / `storage` | **PASS**：venv-ov 1058 MB；隔离根 74 MB（宿主副本 65.4 / 服务数据 8.6） |
| 启动/常驻资源 | `cost.json` → `resident` | **PASS**：服务 pid 25624 `PrivateMemorySize64` 1223.8 MB；opencode 轮次峰值 851 MB；注明跨进程 RSS 不可信 |
| 模型调用与费用 | `cost.json` → `model_calls` + 事件流 | **PASS**：云调用 **0** / 费用 **0**（全本机 Ollama） |
| 维护步骤 | 本文件 §3 + `cost.json` → `maintenance_steps` | **PASS**：步骤可复现 |

---

## 5. 成本账口径（M6 要求「记录」，不是估算）

- **依赖体积**：候选 venv `venv-ov` 整目录 MB + `site-packages` 顶层目录数；DeepTutor 侧复用系统
  Python 3.12 的既有安装（本线**不新增**依赖）；宿主新增依赖 = 0。
- **存储体积**：隔离根总 MB；服务 `data\`（原文面 + 检索面两处都要记）；宿主配置 KB 级。
- **启动/常驻资源**：8782 监听进程的 `WorkingSet64` / `PrivateMemorySize64`（**下界观察**）+
  进程自报（若服务提供）+ 宿主进程。
  ⚠️ 依据 `AGENTS.md`（perf_70 实测）：本机对分离启动进程的跨进程内存查询会返回**假值**
  （持 300MB 的进程三家工具都报 ~4.4MB）——所以数对不上**不是数字错**，真值以自报为准。
- **模型调用次数与费用**：本轮两宿主都用**本机 Ollama**（`qwen2.5:3b-instruct` / `qwen3:8b`），
  云模型调用 **0 次、费用 0**；`model_calls.cloud_calls=0`。本地调用次数从宿主事件流/日志计。
  若 owner 要求用云模型复测，须先单独授权（本线默认不启用云模型）。
- **维护步骤**：见 §3 与 `maintenance_steps`。

---

## 6. 失败与未知项（**跑完已更新**；逐条原因见 `M6-report.md` §5）

1. ~~opencode 的 remote MCP 报 `SSE error`~~ → **已复核 PASS**：服务起来后 `opencode mcp list` 报
   `✓ openviking-mvp connected http://127.0.0.1:8782/mcp`，未出现 SSE 回退问题；agent 轮次也正常派发工具。
   （先前那段 `SSE error` 只是**服务未启动**时的连接失败措辞。）
2. **DeepTutor 本地模型的工具调用**：`binding` ∈ {`ollama`,`vllm`,`lm_studio`,`llama_cpp`} 被列入
   `_NATIVE_TOOL_BLOCKED_BINDINGS`（`runtime\agentic\client.py:52`）→ 本地模型不走原生 function-calling。
   **Lead 已裁决（2026-10-10）**：DeepTutor 主证据 = `host_deeptutor.py`（宿主自身 MCP 客户端路径）；
   `deeptutor run chat` 只作附加、跑不通就记「未测 + 原因」，**不得为此动用云凭据** → 本线**未跑**该项。
3. **服务端 authn 模式**：先实测 `/health` = `auth_mode=dev`（事实），**后来为补齐范围权限切到
   `auth_mode=api_key`**。**Lead 已裁决（2026-10-10）**：按无 key 走并把模式作为**事实**写进证据；
   **「没设 key」未被写成「已授权」**。dev 恒 ROLE=root ⇒ 只读身份无法在 dev 表达，已在 authz profile
   **宿主侧**补齐（`M6-report` §9）；dev 期证据文件保持冻结未改。
4. **写入面**：实测 `viking://resources/**` 与服务器端 `write` 工具自述的可写域一致，**无需改路径**。
   本线用 `write` 工具，不用 `remember`（走会话抽取、有附带成本）、不用 `forget`/`add_skill`。
5. **DeepTutor 隔离 home 的最小性**：实测 `mcp.json` + `model_catalog.json` 足够跑通 `probe_server` /
   `MCPConnectionManager`（宿主客户端路径），无需补真实 `main.yaml`/`system.json`。
6. **模型侧的坑（照实记，非接入缺陷）**：3B 本地模型会**参数错配**与**成功后循环重写**；
   `qwen3:8b` 在 opencode 下报 `Unexpected server error`（未定位）。opencode 侧**agent 主动性**证据成立，
   但「一次干净可控的 agent 写入」受模型能力限制。
7. **opencode 仍读全局 skill 目录**：隔离 HOME 下启动日志出现 `duplicate skill name`（来自
   `~/.claude/skills`、`~/.agents/skills`）——只读、不写，但「隔离 HOME」≠「完全隔离所有输入」。
8. **不可用错误（M4 与本线交集）**：服务停掉时宿主侧应报明确错误（opencode `failed`；
   DeepTutor `probe_server ok=false` + `describe_connect_failure`）。这一条本线可顺带取证，
   但 M4 的主证据归 task-1。

---

## 7. 裁决与事实的落点（全部已闭环）

**已由 Lead 裁决（2026-10-10）并已在实测中执行**：
1. 宿主口径：opencode = agent 驱动（主证据）；DeepTutor = 宿主自身 MCP 客户端路径（主证据）；
   `deeptutor run chat` 仅作附加、未跑；**未动用云凭据**。两宿主差别见 §3.2 ① / `M6-report` §2。
2. authn：localhost 无 key 走，身份用宿主自带 `X-OpenViking-Account` / `X-OpenViking-User`；
   **实测 `auth_mode=dev` 作为事实写进证据**，未把「没设 key」写成「已授权」。
3. 写入口：实测与默认一致（`viking://resources/**` 可写，服务器 `write` 工具自述可写域相同）→ **无需改路径**。

**task-1 给出的两个事实（已收到并落实）**：
1. **authn** = 参考实例 `auth_mode=dev`（dev 恒 ROLE=root，无法表达只读身份）→ 已写进
   `evidence/deeptutor/connect.json` 的 `authn.mode`；只读身份强制记为未测（原因见 `M6-report` §5.2）。
2. **可写域** = 单账户下 `viking://resources/**` 账户内所有已认证用户默认可读且可写；`viking://user/<id>/**` 私有。
   → 本线用 `scope_probe.py` 在同一实例上**实测**了这三条（`evidence/scope-probe.json`）。
