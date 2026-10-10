# M6 真实 agent 宿主接入：opencode + DeepTutor → 同一个隔离服务实例

日期：2026-10-10。执行线：**host-connect**（worktree `wk-host`，分支 `feat/memory-mvp-hosts`）。
写域：`experiments/memory-host-connect/`（本目录）。
状态：**脚本与步骤已就绪；接入测试未跑（task-2 被 task-1 阻塞，共享实例 8782 尚未就绪）。**

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

## 3. 运行手册（task-1 解阻、8782 就绪后按序执行）

```powershell
cd D:\python_work\work2026-4\wk-host\experiments\memory-host-connect

# 0) 前提：隔离实例在 8782 就绪（task-1 的产出；本线不启动服务）
Invoke-RestMethod http://127.0.0.1:8782/health     # 期望就绪

# 1) 生成两宿主隔离配置（幂等；末尾自带「真实配置零写入」核验）
powershell -File setup_isolation.ps1 -ServiceUrl http://127.0.0.1:8782/mcp

# 2) 宿主① opencode：连接状态 + 真 agent 轮次（本地 Ollama）
powershell -File host_opencode.ps1 -Model qwen2.5:3b-instruct
#   证据 → evidence\opencode\mcp-list.txt、agent-turn.jsonl、agent-turn.log

# 3) 宿主② DeepTutor：宿主自身配置加载器 + MCP 客户端路径（读 + 写 + 读回）
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" host_deeptutor.py `
    --home "$env:USERPROFILE\.openviking-mvp-host\hosts\deeptutor-home" `
    --json-out evidence\deeptutor\connect.json
#   可选（更强证据）：真 agent 轮次
powershell -File host_deeptutor_agent.ps1 -Model qwen3:8b

# 4) 成本账
powershell -File collect_cost.ps1

# 5) 隔离与真实配置核验（收工前必跑）
powershell -File verify_isolation.ps1
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

## 4. M6 逐项 → 证据映射（当前状态：**未测**，等 task-1）

| M6 要证明什么 | 证据文件 | 判据 | 状态 |
|---|---|---|---|
| 宿主① 接入同一服务 | `evidence/opencode/mcp-list.txt` | `opencode mcp list` 显示 `openviking-mvp` 为连接成功（非 failed） | 未测 |
| 宿主① 共享读取 | `evidence/opencode/agent-turn.jsonl` | 事件流里出现 `openviking-mvp` 的工具调用与返回正文 | 未测 |
| 宿主① 一次允许的写 | 同上（`write` → `viking://resources/mvp-host-connect/opencode.md`） | 调用成功 + 返回 `Wrote N bytes` | 未测 |
| 宿主② 接入同一服务 | `evidence/deeptutor/connect.json` → `probe_server(list_tools)` | `ok=true`，工具数 = 16（或白名单内数量） | 未测 |
| 宿主② 共享读取 | `connect.json` → `tool_calls[search/read]` | search 命中另一宿主写的 marker；read 读回 opencode 写的正文 | 未测 |
| 宿主② 一次允许的写 | `connect.json` → `tool_calls[write]` | 写成功；随后 `read` 读回一致 | 未测 |
| 身份与范围生效 | 配置 `headers` + `enabled_tools`；服务端审计 | 两宿主不同 `X-OpenViking-User`；`forget`/`add_skill` 被白名单挡下（`tool_whitelist` 步） | 未测 |
| 依赖/存储体积 | `evidence/cost/cost.json` → `dependencies` / `storage` | 数值记录即可 | 未测 |
| 启动/常驻资源 | `cost.json` → `resident` | 记录，注明跨进程 RSS 不可信、以进程自报为准 | 未测 |
| 模型调用与费用 | `cost.json` → `model_calls` + 会话事件流 | 云调用 0 / 费用 0；本地调用次数来自事件流 | 未测 |
| 维护步骤 | 本文件 §3 + `cost.json` → `maintenance_steps` | 步骤可复现 | 就绪 |

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

## 6. 失败与未知项（先登记，跑完更新）

1. **opencode 的 remote MCP 报 `SSE error`**（隔离 HOME 实测，服务未启动时）：
   opencode 1.18.35 对 `type: remote` 的探测信息以 SSE 措辞呈现。**服务起来后必须复核**它最终
   走的是 streamable HTTP 还是 SSE 回退；若是 SSE 回退而我们只暴露 `/mcp`，需在 §3 记录改用
   `--header`/URL 形态或换 `type`。
   *（pre-flight：配置解析与 8782 寻址已确认；协议回退待服务就绪后复测。）*
2. **DeepTutor 本地模型的工具调用**（见 §3.2 ①）：`binding` ∈
   {`ollama`,`vllm`,`lm_studio`,`llama_cpp`} 被列入 `_NATIVE_TOOL_BLOCKED_BINDINGS`
   （`runtime\agentic\client.py:52`）→ 本地模型不走原生 function-calling，走文本工具协议，3B 可能不稳。
   **Lead 已裁决（2026-10-10）**：DeepTutor 主证据 = `host_deeptutor.py`（宿主自身 MCP 客户端路径），
   `deeptutor run chat` 只作附加、跑不通就记「未测 + 原因」，**不得为此动用云凭据**。
3. **服务端 authn 模式**：本线假设 localhost 零 key（dev/trusted）。**Lead 已裁决（2026-10-10）**：
   按无 key 走，最终模式作为**事实**写进证据；若实例要求 key 才能区分身份/范围，按 task-1 给的注入方式
   （只记变量名/头名）注入。**「没设 key」不得写成「已授权」。**
4. **写入面选择**：M6 只要求「一次允许的写入」。默认用 `write` 工具写
   `viking://resources/mvp-host-connect/<host>.md`，不用 `remember`（走会话抽取、有附带 LLM/解析成本）、
   不用 `forget`/`add_skill`。**Lead 已裁决（2026-10-10）**：以 task-1 实例 conf 的实际可写域为准，
   不一致就改路径并在证据里记差异（`--write-uri` 覆盖）。
5. **DeepTutor 隔离 home 的最小性**：只放了 `mcp.json` + `model_catalog.json` + 目录骨架。
   `probe_server`/`MCPConnectionManager` 只需 `mcp.json`；若 `deeptutor run` 还要求
   `main.yaml`/`system.json` 等，按缺失报错逐项补（**仍是隔离副本，不复制真实文件**）。
6. **同实例冲突**：本线只读 8782，不启动/不修改服务；若 task-1 需重启，本线证据必须重跑。
7. **不可用错误（M4 与本线交集）**：服务停掉时宿主侧应报明确错误（opencode `failed`；
   DeepTutor `probe_server ok=false` + `describe_connect_failure`）。这一条本线可顺带取证，
   但 M4 的主证据归 task-1。

---

## 7. 裁决状态与仍在等的事实

**已由 Lead 裁决（2026-10-10）**：
1. 宿主口径：opencode = agent 驱动（主证据）；DeepTutor = 宿主自身 MCP 客户端路径（主证据），
   `deeptutor run chat` 仅作附加；**不得动用云凭据**。两宿主差别必须写进证据（见 §3.2 ①）。
2. authn：localhost 无 key 走，身份用宿主自带头；**模式作为事实写进证据**，不把「没设 key」写成「已授权」。
3. 写入口：默认 `viking://resources/mvp-host-connect/<host>.md`，**以实例 conf 实际可写域为准**，差异入证。

**仍在等 task-1 的两个事实（解阻时 Lead 会给）**：
1. 隔离实例的实际 **authn 模式**（→ 传 `host_deeptutor.py --authn-mode`，并写进 opencode `run-meta.json`）。
2. 实例 conf 的**实际可写域**（→ 决定 `--write-uri`，若与默认不同则在证据里记差异）。
