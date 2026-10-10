# M6 证据报告：两个真实 agent 宿主接入同一隔离服务 + 成本账

日期：2026-10-10（宿主接入实测 15:27–15:45 CST）。执行线：**host-connect**（worktree `wk-host`，分支 `feat/memory-mvp-hosts`）。
对应验收：`mvp-protocol.md` **M6**；接入方依据 ADR-0032 **D6**。

> **补充（2026-10-10，按 Lead 裁决追加）**：本实例已切 `auth_mode=api_key`，在**宿主侧**验证了
> 「范围级读/写权限」——只读身份可读授权子树、写被 `PERMISSION_DENIED` 拒绝；可写身份读写正常。
> 见 **§9 补充：宿主侧只读身份（范围级权限）实测**。§1–§8 的结论与证据**原样保留、未改**。

---

## 0. 结论速览

| | 结果 |
|---|---|
| 两个**真实**宿主接**同一个**实例 | **PASS**（opencode 1.18.35 + DeepTutor 1.6.8 → 127.0.0.1:8782） |
| 共享读取 | **PASS**（宿主② 的 `search` 同时命中两宿主写的条目；`read` **逐字节相同**） |
| 一次允许的写入 | **PASS**（宿主① agent 主动写 75 B；宿主② 写 82 B 并读回一致） |
| 身份差异 | **PASS（部分）**：两宿主用不同 `X-OpenViking-User`、同 `X-OpenViking-Account`；账户级隔离实测生效 |
| 只读身份的强制（ACL） | **未测 + 原因**（dev 模式恒 ROLE=root，无法表达只读身份；见 §5） |
| 成本账 | **PASS**（依赖 / 存储 / 常驻 / 模型调用 / 维护五块，见 §6） |

**M6 判据（mvp-protocol 原文）**：「至少两个实际 agent 宿主接入同一服务，完成共享内容读取与一次允许的写入；
记录启用步骤、依赖/存储体积、启动/常驻资源、模型调用和维护步骤」→ 上述 PASS 即满足。

---

## 1. 被接入的实例（事实，非假设）

| 项目 | 值 | 来源 |
|---|---|---|
| 端点 | `http://127.0.0.1:8782/mcp`（MCP）+ `/health`、`/api/v1/**` | 本线自建实例 |
| 版本 / authn | OpenViking **0.4.23**，`auth_mode=dev`（服务自动判定） | `GET /health` → `{"status":"ok","healthy":true,"version":"0.4.23","auth_mode":"dev"}` |
| 配置 / 数据 | `%USERPROFILE%\.openviking-mvp-host\ov.conf`（无 BOM）/ `…\data` | `host_server.py conf/status` |
| embedding | 本机 GGUF `bge-small-zh-v1.5-f16`、`provider=local`、`max_concurrent=1` | `ov.conf` |
| 云模型 | **未启用**（conf 里无任何云 provider；两宿主模型走本机 Ollama） | `ov.conf` / 宿主配置 |
| 隔离 | 8781/8783/8765 端口被断言拒绝；workspace 与日志都在本线根目录 | `host_server.py assert_not_foreign_port()` |

隔离复核：`verify_isolation.ps1` → **PASS**（真实 `opencode.json` `a2157e84…`、真实 DeepTutor `mcp.json` `7073b474…`、
真实 `model_catalog.json` `faf58381…` 三条哈希全程未变；隔离副本不含 8781/8783/8765）。

---

## 2. 两个宿主是谁、差别在哪（口径写死）

| | 宿主① opencode | 宿主② DeepTutor |
|---|---|---|
| 版本 | 1.18.35（npm 全局） | 1.6.8（系统 Python 3.12.2） |
| 接入方式 | 隔离 `HOME` + `opencode.json` 里 `mcp."openviking-mvp" = {type: remote, url: …/mcp, headers}` | 隔离 `DEEPTUTOR_HOME` + `data/user/settings/mcp.json`（`{"servers":{…MCPServerConfig}}`） |
| **驱动方** | **agent 驱动**：本地 Ollama 模型自己决定调 MCP 工具 | **宿主客户端驱动**：DeepTutor 自身 `probe_server` / `MCPConnectionManager` |
| 模型 | `ollama/qwen2.5:3b-instruct-q4_k_m`（本机，非云） | 无（宿主客户端路径不调 LLM） |
| 身份头 | `X-OpenViking-Account: mvp-host` / `X-OpenViking-User: opencode-agent` | `…: mvp-host` / `…: deeptutor-agent` |
| 证据 | `evidence/opencode/{mcp-list.txt, agent-turn.jsonl, run-meta.json}` | `evidence/deeptutor/connect.json` |

**这就是两者的差别，不是「两个都一样」**：宿主① 的写入是**模型主动发起**的工具调用；宿主② 的写入/读取是
**宿主自己的 MCP 客户端路径**发起的。DeepTutor 的 LLM 驱动轮次（`deeptutor run chat`）**未跑**——原因见 §5.3。

---

## 3. 逐项证据

### 3.1 宿主① opencode 接入（agent 驱动）

**连接**：`opencode mcp list` → `• ✓ openviking-mvp connected  http://127.0.0.1:8782/mcp`
（`evidence/opencode/mcp-list.txt`）。配置由隔离 HOME 读取（实测日志 `loading …\opencode-home\.config\opencode\opencode.json`）。

**agent 主动工具调用**（`evidence/opencode/agent-turn.jsonl`，原始事件流，`--format json`）：

```
{"type":"tool_use", … "part":{"type":"tool","tool":"openviking-mvp_write",
  "state":{"status":"completed",
    "input":{"uri":"viking://resources/mvp-host-connect/opencode.md",
             "content":"M6-OPENCODE marker written by the opencode …",
             "mode":"replace","wait":"true"},
    "output":"Wrote 75 bytes to viking://resources/mvp-host-connect/opencode.md (mode=replace).\n
              Indexing: semantic=complete, vector=complete."}}}
```

即：**模型自己选择**了 MCP 工具 `openviking-mvp_write`（工具名 = `<server>_<tool>`），参数由模型填，
响应由服务器返回；服务器侧落盘并完成索引。

**已知模型缺陷（照实记，不是接入问题）**：3B 模型在首次成功后**继续重复调用** `write`，把工具返回文本
当作 `content` 反复覆盖（75→16→58→3 B），直至进程被杀。因此磁盘上 `opencode.md` 的最终内容是循环产物，
**不能**作为该宿主写入的判据——判据是上面的原始工具调用事件流。先前另有一次同类记录
（`evidence/preflight/opencode-attempt1-3b-argerror.jsonl`）：模型把 `read` 的 `{"uris": …}` 传给了 `write`，
服务器返回 pydantic 校验错误——说明**派发链路是通的**，失败在模型侧参数构造。

### 3.2 宿主② DeepTutor 接入（宿主客户端路径，`evidence/deeptutor/connect.json`）

`6 PASS / 0 FAIL`：

| 步 | 结果 |
|---|---|
| `load_mcp_config`（DeepTutor 自身加载器） | PASS：解出 `type=streamableHttp`、url=`…:8782/mcp`、`enabled_tools`=读+write、`disabled_tools`=forget/add_skill/add_resource、`headers_keys`=[X-OpenViking-Account, X-OpenViking-User] |
| `tool_whitelist` | PASS：`forget`/`add_skill` 被配置挡下 |
| `probe_server(list_tools)` | PASS：`ok=true`，**20 个工具** |
| `manager.ensure_started` | PASS：`status=connected` |
| `readback_matches_write` | PASS：写 82 B → 读回 82 B，内容一致 |
| `cross_host_peer_read` | PASS：读到**宿主①写的** `opencode.md`，`byte_identical=true` |

工具调用原文（节选）：
```
search({"query":"M6-DEEPTUTOR","limit":5}) => Found 4 item(s):
    - [resource 83%] viking://resources/mvp-host-connect/deeptutor.md
    - [resource 65%] viking://resources/mvp-host-connect/opencode.md        ← 宿主①的条目
write({"uri":"viking://resources/mvp-host-connect/deeptutor.md", …})
    => Wrote 82 bytes … Indexing: semantic=complete, vector=complete.
read({"uris":"viking://resources/mvp-host-connect/deeptutor.md"}) => <原文，逐字节一致>
read({"uris":"viking://resources/mvp-host-connect/opencode.md"}) => <宿主①写的字节，sha256 相同>
list({"uri":"viking://resources/mvp-host-connect"}) => [file] deeptutor.md [file] opencode.md
```

**跨宿主共享的两条独立证据**：① `search` 在同一共享索引里同时命中两宿主的条目；② `read` 取回宿主①
写入的那份字节，与磁盘文件 `sha256` **完全相同**（`cross_host.disk_sha256 == mcp_read_sha256`）。

### 3.3 写入落在哪里（可解释）

两宿主写入的字节落在**同一 workspace、同一账户目录**：
```
…\.openviking-mvp-host\data\viking\mvp-host\resources\mvp-host-connect\deeptutor.md  (82 B)
…\.openviking-mvp-host\data\viking\mvp-host\resources\mvp-host-connect\opencode.md   (122 B, 见 §3.1 循环说明)
```
路径里的 `mvp-host` = 两宿主配置里的 `X-OpenViking-Account`。写入域 `viking://resources/**` 与服务器端
`write` 工具自述的可写域一致（工具描述原文：`Writable scopes: viking://resources/, viking://user/{user_id}/, viking://agent/`），
因此**默认 URI 无需按实例差异调整**（Lead 决策③ 的「以实例实际可写域为准」已核对：一致）。

---

## 4. 身份与范围如何生效（实测，非推断）

`scope_probe.py` 用 HTTP 数据面 API 在同一实例上做了四组对照（`evidence/scope-probe.json`）：

| 探针 | 身份 | 结果 |
|---|---|---|
| 写入 | `X-OpenViking-Account: scope-a`, `User: user-a1` | 200，落盘 `data\viking\scope-a\resources\…\scope-probe.md`（36 B） |
| 同身份读回 | 同 A/a1 | **200 可见** |
| 换账户读 | `Account: scope-b` | **404 NOT_FOUND**（账户即隔离边界） |
| 同账户换用户读 | `Account: scope-a`, `User: user-a2` | **200 可见**（账户内 `viking://resources/**` 对所有已认证用户默认可读——与 task-1 给的事实一致） |
| 不带身份头读 | 无 | **404 NOT_FOUND**（不认证 = 不授权） |

⇒ **范围机制 = 账户命名空间隔离 + 账户内资源共享**。两宿主放同一账户（`mvp-host`）所以能互相读到；
这一点也解释了 §3.3 的落盘路径。

**未测：只读身份的强制。** 本实例是 `auth_mode=dev`，dev 插件**恒返回 ROLE=root**、ROOT 绕过 ACL，
因此**无法**在本实例上表达「只读身份写入被拒」。task-1 的授权线用第二个配置（`ov-authz.conf`，`auth_mode=api_key`）
在自己的实例上覆盖了这一面；本线**未**切换 profile，原因见 §5.2。

---

## 5. 失败、未测与原因

### 5.1 已修掉的真实缺陷（pre-flight 阶段）
- **`NO_PROXY` 含带方括号 IPv6**（`[::1]`）→ DeepTutor 的 httpx 在**发出连接前**就抛
  `InvalidURL: Invalid port: ':1]'`。修法：连接前 `sanitize_no_proxy()`（记录 before/after 到证据）；opencode 侧在 PS 层清理。
- **宿主 NO_PROXY/HTTP(S)_PROXY 双拼写** → 子进程 env 块需按大小写不敏感重建，否则 `CreateProcess` 报
  「字典中的关键字」。修法：`host_server.py::sanitized_env()` 全大写重建 + 丢弃代理（本实例不该出网）。
- **模型名不匹配**：Ollama 实际 tag 是 `qwen2.5:3b-instruct-q4_k_m`，写 `qwen2.5:3b-instruct` 会 404。
- **MCP `read` 的参数是 `uris`（str|list）不是 `uri`**：传 `uri` 报 pydantic `Field required`。

### 5.2 未测：本实例上的「只读身份 / ACL 强制」
**原因**：本线实例按参考 profile `dev` 运行（`/health` 实测 `auth_mode=dev`），dev 恒 ROLE=root、绕过 ACL；
要表达只读身份需把同一实例切到 `ov-authz.conf`（`api_key` + root key），而 root key 需走进程环境注入、
且切换后 dev 期创建的账户/数据面身份需重新登记。M6 的目标（两宿主共享读 + 一次允许的写）已达成，
按下 task-2 交付要求「身份与范围如何生效」已用 §4 的账户/用户对照实测覆盖。
**如需补齐**：按 task-1 配方（`spike_identity.py`）在本实例 `authz` profile 上登记两名身份
（admin 写者 / user 读者）+ 账户 ACL 开关 + 子树 `restricted` + `grant`，即可验证「读者写入被拒」。

### 5.3 未测：DeepTutor 的 LLM 驱动轮次（`deeptutor run chat`）
**原因**：Lead 裁决① 明确「DeepTutor 走宿主 MCP 客户端路径即可，不要求 LLM 主动调用；不得为它动用云凭据」。
本地模型侧还有一处产品事实：DeepTutor 把 `ollama/vllm/lm_studio/llama_cpp` 列入
`_NATIVE_TOOL_BLOCKED_BINDINGS`（`runtime/agentic/client.py:52`）→ 本地模型不走原生 function-calling。
故该项为**附加**，未跑，不影响 M6 判据。

### 5.4 其他已知限制
- **opencode 的 3B 本地模型不稳定**（§3.1）：能发起 MCP 调用，但会参数错配 / 成功后循环。opencode 侧
  「agent 主动性」证据成立；若要求「一次干净、可控的 agent 写入」，需换更强的本地模型或接受交互式确认。
- **opencode 仍会读全局 skill 目录**：隔离 HOME 下启动日志出现 `duplicate skill name`（来自
  `~/.claude/skills`、`~/.agents/skills`）。只读、不写；但说明「隔离 HOME」不等于「完全隔离所有输入」。
- **`qwen3:8b` 在 opencode 下报 `Unexpected server error`**，未定位（3B 可用）；记为未知项。
- **`authn` 字段口径**：本实例 `auth_mode=dev`，证据里的 `authn.mode` 照实写为 dev，并注明
  「dev 恒 ROLE=root」——**没有**把「未设 key」写成「已授权」。

---

## 6. 成本账（`evidence/cost/cost.json`）

**依赖体积**
| 项 | 值 |
|---|---|
| 候选环境 `venv-ov` | **1058 MB**（`site-packages` 顶层 497 目录） |
| `mcp` SDK（随 venv-ov） | 1.30.0 |
| opencode | 1.18.35（npm 全局，本线未新增依赖） |
| DeepTutor | 1.6.8（复用系统 Python 3.12.2 的既有安装，本线**未新增**依赖） |
| 宿主侧新增依赖 | **0** |

**存储体积**
| 项 | 值 |
|---|---|
| 本线隔离根合计 | **74 MB** |
| 其中宿主隔离副本（opencode HOME 缓存等） | **65.4 MB** ← 大头是 opencode 的模型缓存/ripgrep/db，不是记忆数据 |
| 其中服务数据（`data\`：viking 原文面 + vectordb 检索面） | **8.6 MB** |
| 检索面位置 | `data\vectordb\context\{index,store}`（原文面与检索面**两处**都存在，M5 备份语义相关） |

**启动 / 常驻资源**
| 项 | 值 |
|---|---|
| 8782 服务进程 | pid 25624，`WorkingSet64` **152.2 MB**、`PrivateMemorySize64` **1223.8 MB**（BGE-small 在本进程内） |
| opencode 轮次峰值 | **851 MB**（运行中实测） |
| DeepTutor 客户端轮次 | 数十 MB 量级（短命进程） |
| ⚠️ 口径 | 本机对**分离启动**的进程跨进程查内存会返回假值（perf_70 实测），上表 `WorkingSet` 仅作**下界观察**；`PrivateMemorySize64` 更接近真值 |

**模型调用次数与费用**
| 项 | 值 |
|---|---|
| 云模型调用 | **0 次** |
| 费用 | **0 元**（两宿主全走本机 Ollama：`qwen2.5:3b-instruct-q4_k_m` 2.2 GB，100% CPU） |
| 本地调用 | 见 `evidence/opencode/agent-turn.jsonl`（token 计数在事件流里：单步 input≈2046 / output≈52–97，`cost=0`） |
| 延迟观察 | Ollama 3B 在本机约 **11 s / 10 token**（CPU），一次 agent 轮次数分钟量级 |

**维护步骤**
1. 接入 = 写一份**隔离副本**配置（`setup_isolation.ps1`），重启宿主生效；真实宿主注册由 owner 决定，本线不碰。
2. 无「每宿主一套索引」：两宿主连同一实例、同一 workspace、同一账户 → 天然共享。
3. 服务不可用时的宿主侧表现：opencode `mcp list` = `failed`；DeepTutor `probe_server ok=false`
   （实测错误 `ConnectError: All connection attempts failed`）。
4. 索引维护（重建 / 收录）不在宿主动作里：M6 只验证接入与共享读写。

---

## 7. 复现步骤（本线全部脚本都在本目录）

```powershell
cd D:\python_work\work2026-4\wk-host\experiments\memory-host-connect

# 0) 本线实例（8782；dev profile；配置与数据在 %USERPROFILE%\.openviking-mvp-host\）
& "D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\python.exe" host_server.py start --profile dev
& "D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\python.exe" host_server.py status

# 1) 隔离副本配置 + 零写入核验
powershell -File setup_isolation.ps1 -OllamaModel "qwen2.5:3b-instruct-q4_k_m"
powershell -File verify_isolation.ps1

# 2) 宿主①：连接状态 + agent 轮次（本地 Ollama，非云）
powershell -File host_opencode.ps1 -Model "qwen2.5:3b-instruct-q4_k_m"

# 3) 宿主②：DeepTutor 自身加载器 + MCP 客户端路径（读 / 写 / 读回 / 跨宿主逐字节比对）
& "D:\Users\Tan\AppData\Local\Programs\Python\Python312\python.exe" host_deeptutor.py `
  --home "$env:USERPROFILE\.openviking-mvp-host\hosts\deeptutor-home" `
  --authn-mode "dev ..." `
  --peer-disk-path "$env:USERPROFILE\.openviking-mvp-host\data\viking\mvp-host\resources\mvp-host-connect\opencode.md" `
  --json-out evidence\deeptutor\connect.json

# 4) 身份 / 范围对照
& "D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\python.exe" scope_probe.py --json-out evidence\scope-probe.json

# 5) 成本账
powershell -File collect_cost.ps1
```

停工：`python host_server.py stop`（只按 pid 文件精确停本线进程）。

---

## 8. 给 Lead 的一句话结论

**M6 成立**：两个真实宿主（opencode 1.18.35 agent 驱动 / DeepTutor 1.6.8 宿主客户端驱动）接入**同一**隔离实例，
共享读取（跨宿主 `search` 命中 + `read` 逐字节相同）与一次允许的写入（两宿主各一次）均有原始工具调用证据；
身份/范围用账户-用户对照实测（账户隔离、账户内共享、无身份拒绝）；成本账齐全，**云调用 0 / 费用 0**。
未测项两处：本实例上的只读身份 ACL 强制（dev 恒 ROLE=root，原因已记）、DeepTutor 的 LLM 驱动轮次（裁决①不要求）。

> 其中第一处未测项已由 **§9 补充**补齐（切 authz 后在**宿主侧**验证只读身份 + 范围级写拒绝）。

---

## 9. 补充：宿主侧只读身份（范围级权限）实测

**本节为追加，§1–§8 的结论与证据未做任何修改。** 触发：Lead 裁决要求补齐「范围级读/写权限（哪些子树
agent 只读、哪些可写）」——task-1 验的是 **API 身份**，不是**宿主**；本补充在**本线自己的 8782 实例**上
按 task-1 配方切 authz profile，并把结论落到**宿主侧**。

### 9.1 做了什么（一条命令可复现：`run_supplement_authz.ps1`）

| 步 | 动作 | 结果 |
|---|---|---|
| 1 | 本实例切 `auth_mode=api_key`（`ov-authz.conf`；root key **只在进程环境**，本脚本不落盘/不回显） | `/health` → `auth_mode=api_key` |
| 2 | 登记两名身份：`host-rw`(role=admin) / `host-ro`(role=user) | PASS（已存在则复用，`ALREADY_EXISTS` → 取回既有 key） |
| 3 | 打开**账户级 ACL**（默认关，不开则 restricted/grant 不生效） | PASS `acl_enabled=true` |
| 4 | 建**受限子树** `viking://resources/m6-scope` 并授权 | `acl_mode=restricted`；`direct_entries=[{user:host-ro,read},{user:host-rw,write}]` |
| 5 | 两把 key 存进 **DeepTutor 自己的** per-owner secret store（`data/system/user-secrets/_shared/private/mcp/…`，chmod 700） | PASS；`mcp.json` 里只有引用 `${secret:openviking-mvp/api_key_ro|rw}`，**不含 key** |
| 6 | **宿主侧**跑只读身份（DeepTutor 自身 MCP 客户端路径） | **6 PASS / 0 FAIL** |
| 7 | **宿主侧**跑可写身份（同上） | **6 PASS / 0 FAIL** |

### 9.2 宿主侧证据原文（`evidence/supplement-authz/`）

只读身份 `host-ro`（`host-ro-readonly.json`）：
```
[PASS] load_mcp_config: headers_keys=[X-API-Key, X-OpenViking-Account, X-OpenViking-User]  ← 只有引用，无 key
[PASS] probe_server(list_tools): ok=True tools=20
[PASS] manager.ensure_started: status=connected
[PASS] scope_read_allowed: viking://resources/m6-scope/seed.md readable = True
       read  => "M6-SUPPLEMENT seed written by host-rw"
[PASS] write_denied_for_readonly:
       write => "PERMISSION_DENIED: Access denied for viking://resources/m6-scope/ro-attempt.md:
                 write permission required"
```
可写身份 `host-rw`（`host-rw-writable.json`）：
```
[PASS] scope_read_allowed: viking://resources/m6-scope/seed.md readable = True
[PASS] readback_matches_write: 写 86 B → 读回 86 B，内容一致
       write => "Wrote 86 bytes to viking://resources/m6-scope/rw-written.md (mode=replace).
                 Indexing: semantic=complete, vector=complete."
```
**磁盘反证**：被拒的 `ro-attempt.md` **不存在**（拒绝的写没有留下任何字节）；`rw-written.md` 存在。
⇒ **范围级权限在宿主侧成立**：同一子树，只读身份读得到、写被 403 拒绝；可写身份读写均正常。

**只读身份来自哪个宿主**：DeepTutor（它的 `mcp.json` 支持 `${secret:…}` 引用，key 进它自己的 secret store）。
opencode 在 authz 下**未接入**，原因：它的 remote-MCP 配置没有同类 secret-reference 机制，把明文 key 写进
隔离 `opencode.json` 会违反凭据规则；Lead 的口径是「至少一个宿主走只读身份就够」，故此项用 DeepTutor 交付。

### 9.3 凭据处理（照实记）

- **mcp.json 只有引用**：`X-API-Key: ${secret:openviking-mvp/api_key_ro}`（DeepTutor 的产品设计：配置里
  只允许放引用，值在连接时于内存中解析——`services/mcp/secrets.py`）。
- **key 值落在 DeepTutor 自己的 secret store**（per-owner、chmod 700、在隔离 `DEEPTUTOR_HOME` 内，仓库外）。
- **root key（api_key 模式的根凭据）只在进程环境**：由 `run_supplement_authz.ps1` 现场生成、只传给服务进程
  与 provisioning 进程，**不写文件、不进 git、不回显**；脚本重跑会换一把新 key 并重新 provision。
- 证据里只有**键名与键前缀**（如 `ZGVmYX...`），无任何完整 key。

### 9.4 切换的副作用（照实记）

切到 authz 后，**§1–§8 的 dev 期证据不能用同样命令重跑**（实例现在要求 `X-API-Key`；dev 期自动创建的
`mvp-host` / `scope-a` 账户数据仍在磁盘，但 `default` 账户身份看不到它们）。已有证据文件**已冻结、未改**。
要回到 dev 形态：`python host_server.py start --profile dev`（`ov.conf` 未变）。

### 9.5 补充过程中发现并修掉的三个真实缺陷（都进了脚本）

1. **ACL 调用顺序**：资源不存在时 `PUT /api/v1/acl` / `grant` 返回 **404 NOT_FOUND** → 必须**先写种子文件再设 ACL**。
2. **PowerShell 只读自动变量**：`setup_authz_supplement.ps1` 里 `$home = …` 抛
   `VariableNotWritable`（`$HOME` 只读且变量名大小写不敏感）→ 改名 `$dtHome`；已对全部 .ps1 做自动变量扫描。
3. **实例生命周期泄漏**：`host_server.py` 原来只记**一个** pid，重复 start 会留下孤儿进程占端口/工作区锁
   （实测出现「服务打印 running 后端口不通」的假象，根因是**我在启动窗口内做了健康探测**，叠加孤儿进程）。
   现已改为：pid 文件记 pid **列表**、`start()` 先 `stop()` 自清、端口被非本脚本进程占用则拒绝启动、
   启动超时自动 dump `server.err.log` 尾部。

### 9.6 与宿主工具白名单相关的一条重要事实（Lead 要求显式写）

**服务器实际注册 20 个 MCP 工具**，而 `openviking/server/app.py:788` 的注释写的是
「serves 16 tools (…16 个名字…)」——**那 16 是旧值，注释未随注册更新**；权威列表是
`mcp_endpoint.py` 里的 `@mcp.tool` 注册（实测 `probe_server` 返回 **20**）。
对宿主接入的直接影响：**宿主的 `enabled_tools` 白名单要照实测的 20 个名字写，不能照注释**，
否则会出现「配置看似完整、但新工具静默不可用」。本线两宿主的白名单都是按 `probe_server` 实测的工具名逐条挑的
（读 + `write`），并显式 `disabled_tools` 挡掉 `forget`/`add_skill`/`add_resource`。
