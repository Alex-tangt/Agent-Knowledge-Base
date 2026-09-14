# 0008 memory_agent 读路径：MCP 实现选型 + 语料范围 + 引擎接缝

Status: accepted

issue #10（读路径最小闭环）落地时确定的四个接缝。本 ADR 只记录**难逆或踩过坑**的决策，不含实现细节。

## 决策

**D1 MCP 实现 = 官方 `mcp` 2.x（当前稳定线）。** 理由：标准协议 + 零自定义胶水（"opencode 用标准 MCP 配置接入"是验收项），且包要能独立发布。代价：2.x 是对 v1 的大重写、生态文档仍偏 v1。**出口**：若 stdio 握手与 opencode 不兼容且两次修复无效，回落 `mcp>=1.28,<2`（v1 线仍收关键修复），并在本 ADR 追加记录。

**D2 #10 只读语料 = 本仓库（Agent-Knowledge-Base）。** 可写 = 全局 KB 条目（frontmatter 带 `id`）。理由：自足、可当场验证 `writable:false`，一次把闭环跑通；issue 里"三个项目仓库"未点名，留后续票据扩展（`corpus/loader.py` 的 `READONLY_ROOTS` 即扩展点）。

**D3 派生索引用独立 Qdrant 路径 `memory_agent/vector_db/`。** 与 `legal_web/vector_db` 分开（各自的生命周期与重建时机不同）。索引 + `manifest.json` 都是派生物，可丢弃、可由 Markdown 全量重建（`memory_agent/build_index.py`）。

**D5 Qdrant local client 按操作开/关，不缓存。** 实测锁语义：锁在 **client 构造期**持有、`close()` 释放，构造函数无绕过开关。旧实现把 client 缓存在 `VectorStoreService._client` 上，锁从构造一直持有到进程结束——opencode 常驻的 MCP 服务因此整天独占存储目录，索引重建 / CLI / 冒烟脚本全被挡（实测 `RuntimeError: Storage folder ... already accessed by another instance`）。改为每次操作 `with self._session()` 开关一个 client：开销实测 **open 17.6ms + query 1.2ms + close 0.2ms ≈ 19ms**（1024 维 × 60 点），相对单次嵌入是噪声；锁只在调用期存在，并配 6 次递增退避重试应对撞车。回归测试：`tests/unit/test_vector_store_locking.py`（同进程内构造第二个 client 必须成功）。

**D4 `memory_get` 从文件读回，不从索引读。** 真相源是 Markdown（ADR-0006）；索引只负责找到条目，内容取文件。索引与文件不一致时以文件为准。

## 踩过的坑（必须守住）

- **stdio 通道不能被日志污染。** ragcore `utils/logger.py` 把 root handler 配到 `sys.stdout`——对 stdio MCP 是致命的（腐蚀 JSON-RPC）。`memory_agent` 必须在 import ragcore **之前**把 root logger 抢配到 stderr（`_bootstrap.configure_stderr_logging`；`logging.basicConfig` 只在 root 无 handler 时生效，先配者胜）。
- **模块名不得与 ragcore 的顶层包重名。** 实测：`memory_agent/config.py` 会在 `python memory_agent/xxx.py` 下遮蔽 ragcore 的 `config` 包（`ModuleNotFoundError: No module named 'config.config'; 'config' is not a package`）。故改名为 `settings.py`，并在入口脚本里把自身目录从 `sys.path` 剔除、只加仓库根；内部一律用 `memory_agent.` 前缀绝对导入。

## Consequences

- 新增依赖 `mcp>=2.2,<3`（`memory_agent/requirements.txt`）；引擎依赖仍复用 `legal_web/requirements.txt`。
- 因 D5，`memory_agent` 与 `legal_web` **可以并存**运行（锁不再长期占用）；两者同时重活时仍可能短暂撞锁，靠退避重试兜住。引擎模型仍是每进程一份内存（2.2GB×2），这是内存代价，不是正确性问题。
- 已知债务：`memory_agent` import `ragcore` 会连带触发 `ragcore/config/config.py`，后者要求 `legal_web/.env` 里的 LLM 三项存在——**读路径并不需要 LLM**。独立发布前需解开该耦合（另行开票）。
