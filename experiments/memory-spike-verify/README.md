# 独立复核：个人记忆基座 MVP（阶段 1–3 关键结论）

对应共享任务 **task-3**（被 task-1 阻塞）。工作树 `wk-verify`，分支 `feat/memory-mvp-verify`。
写域：**仅本目录**；隔离实例数据在仓库外 `%USERPROFILE%\.openviking-mvp-verify\`。

## 目的与纪律

- 复核执行方（task-1 / `wk-spike`）在 MVP 阶段 1–3 的关键结论：**不复用其脚本、不接受其结论**。
- **预注册**：本目录的判定标准与检查脚本在执行方产出任何结果**之前**写定并提交；
  先跑自己的隔离实例得到自己的观测，**之后**才读执行方证据做逐条比对。
- 判定标准是唯一裁决依据：`verify_criteria.py` 里逐 probe 写明 `pass_rule` /
  `fail_rule` / `inconclusive_rule`；`aggregate()` 机械汇总（全 PASS 才 PASS；
  任一 FAIL 即 FAIL；无 FAIL 但有 INCONCLUSIVE 则 INCONCLUSIVE）。
- 判定标准指纹 `criteria_sha256()` 必须与实跑结果里记录的一致（证明"跑前定死、跑后没改"）。

## 五条检查（对应 task-3 必复核最小集合）

| 检查 | 内容 | 预注册要点 |
|---|---|---|
| C1 | 原文往返 | raw 读回 == 输入（C1a）；**磁盘/git 对象**字节 == 输入（C1b）；默认读回接口也不得改写（C1c）；重启后仍可读（C1d） |
| C2 | 形态（指过去 / 拷一份） | 外部改源文件后 **120 s** 内是否读到新正文（C2a）；workspace 内是否出现独立正文副本（C2b）；同源重复接入是否新增条目（C2c） |
| C3 | 重复提交 | 同 URI 同正文 replace 两次仍 1 条（C3a）；create 两次幂等或显式冲突（C3b）；同源 + 同 to 重复不新增（C3c）；公开接口是否有幂等键（C3d） |
| C4 | 来源只读 | 接入前后源目录清单 + 逐文件 sha256 + git HEAD/status 一致（C4a）；基座写来源是否被拒（C4b）；HTTP 本地路径来源被显式拒绝（C4c） |
| C5 | 停机报错 | 在线对照成功（C5a）；停机后读取抛显式错误而非空结果（C5b）；停机后检索同样显式报错（C5c）；三类错误可区分（C5d） |

**不变式**：判定标准在跑前冻结；全部结论附可复算哈希（`criteria_sha256` / `payload_sha256` /
正文 sha256 / 源目录 listing sha256）。

## 文件

| 文件 | 作用 |
|---|---|
| `verify_criteria.py` | **预注册判定标准**（冻结常量、五条检查逐 probe 规则、机械汇总） |
| `verify_env.py` | 隔离面：绝对路径、写域守卫 `assert_writable()`、无 BOM 配置、启停（按精确 PID + 端口所有权） |
| `verify_probe.py` | 观测原语：官方 SDK 消费者、磁盘/git 字节核对、源目录快照、测试内容生成 |
| `verify_checks.py` | 静态自检 + 仪器预检 + 实跑实现 + Markdown 渲染 |
| `run_verify.py` | 驱动器（`--stage static` / `--stage preflight` / `--stage live`） |
| `evidence/` | 落盘证据（静态自检、仪器预检、实跑 JSON、报告） |

## 运行

```powershell
$py = 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\python.exe'
$d  = 'D:\python_work\work2026-4\wk-verify\experiments\memory-spike-verify'

# 1) 静态自检（不联网、不启服务）：判定标准完整 + 写域不外溢 + 框架源码字节锚定
& $py "$d\run_verify.py" --stage static

# 2) 仪器预检（不启服务）：证明扫描/比对工具能测出阳性与阴性（正控 + 负控）
& $py "$d\run_verify.py" --stage preflight

# 3) 隔离实例实跑（task-1 完成后）
& $py "$d\run_verify.py" --stage live
```

> 顺序：`static` → `preflight` → `live`。**预检必须在实跑之前**：它证明"能测出改写、
> 能测出找不到、git blob 逐字节相等可判、探针清理干净"，否则实跑报 PASS 没有意义。

## 隔离配方（本线专用，绝不与另两条线共用）

- 可执行文件：主树绝对路径 `venv-ov\Scripts\{python.exe,openviking-server.exe,ov.exe}`。
- 启动：`openviking-server.exe --config <ov.conf> --host 127.0.0.1 --port 8783`。
- 配置与数据：`%USERPROFILE%\.openviking-mvp-verify\`（`ov.conf` / `ovcli.conf` / `data/` / `logs/`）；
  经 `OPENVIKING_CONFIG_FILE` / `OPENVIKING_CLI_CONFIG_FILE` 指定；conf **无 BOM**。
- embedding：`provider=local`，用本机已有缓存 `bge-small-zh-v1.5-f16.gguf`；**配置里没有 vlm / query_planner 段 ⇒ 不启用云模型**。
- 停机：只按登记的 PID 且校验端口所有者一致后 `taskkill /PID <pid> /T /F`。
- 永不触碰：`~/.config/opencode/knowledge`、`~/.config/opencode/opencode.json`、`~/.openviking`（已有 conf/data）、
  `memory_agent/vector_db`、8765 旧 daemon、另两条线的端口 8781/8782 与它们的 worktree。

## 复核实跑留下的"框架源码静态证据"

`--stage static` 会以 **ast 解析**（不 import、不触发任何初始化）从已安装框架源码取出：

- `WriteContentRequest` / `AddResourceRequest` 字段集与 `extra="forbid"` → 公开写入接口是否提供幂等键；
- `local_input_guard.py` 的本地路径拒绝谓词；
- SDK `add_resource` 对本地目录 `_zip_directory` + `temp_file_id`（=上传快照，不是指针）；
- upload + `watch_interval>0` 被显式拒绝；
- `UnavailableError` 类存在。

同时记录被解析源码文件的 sha256 —— 实跑阶段的预期因此锚定在**具体字节**上，可复算。

## 已记录的指纹（写定后不改）

| 对象 | 值 |
|---|---|
| `criteria_sha256`（判定标准） | `17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d` |
| 静态自检载荷 | 见 `evidence/static_selfcheck.json` 的 `payload_sha256` |
| 仪器预检载荷 | 见 `evidence/preflight_instruments.json` 的 `payload_sha256` |
| 候选版本 | `openviking 0.4.23` / `openviking-sdk 0.1.13`（`ov` 二进制版本另记，见执行方） |

> 实跑结果里会同时写入 `criteria_sha256` 与 `harness_sha256`（五个脚本的字节哈希）：
> 若与本节不一致，说明判定标准或脚本在实跑后被改过，结论作废。

## 与执行方证据的比对口径（在跑完自己的实例之后才执行）

| 判词 | 定义 |
|---|---|
| **一致** | 执行方结论与我方独立观测落在同一命题上，方向相同、关键量值相同（允许措辞/单位差异） |
| **不一致** | 同一命题方向相反，或关键机制/量值不同（必须附**双方**原始证据） |
| **无法复核** | 我方隔离实例、身份或能力无法构造该命题的观测（写明具体原因，不得折算成"一致"） |

另单列 **"我方发现的执行方可能误报点"**：结论未必错，但其表述/证据不足以支撑该结论，
或只在某个身份/路径下成立却被写成普适结论。

### 已发生的复核工具自身缺陷（留痕，供审阅）

第一轮实跑（`evidence/live_results_run1_harness_bug.json`）的判定**不可用**，原因是我方工具缺陷，已在第二轮修正：

1. `Path.write_text()` 在 Windows 上把 `\n` 翻成 `\r\n` → 我的"LF 期望值"与"磁盘/导入后的 CRLF 实际值"永不相等，导致 **C2a 无法检出追平、C2b 假阴性**。修正：源文件按原始字节写入 + 全部比对锚到**磁盘真实字节**。
2. pip 生成的 `openviking-server.exe` 是 launcher，会再拉起子进程（实测两者 PID 不同：launcher 35896 / 监听 27220）→ 我按"PID 必须相等"的所有权校验把**自己的实例**拒停，C5/C1d 因此无法裁决。修正：改为按**监听进程命令行**做所有权校验，并同时停 launcher 与监听进程。
3. `add_resource(parent=...)` 要求 parent 已存在，两次接入都失败却因 `counts [0,0]` 相等被判**空洞 PASS**。修正：要求至少一次接入成功且 ls 成功，否则记 INCONCLUSIVE。
4. C4b 用了"接入前"的快照作基线，把**我自己**在 C2a 改动源文件造成的 diff 误判成"基座写坏了来源"。修正：基线改为写请求前的即时快照。
5. C5d 实现比冻结规则更宽（`unavailable` 缺失时仍能 PASS）。修正：严格按规则要求两档同时存在且类型不同。

第一轮与第二轮的结果都留在 `evidence/` 里；**只有第二轮（修正后）的结果参与结论**。
