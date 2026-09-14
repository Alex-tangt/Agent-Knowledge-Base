# 0013 memory_agent 拓扑：共享单实例 HTTP daemon + 每会话 stdio 代理

Status: accepted

issue #19 的根治。取代 ADR-0008 中「传输 = stdio、引擎进程内、无长驻服务」的约定（D1 的 stdio 默认仍保留为单会话/手动模式，但**不再是 opencode 的标准接入形态**）。

## 背景

opencode 每个会话都会拉起一份 stdio MCP 进程，各自加载一份 BGE-M3（实测**每个 ~3.9GB**，`mcp_server.py` 私有内存）。Windows 无 `fork()`，进程间无法 COW 共享权重；三条并行会话即把系统 commit 打满（`Out of memory` / 卡死）。#19 第一步（惰性预热）只把"没用到的会话"降到 54MB，**用到的每个会话仍各付 3.9GB**。

## 决策

**D1 拓扑 = 一个常驻 daemon + N 个瘦代理。**

```
opencode 会话 A/B/C …（每会话一个 proxy.py，stdio）
        │  stdio JSON-RPC 透明转发
        ▼
一个 HTTP daemon（streamable-http，固定 127.0.0.1:8765，持有唯一一份 BGE-M3）
```

- daemon：`mcp_server.py --transport http`（复用 `mcp` 2.x 自带传输，不自造 IPC）；默认 **eager 预热**（全局仅一份，常温才能免冷启动）。
- 代理：`proxy.py`，仍是 opencode 的 `type:"local"` 命令。每会话一个**瘦**进程（几十 MB），职责是①确保 daemon 在跑、②把本会话 stdio 请求转发到 daemon 的 `/mcp`。
- 选**代理**而非 `type:"remote"` 直连：remote 模式下 opencode 只"连"不"起"，daemon 没起则该会话被标记 failed 且不自动重连；代理让 opencode 的本地命令生命周期负责"按需拉起 + 握手后才就绪"，无启动竞态、也不会忘记起。代价是多一个待维护的转发层。

**D2 生命周期 = 按需拉起、进程内重定向日志、独立就绪探测。**
- `proxy --ensure` 幂等：已在跑直接返回；否则由**唯一抢到启动权文件锁**的进程 spawn daemon（`DETACHED_PROCESS`，子进程内把 stdout/stderr 重定向到 `memory_agent/vector_db/daemon.log`）。
- **必须**有启动权锁：否则 N 个会话同时冷启动会各 spawn 一个 daemon、各加载一份模型，把内存打爆（验收首轮实测踩到）。陈旧锁（>120s）可回收。
- 就绪探测 = `GET /health`（daemon 上的自定义路由），不是"端口被占"。daemon 崩溃/不可达时代理返回清晰错误，不静默。
- daemon 起来后常驻到手动停或重启机器；**不做空闲卸载**（那会把"反复加载"变成常态，回到最初的问题）。

**D3 并发 = 进程内串行化（daemon 要服务 N 会话）。**
- 实测 Qdrant local mode **同一进程内也不能并发开两个 client**（4 线程 3 个立刻 `RuntimeError`），退避重试兜不住。`VectorStoreService._session` 加模块级 `RLock` 串行化「构造→操作→close」。
- `memory_agent/memory/locks.py`：`INDEX_LOCK`（search 的自洽核对+查询、refresh/rebuild/status/reindex 互斥）、`WRITE_LOCK`（add/supersede/archive 从头到尾一个临界区）。加锁顺序恒为 `WRITE → INDEX → store`。
- 副作用收益：单 daemon = **单写者**，部分收掉 PRD #7 的"多写者" out-of-scope。

**D4 安全 = 只绑回环 + DNS-rebinding 防护。**
- 默认 `127.0.0.1`；显式 `allowed_hosts=["127.0.0.1:<port>","localhost:<port>"]`（该 `mcp` 版本路径下防护**默认关闭**，仅绑回环挡不住浏览器 DNS rebinding）。

**D5 保留 stdio 模式。** `--transport stdio` 仍是默认，供单会话/手动/调试；opencode 的接入从"直接跑 `mcp_server.py`"改为"跑 `proxy.py`"。

## Consequences

- 内存：N 会话常驻从 `N × 3.9GB` 降为 `1 × 3.9GB + N × ~几十MB`。
- 单点：daemon 挂了，所有会话的记忆工具不可用（代理报错，不静默）；重启 daemon（或重开会话触发 `ensure`）即恢复。记忆能力非关键路径，接受。
- `legal_web` 不受影响：独立进程、独立 Qdrant 路径；ragcore 的 store 串行锁顺带修了它同类并发隐患。
- 已知限制（另行开票）：模型加载期仍有一次对外 HF 请求（issue #18）；daemon 崩溃后当前会话不会自动重连。

## 备选与否决

- **`type:"remote"` + 登录自启/手动起**：登录自启对"只有 agent 用"过重；手动起易忘 → 会话 failed 且不重连。否决。
- **空闲卸载**：会把冷启动（~30–40s 模型加载）变成常态。否决。
- **换小模型**：#19 的 B 方向，质量影响待 #15（BEIR nDCG@10）给证据，与本拓扑正交，另行评估。

## 证据

`memory_agent/eval/issue19_acceptance.md`（+ 脚本 `issue19_acceptance.py`）：单份模型、3 并发检索、2 并发写入无 `.git/index.lock`、daemon-down 清晰报错、`legal_web` 启动冒烟。
