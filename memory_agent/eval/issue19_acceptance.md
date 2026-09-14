# issue #19 验收证据：共享单实例 daemon + stdio 代理

> 记录时间：2026-09-14 · 分支 `feat/19-shared-mcp` · 机器：Windows / Python 3.12（venv）
> 目的：证明「N 个 opencode 会话共享一份嵌入引擎」的拓扑正确、并发安全、可诊断。

## 结论

**PASS**。真实模型（BAAI/bge-m3，私有内存 ~3.9GB）下的端到端验收：

| 验收项 | 结果 |
|---|---|
| 只有一个进程持有嵌入引擎 | ✅ HTTP daemon 中 >1GB 的进程数 = **1**（旧 stdio 会话各 3.86GB） |
| 多会话并行 `memory_search`（3 并发，各自经代理） | ✅ **3/3** 成功（各 3 hits，top score 0.54–0.61） |
| 首次检索不冷启动 | ✅ daemon eager 预热完成（log `memory index warmup done`）后才检索 |
| daemon 不可用时错误清晰 | ✅ `/health` 不可达；`proxy --status` → `down`，rc=1 |
| 并发写不撞 `.git/index.lock` | ✅ 临时 KB 两路并发 `memory_add` → **2/2 写入、2 个 commit、无 `index.lock`** |
| `legal_web` 不受影响 | ✅ boot 冒烟：`/api/status ready:true`、`/` 与 `/script.js` 200、`documents/count=3799` |

复现：`venv\Scripts\python.exe memory_agent/eval/issue19_acceptance.py`（用临时 KB + 索引副本，不污染真实 KB）。

## 过程中发现并修复的问题

### D1 冷启动竞态（真实缺陷）

**现象**：验收首轮，3 个代理几乎同时发现 daemon 未就绪，于是**各自 spawn 一个 daemon**；多个进程同时加载 BGE-M3，直接把内存打爆（日志 `memory allocation of ... failed`），daemon 崩溃、SSE 流中断，检索全部失败。

**根因**：`ensure_daemon` 只做「健康检查 → 不在就 spawn」，在读-写之间没有互斥；N 个会话同时冷启动 = N 次模型加载。

**修复**：加**启动权文件锁**（`os.open(O_CREAT|O_EXCL)`，带陈旧锁回收）。抢到锁的进程负责 spawn + 等就绪，其余只轮询健康检查、不重复拉进程。回归：`tests/unit/test_memory_proxy.py`（含「抢不到锁不 spawn」「陈旧锁可回收」）。

### D2 单进程内 Qdrant 锁（并发前提）

实测 Qdrant local mode **同一进程内也不能并发开两个 client**（4 线程探针 3 个立刻 `RuntimeError`）。故 `VectorStoreService._session` 加进程内 `RLock` 串行化「构造→操作→close」；`MemoryIndex`/`Reindexer`/`MemoryWriter` 再各加逻辑锁（INDEX/WRITE），保证自洽核对、gen 快照、git 提交的原子性。回归：`tests/unit/test_memory_concurrency.py`、`test_memory_writer.py::test_concurrent_adds_are_serialized`。

### D3 安全：DNS-rebinding 防护默认关闭

该 `mcp` 版本路径下 `transport_security=None` → 防护关闭。只绑 `127.0.0.1` 挡不住浏览器 DNS rebinding。已显式设 `allowed_hosts=["127.0.0.1:<port>", "localhost:<port>"]`。

## 环境观察（非本票缺陷）

- 迁移前实测：**每个 opencode 会话的 stdio `mcp_server.py` 各占 ~3.86GB**（并发 8 个 = ~31GB），这是 #19 要治的病。验收前为腾内存已停掉这些旧进程。
- 模型加载期仍有一次对外 HF 请求（`HEAD .../model.safetensors.index.json → 404`），尽管加载器设了 `local_files_only=True`——与 issue #18 同一现象，不属本票。

## 复现要点

- daemon：`python memory_agent/mcp_server.py --transport http --host 127.0.0.1 --port 8765`（默认 eager 预热；`--no-warmup` 可关）。
- 代理：opencode `type:"local"` 命令指向 `memory_agent/proxy.py`（每会话一个瘦代理，自动确保 daemon）。
- 就绪：`GET http://127.0.0.1:8765/health`。
