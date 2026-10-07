# #70 并发与可靠性：一组可复现的数字（p50/p95 · RSS · 长任务阻塞）

- 日期：2026-10-07 17:37:28（本地时区；整轮 485.1s）
- 宿主：nt / win32 · 16 逻辑核 (8 物理) · 内存 27.7 GiB
- 仓库：`D:\python_work\work2026-4\wk-70-perf` (branch `feat/70-perf`, HEAD `6e3181c4a5`) · Python 3.12.2
- 脚本：`memory_agent/eval/perf_70.py`（**原始逐请求数字**在 `perf_70_results.json`）
- 命令：`python.exe --requests 12 --longtask-seconds 8`

## 0. 机制与口径

拓扑（ADR-0013）：**一个常驻 daemon（HTTP `/mcp`）+ 每会话一个 stdio 代理**；daemon 里那份 BGE-M3 只付一次。进程内并发语义（只读这些代码，未改动）：

- `MemoryIndex.search` / `refresh` / `rebuild` / `reindex` 全在**同一把 `INDEX_LOCK`（RLock）**下 → **N 个并发读者在服务端串行**（不是并行）；
- 写入 `MemoryWriter.add` 在 `WRITE_LOCK` 下（去重检索再取 `INDEX_LOCK`）→ **单写者串行**；加锁顺序恒为 WRITE → INDEX → store session；
- Qdrant **local mode 同进程也不能并发开 client**，`VectorStoreService._session` 再串一道 `_SESSION_LOCK`（第 3 道串行化）；
- 每次 `search` 还包含**语料指纹扫描**（`_maybe_refresh` 的 stat）与 **自洽核对**（`store.count()`），都在这把锁里。

所以本页回答的不是「有没有并行」，而是**串行化下 p50/p95 怎么长、内存怎么摊**。

口径：**R/W/L 用 Stub 嵌入**（无 BGE-M3、无网络、无 CPU 争用，确定性）；L 的慢档用「每篇 20.0 ms 人工延时」放大嵌入临界区，是**机制演示**，不是真模型绝对耗时。**M 是真 BGE-M3**，只测冷启动 RSS 与代理 RSS 两个头条。每点重复 3 次报中位数；(max−min)/median > 0.30 标 `unstable`。

**RSS 口径**：daemon 的 RSS 一律取**进程自报**（包装脚本里的 reporter 线程把 `psutil.Process().memory_info().rss` 写进 `PERF_RSS_FILE`，harness 只读文件）——因为本机对**分离启动（DETACHED_PROCESS）**进程的**跨进程**内存查询返回假值（对照实验：持有 300MB 的子进程，psutil / tasklist / PowerShell `WorkingSet64` 三家都报 ~4.4MB，自报 333MB）。由 `stdio_client` 正常派生的代理进程两种口径一致（自报 74.5MB vs 跨进程 78MB）。JSON 里另有 `cross_process_rss_*` 字段作注脚，**不进结论**。

整轮宿主峰值：CPU **83.1%** / 内存 **61.7%**（本机与其它 teammate 可能并行；每个测量点的窗口负载记在 JSON 的 `host` 字段，某点标 `unstable` 时先看那里）。

**宿主静窗门**：6/8 个测量点开测前等到了 < 40% CPU 静窗，其余到点即测并把 `host_gate` 写进证据。**绝对延迟会随宿主负载整体平移（实测忙窗 ≈ 静窗的 2.2×），但 N 的缩放形状（p50 ∝ N、吞吐持平）不受影响**——本页的可复现结论看形状，绝对数字请连同 `host` / `host_gate` 一起读。

## 1. 读并发：N 个读者（真 daemon HTTP `/mcp`）

| N | 样本 | p50 (ms) | p95 (ms) | p99 (ms) | wall (s) | 吞吐 (req/s) | p50 / N=1 | daemon RSS (MiB) | 客户端 RSS (MiB) | 稳定 | 静窗 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 36 | 125.224 | 180.214 | 182.258 | 1.63 | 7.4 | 1.0 | 530.8 | 84.3 | ok | 忙(等20.0s) |
| 4 | 144 | 550.602 | 802.854 | 827.991 | 6.762 | 7.1 | 4.4 | 536.6 | 85.5 | ok | ok |
| 8 | 288 | 1101.186 | 1393.67 | 1447.535 | 13.446 | 7.1 | 8.79 | 539.2 | 86.8 | ok | 忙(等20.0s) |
| 16 | 576 | 2053.529 | 2323.51 | 2340.704 | 25.327 | 7.6 | 16.4 | 544.4 | 90.3 | ok | ok |

读基线（N=1）p50 = **125.224 ms**。读数：**p50 随 N 近似线性上升、吞吐基本持平**——这正是 `INDEX_LOCK` 串行化的形状（服务端同一临界区排队），不是「多读者并行加速」。N=1 的绝对延迟里也含 Qdrant local mode **每次操作开关 client** 的开销（stub 档无模型；真模型再加每 query 的嵌入）。「静窗」列 = 开测前是否等到宿主 CPU < 40%（本机与 #71 teammate 并行，忙窗能把同一测量点抬高 ~2.2×；等不到就照测并把 `host_gate` 记进 JSON）。

## 2. 写串行：N 个并发 `memory_add`（`WRITE_LOCK`）

| N | ops | p50 (ms) | p95 (ms) | wall (s) | wall/op (ms) | 有效吞吐 (op/s) | 写时读探针 p50 (ms) | 写时读探针 p95 (ms) | 非 written 响应 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 616.63 | 627.656 | 1.28 | 640.0 | 1.56 | 600.375 | 612.853 | 0 |
| 4 | 24 | 2467.569 | 2534.237 | 5.347 | 668.4 | 1.5 | 654.72 | 760.104 | 0 |
| 8 | 48 | 5135.398 | 5214.308 | 10.903 | 681.4 | 1.47 | 755.781 | 858.793 | 0 |

**写侧是单写者串行（设计取舍，不是缺陷）**：`WRITE_LOCK` 让一次 `memory_add` 从头（去重检索）到尾（落盘 + git commit）独占，所以：① **p50 随 N 近似线性上升**（N=1→4→8 → p50 = 616.63 → 2467.569 → 5135.398 ms，即中位请求排在 ≈N/2 个写之后）；② **单次服务时间 ≈ `wall/op` ≈ [640.0, 668.0, 681.0] ms**（表里该列各 N 基本恒定），总墙钟 ≈ ops × 服务时间 → **吞吐 ≈ 1.56 / 1.5 / 1.47 op/s、不随 N 提高**；③ 写期间的读探针 p50 ≈ 654.72 ms（高于空闲读基线，因为写与读抢同一把 `INDEX_LOCK`）。取舍 = 去重的 TOCTOU 消除 + `.git/index.lock` 不争用 + 索引只被一个写者改（ADR-0013 D3）。 本次所有写请求都真的落盘（非 `written` 响应 = **0**）。

## 3. 长任务阻塞：请求延迟 vs 空闲基线

同一慢 Stub daemon（每篇 20.0 ms 人工延时）；探针 ~50 qps 单读者；每档 8.0 s。

| 场景 | 样本 | p50 (ms) | p95 (ms) | max (ms) | p50 / 空闲 | p95 / 空闲 |
|---|---|---|---|---|---|---|
| idle | 39 | 174.393 | 188.37 | 422.796 | — | — |
| index_rebuild | 13 | 600.239 | 777.157 | 1020.61 | 3.44 | 4.13 |
| host_cpu_saturated | 39 | 175.108 | 185.44 | 418.734 | 1.0 | 0.98 |

索引重建期间记录 13 个 `memory_reindex` 分块（每块 16 条，**批内持 `INDEX_LOCK`**），分块耗时 p50 633.3 ms / max 1073.1 ms。重建期请求被挡在 `INDEX_LOCK` 外：p95 抬到与「一块的嵌入耗时」同量级；块间（MCP 往返间隙）请求仍可穿过，所以 p50 抬升小于 p95。`host_cpu_saturated` 档是**同机 CPU 争用**，与锁无关（daemon 单线程处理请求）。

## 4. 真模型：1×BGE-M3 + N×数十MB（不是 N×3.9GB）

- daemon 冷启动（`--no-warmup`，未载模型）RSS = **131.0 MiB**
- 首次真正工作（建索引）后 RSS = **1960.5 MiB**（BGE-M3 载入 18.0 s）
- 又跑 5 次真查询后的稳态 RSS = **1941.1 MiB**（整段最大值 1960.5 MiB）
- 索引：{'entries': 8, 'gen': 'gen-1', 'total': 8}

| 会话数 N | 每代理 RSS (MiB) | 代理 RSS 合计 (MiB) | 若每会话各载一份模型 (N × daemon, MiB) |
|---|---|---|---|
| 1 | [74.4] | 74.4 | 1960.5 |
| 8 | [74.4, 74.4, 74.4, 74.4, 74.5, 74.6, 74.7, 74.7] | 596.1 | 15684.3 |

结论：**模型只在 daemon 里一份**；N 个会话各自只付代理进程的几十 MB。代理 RSS 与 N 基本无关，**合计 ≈ N × 数十 MB**，不是 N × 3.9GB——这就是 ADR-0013 拓扑的内存论点，现在有数字了。

> 口径边界：本机实测「载模型后」≈ 1941.1 MiB，**低于 AGENTS.md 里的 ~3.9GB 口径**（后者含 reranker 与峰值页；本档只装 BGE-M3 一份、`MEMORY_RERANK=0`、`--no-warmup` 后一次真工作即取样）。结论不受影响：付的是**一份**模型，不随会话数增长。

## 5. 边界与取舍（写清，不藏）

1. **读并发 = 串行服务**：`INDEX_LOCK` 是进程级 RLock，`search` 整体在锁内（含自洽核对 + 语料指纹扫描 + store 往返）。N 个读者不会并行变快；吞吐上限 ≈ 单进程检索吞吐。**取舍**：Qdrant local mode 同进程不能并发开 client（构造即 `already accessed`），不串行就不可用。要真并行得换 Qdrant server 形态（`MEMORY_STORE_URL` 已预留，ADR-0019）。
2. **写串行 = 单写者**：`WRITE_LOCK` 一次只放一个 add/supersede/archive 走完（去重检索 → 落盘 → git commit）。**取舍**：优先正确性（去重 TOCTOU / git index 争用）而非写吞吐。
3. **文件锁只在本机成立**：锁是**进程内** `threading` 原语；daemon 的 PID 文件（`proxy.py` 的启动权锁、`daemon_pid_path`）也是**本机文件系统**语义。跨机（NFS / 容器共享卷）不保证互斥；多机部署必须一个 daemon 一个索引（`MEMORY_MCP_PORT` 隔离）。
4. **Qdrant local mode 同进程独占**：同一 `path=` 在一个进程内只能有一个 client；所以本地平面天然要求「单 daemon」。
5. **测量口径的边界**：Stub 档的绝对延迟**不含** BGE-M3 查询嵌入（真模型每 query 还要加嵌入时间），但**并发形状（串行排队）与模型无关**；RSS 全部自报（原因见 §0）；宿主负载（与本机其它 teammate 并行）记在 JSON 每个测量点的 `host` 字段。
6. **隔离**：主树 `git status` 前后一致 = True；生产 `memory_agent/vector_db` 目录指纹前后一致 = True；worktree `git status` 前后一致 = True。

## 6. 精确复跑命令

```powershell
# 一把跑完（默认 N ∈ {1,4,8,16} × 3 次；Stub 扫描 + 真模型头条）
cd D:\python_work\work2026-4\wk-70-perf
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py

# 本页证据就是这么跑的（参数记在 meta.args / meta.command）：
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --requests 12 --longtask-seconds 8

# 单档：只看读并发 / 只看写串行 / 只看长任务 / 只看真模型
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --mode read --n 1,4,8,16 --runs 3
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --mode write
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --mode longtask
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --mode real

# 只改渲染口径（数字不动）：从既有 JSON 重新生成 MD
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/perf_70.py --render-only memory_agent/eval/perf_70_results.json
```

> 端口隔离契约：`--port 8891`（读/写 daemon）、`+1`（长任务慢 Stub daemon）、`+2`（真模型 daemon）；被占即报错退出，不静默换端口。
