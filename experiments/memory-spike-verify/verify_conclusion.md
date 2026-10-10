# 独立复核结论：MVP 阶段 1–3（task-3）

复核方：spike-verifier · 工作树 `D:\python_work\work2026-4\wk-verify` · 分支 `feat/memory-mvp-verify`
被复核方：spike-runner · `D:\python_work\work2026-4\wk-spike` · 分支 `feat/memory-mvp-spike`（commits `4b1e83f/279c2fc/cc3bf0c/89df301/625a510/7cb7209`）

**方法与顺序（预注册纪律）**：判定标准与脚本先写定并提交（`criteria_sha256 = 17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`，commit `d3c0224`），
再在**本线自己的隔离实例**（端口 8783，数据 `%USERPROFILE%\.openviking-mvp-verify\`）上跑出第一手观测，
**之后**才读执行方证据。执行方脚本一行未复用。

## 0. 一句话结论

执行方在阶段 1–3 的**事实性结论我全部独立复现（一致）**；差异只在**判词口径**：我按预注册规则把
M2 的形态判为 **FAIL（拷贝、无自动追平）**，执行方按协议的"快照 + 已登记更新机制"读法判 **PASS**——
**事实完全一致，验收读法不同**。另发现 4 处**表述/口径需要收窄**的地方（见 §3），
以及 1 处它未覆盖的重复提交面（add-resource 会生成兄弟 `_1`，我独立复现）。

## 1. 我方独立结果（第一手，均可复算）

实跑：run `20261010T153910-e48bc7` · 载荷指纹 `5730a1269e1bf409e25315aae0c9fe5ffc38f1376f70a800869f5760093db38c`
（同判定标准下连跑两次结果逐项相同；第一轮因工具缺陷作废，见 §5）

| 检查 | 判定 | 关键观测 |
|---|---|---|
| C1 原文往返 | **PASS** | 519 B 输入：`read_raw` sha `7889b2ad…`＝输入；默认 `read` 同 sha；**磁盘**上 `data\viking\default\user\default\verify\…\roundtrip.md` 519 B 逐字节相同（扫 143 个文件）；重启后同 sha |
| C2 形态 | **FAIL** | 外部改源文件后 **120 s 内 13 次采样**读回 sha 恒为 `5b687743…`（＝改动前源字节）；workspace 内 `…\resources\verify-…\src\a.md` 78 B 与源文件**逐字节相同**（第二份正文）；重复导入生成兄弟 `source-tree_1` |
| C3 重复提交 | **FAIL** | replace 两次→1 条；create 两次→第二次 `AlreadyExistsError`、仍 1 条；同源+同 `to`→1 条；**`content/write` 带 `request_id` → 400 `INVALID_ARGUMENT`/`extra_forbidden`（公开接口无幂等键）** ⇒ 该项按预注册规则 FAIL |
| C4 来源只读 | **INCONCLUSIVE** | 接入前后源目录清单 sha `0d574d5a…` 相同、git HEAD 相同、逐文件 sha 相同（C4a PASS）；HTTP 本地路径来源 → **403 `PERMISSION_DENIED`**（C4c PASS）；**C4b**：dev/ROOT 下基座只持副本，写请求被接受且真实源目录不变 ⇒ "写来源被拒"物理上不存在，不得记 PASS |
| C5 停机报错 | **PASS** | 停机后 `read` 与 `find` 均抛 `httpx.ConnectError: All connection attempts failed`（**未**返回空/None）；`NotFoundError` 与网络错误两类可区分 |

附加观测（非预注册 probe，仅描述性）：`GET /content/download` → 200 / 519 B / 与输入逐字节相同；
`ov read` 默认输出 = 输入 + **1 个尾随换行**；`ov read <uri> -o json` 的 `result` 字段逐字节相同（见 §3.1）。

### 1.1 授权面（补执行方 M2.6/M3 的核心声明；我方另起 api_key 线，全新账户）

`evidence/acl_independent.json` · 载荷指纹 `0e12b1c2bc20a04e81378032af571299461ed9256c429d7b7f261eb5d85b1d4b`

| 声明 | 判定 | 关键观测（账户 `mvpverifyb0adaa`） |
|---|---|---|
| ACL-a 账户级 ACL 默认关闭 | **PASS** | `GET …/settings` → `{"acl":{"enabled":false}}`；静态佐证 `account_config.py::AccountAclSettings.enabled = RuntimeField(default=False)`（sha256 `cfa2cd2c…`） |
| ACL-b 不打开时只读身份也能写 | **PASS** | restricted + `user:vro` (read) 授权；只读身份 `read` OK 且 **`write` 成功**（`content_updated=true`） |
| ACL-c 打开后同一操作 403 | **PASS** | `PATCH …/settings {"acl":{"enabled":true}}` → 200；同一身份同一操作 → `PermissionDeniedError: Access denied for …/doc.md: write permission required`；**对照**：其 `read` 仍 OK、admin 仍可写 |
| ACL-d 范围外不得泄露 | **PASS** | 未授权 restricted 子树：`read` 与 `ls` 均 `PermissionDeniedError: read permission required` |
| ACL-e 非 ROOT 身份写受限范围被拒 | **PASS** | 同上机制（补 C4b 缺的非 ROOT 身份证据） |

## 2. 逐条比对：执行方声明 vs 我方观测

判词口径：**一致**＝同向且关键量值相同；**不一致**＝方向/机制不同；**无法复核**＝我方无法构造该观测。

| # | 执行方声明 | 判词 | 双方证据 |
|---|---|---|---|
| 1 | M1：555 B 输入在**磁盘/`read?raw`/`download`/独立 `ov read`** 四处逐字节相同，sha `2d5f9794…43e1`；重启后不变 | **一致**（附口径收窄） | 我**独立复算其 payload 哈希**：`mvp/payload/m1-app-submit.md` = 555 B、sha `2d5f97940eca9f3c4261a2a7cc4daec48d26b5c5b0cdc5e7d97cb0badc3143e1`，**与其声称完全吻合**；其 `m1_roundtrip.json` 五处 sha 自洽。我方 C1a/C1c/C1d PASS + download 逐字节相同 + `ov read -o json` 的 `result` 逐字节相同 |
| 2 | M2 形态＝**拷一份**（本地路径 watch 被显式拒绝；重复导入生成兄弟 `_1`；来源清单+git 提交号前后一致） | **一致** | 我方 C2a FAIL（120 s 无追平）、C2b FAIL（workspace 内出现逐字节相同的第二份正文）、C2c FAIL（`source-tree_1`）、C4a PASS（源目录清单+git 前后一致）、C4c PASS（本地路径 403）。执行方 `m2_source.md` 的框架原文（"A local path cannot be watched…"）与我方 C4c 的 403 报文同源 |
| 3 | M2 整体判 **PASS**（快照形态 + 已登记更新机制/延迟/成本） | **不一致（判词口径，非事实）** | 事实层与第 2 条相同；分歧只在读法：我方预注册 C2 的 pass_rule 要求"登记时限内读到新正文"，实测**未**追平 ⇒ FAIL；执行方按协议允许的"导入快照 + 显式更新机制"读法判 PASS。**建议 Lead 在 M2 结论处显式写明该读法前提**（M2 原文既写"追平更新"又允许"导入快照"，本身有歧义） |
| 4 | M1 重复提交：`create` 409 `ALREADY_EXISTS`、`replace` 就地更新、条目数恒 1；服务端无幂等键 | **一致（仅限 content/write）** | 我方 C3a/C3b/C3c PASS（第二次 create → `AlreadyExistsError`，条目数 1）；C3d FAIL（`request_id` 被 `extra=forbid` 拒绝；框架字段集无幂等键，附源码 sha256）。**但**该声明不覆盖 add-resource 路径：我独立复现兄弟 `_1`（C2c） |
| 5 | M2.6 通过基座写"该来源"被拒（只读身份） | **无法复核（原样）/ 机制层一致** | 我方 C4b INCONCLUSIVE（dev/ROOT，基座只持副本，写被接受且源目录不变）；我方 ACL-e PASS 用**非 ROOT 身份**证实"受限范围写入被拒"这一机制。对象不同（其目标＝导入来源树；我方＝自建受限资源） |
| 6 | M3：账户级 ACL 默认关闭；不打开只读身份也能写；打开后 403（开关 `acl.enabled`） | **一致（独立复现）** | 见 §1.1 全部 PASS；其 `m3_authz.json` M3.0b 的 `acl_off → 200/written_bytes 49`、`acl_on → 403 PERMISSION_DENIED`（报文"write permission required"）与我方报文**同文** |
| 7 | M4：停机后显式错误 | **一致** | 我方 C5b/C5c PASS（`ConnectError`，无静默空结果）；其 M4.2 记"无 HTTP 状态码 / CLI 非 0" |
| 8 | M4.3 三类可区分（无匹配 / 拒绝访问 / 服务不可用） | **一致（第三档口径不同）** | 我方 C5d PASS：`NotFoundError`（读取不存在条目，404）与网络错误两类不同；"拒绝访问"由 ACL 线取得 `PermissionDeniedError`。执行方把"无匹配"定义为 **HTTP 200 + 空结果**（M4.1），我用的是 404 —— 两种都能支持"可区分"，但不是同一对照档 |
| 9 | M4.6 `semantic_status=complete` 但目录 overview 是 `[Directory overview is not ready]` 占位 ⇒ 派生状态字段不可信 | **无法复核（未独立测）** | 我方隔离配置 `auto_generate_l0/l1=false` + content-only 向量化，未产生目录 overview 占位，既未证实也未证伪（不在我方最小集合） |
| 10 | M5.4 clean-room 恢复 FAIL（本地 GGUF 嵌入并发 → 原生 `GGML_ASSERT` 整进程退出，重启 1 次） | **无法复核（未独立复现）** | 我的实跑**全程无 abort**，但只做了串行读写与少量 add-resource，**未**做 restore/import 批量嵌入 ⇒ **不构成反证**。该缺陷方向保守（判 FAIL），Lead 若要复用需按其脚本复跑或自行复现 |
| 11 | 版本口径：包 `0.4.23`、`ov` 自报 `0.4.24.dev0` | **一致** | 我方 `importlib.metadata` → openviking 0.4.23 / openviking-sdk 0.1.13；`ov --version` → `openviking 0.4.24.dev0` |
| 12 | 环境坑：conf 无 BOM、`NO_PROXY` 带方括号 IPv6 致 `Invalid port` | **一致（又独立撞上）** | 我的 api_key 线首次运行正是被 `httpx.InvalidURL: Invalid port: ':1]'` 打断（`NO_PROXY` 含 `[::1]`）；隔离 conf 一律无 BOM |

## 3. 我方发现的执行方可能误报点（措辞/口径，未必是错的结论）

1. **"独立进程 `ov read` 逐字节相同"缺输出模式限定。** 其命令是 `ov read <uri> -o json`（见 `m1_roundtrip.json` M1.5）。
   我实测：`-o json` 的 `result` 字段逐字节相同（531 B，sha `da430719…`）；而**默认人读输出多一个尾随换行**（532 B vs 531 B，sha 不同）。
   若把这句写成"`ov read` 逐字节相同"而不注明 `-o json`，会被读成包含默认输出模式。
2. **"同一请求重复提交不得产生第二条"只在 `content/write` 路径成立。** 我独立复现 add-resource 重复导入生成兄弟 `source-tree_1`（条目数 3→4）。
   执行方的重复提交结论与该现象分列两节（形态节），并列时容易被读成"重复提交全局没问题"。建议在结论句里加路径限定。
3. **M2 判 PASS 依赖特定读法。** 同一批事实（无自动追平、存在独立副本、兄弟 `_1`）按"自动追平"读即 FAIL。
   M2 原文同时写了"追平更新"与"若实现提供导入快照…"，建议把 PASS 的读法前提写在结论行内。
4. **M5.4 崩溃与其它 PASS 项的时间线未被记录。** `m5_backup.md` 只记"重启次数 = 1"。
   若某些 PASS 项在崩溃之后采样，需要确认它们不是在同一被污染进程里测到；建议补一条时间线（否则 M5.1/5.2/5.3 的可信度无法从证据本身判断）。
5. （执行方**未**出现的偏乐观声明）我方特意追了它四处"逐字节"口径，未发现把"解析结果"冒充"原文"的情况——C1b 的磁盘级证据在我方独立取得，与其一致。

## 4. 我无法复核的项（明确列出，不折算成"一致"）

| 项 | 原因 |
|---|---|
| M2.6「写该来源被拒」的原样对象 | 本线 dev/ROOT 身份下基座只持导入副本，物理上不存在"写来源"动作；机制层用 ACL-e 补了 |
| M3.5 检索越界不泄露（find/grep 越界） | 我只做了直接 `read`/`ls` 越界的 403（ACL-d）；未测 find/grep 的越界泄露 |
| M4.6 派生状态不可信 | 我的配置不产生目录 overview 占位，未独立测 |
| M5.1–M5.6 备份/快照/干净位置恢复 | 不在我方最小集合，未独立重跑；M5.4 的崩溃无法复核 |
| M0 部署细节 | 我用自建隔离配置起服务并实测就绪（`/health`、`/ready` 均 200），未逐条核对它的 m0 证据 |
| M3 的 401/403/404 全套错误矩阵 | 我取得 404 与 403 两档；401（无/坏凭据）未独立构造 |
| M6（opencode / DeepTutor / 手机端） | 非本轮、非本线 |

## 5. 复核工具自身的缺陷与修正（诚实留痕；第一轮判定作废）

第一轮 `evidence/live_results_run1_harness_bug.json` 的判定**不可用**，已保留作对照。修正过程：

1. `Path.write_text()` 在 Windows 把 `\n` 翻成 `\r\n` → 我的"LF 期望值"与磁盘/导入后的 CRLF 实际值永不相等 ⇒ **C2a 检不出追平、C2b 假阴性**。改：源文件按原始字节写 + 全部比对锚到**磁盘真实字节**。
2. pip 的 `openviking-server.exe` 是 launcher，会再拉起子进程（实测 launcher 35896 / 监听 27220）⇒ 我按"PID 必须相等"的所有权校验把**自己的实例**拒停，C5/C1d 无法裁决。改：按**监听进程命令行**校验所有权，并同时停 launcher 与监听进程。
3. `add_resource(parent=…)` 要求 parent 已存在，两次接入都失败却因 `counts [0,0]` 相等被判**空洞 PASS**。改：要求至少一次接入成功且 ls 成功，否则 INCONCLUSIVE。
4. C4b 用"接入前"快照作基线，把**我自己**在 C2a 改动源文件造成的 diff 误判成"基座写坏了来源"。改：基线＝写请求前即时快照。
5. C5d 实现比冻结规则更宽（`unavailable` 缺失时仍能 PASS）。改：严格按规则要求两档同时存在且类型不同。
6. **证据脱敏函数被用来取凭据**：`_call()` 把 `user_key` 脱敏成 `"<redacted>"`，我又拿它当 API key 用 ⇒ 一律 `Invalid API Key`。改：`_call_raw()` 取原值、`_redact()` 只用于写证据。
7. **账户复用污染默认值测量**：ACL 开关是账户级持久设置，复用账户使第二次运行读到上一次留下的 `acl.enabled=true`，"默认关闭"根本测不到。改：每次运行用**全新账户**。
8. 新阶段漏了进程环境钉定：`openviking_sdk` 会去读**真实** `~/.openviking/ovcli.conf`（本机实测该文件解析失败 → `ValueError: Invalid CLI config … Expecting value`）。改：抽出 `pin_process_env()`，任何 SDK 客户端创建**之前**钉定 `OPENVIKING_CONFIG_FILE` / `OPENVIKING_CLI_CONFIG_FILE` 并净化 `NO_PROXY`（三条线统一使用）。

> 判定标准 `criteria_sha256` 全程未变（`17d18b7b…`）；以上只改驱动器与观测口径。

## 6. 交付物与可复算指纹

| 文件 | 内容 | 指纹 |
|---|---|---|
| `verify_criteria.py` | **预注册判定标准**（五条检查逐 probe 规则 + 机械汇总） | `criteria_sha256 = 17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d` |
| `evidence/live_results.json` | 我方五条检查第一手结果（run `20261010T153910-e48bc7`） | `payload_sha256 = 5730a1269e1bf409e25315aae0c9fe5ffc38f1376f70a800869f5760093db38c` |
| `evidence/acl_independent.json` | 授权面独立复核（ACL-a…e 全 PASS） | `payload_sha256 = 0e12b1c2bc20a04e81378032af571299461ed9256c429d7b7f261eb5d85b1d4b` |
| `evidence/cli_read_modes.json` | `ov read` 两种输出模式的字节口径 | `payload_sha256 = 8c09ebf8f5b9dbb9517974a33737991c2187a0f96bd3e214bbd5d55bcaf046d3` |
| `evidence/static_selfcheck.json` | 静态自检（判定标准完整 + 写域守卫 + 框架源码锚定） | `payload_sha256` 见文件 |
| `evidence/preflight_instruments.json` | 仪器预检（正控/负控，证明能测出阳性与阴性） | `payload_sha256` 见文件 |
| `evidence/live_results_run1_harness_bug.json` | 第一轮（工具缺陷）结果，仅作对照 | — |

隔离纪律：数据与配置全在 `%USERPROFILE%\.openviking-mvp-verify\`（仓库外）；端口 8783；
未触碰真实 KB、真实索引、8765 旧 daemon、`~/.openviking` 已有 conf/data、另两条线（8781/8782）；未启用云模型（conf 无 `vlm`/`query_planner` 段）；
root/用户凭据随运行随机生成，只写仓外隔离 conf，证据中一律脱敏。
