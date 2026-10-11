# 正式基座与 Agent-bubble 的真实接入验收

问题：实验实例的读写成功，是否已经转化为日常宿主可用的部署？

判据：固定 OpenViking 版本；正式生命周期命令可运行；Agent-bubble 实际 pi 后端处理真实文章和视频来源并原样保存总结；另一身份及真实 Codex MCP 可以读到同一内容；服务重启后内容仍在；只读写入和未认证访问被拒绝；故障明确显示。

设置：家用 PC，正式状态目录 `~/.agent-memory-base`，OpenViking 8786、外部串行嵌入 8796、统一接入 8788；Bubble 保持其原端口 8787。来源复用 Bubble 已缓存的真实内容，不批量回填历史总结。凭据从系统凭据管理器或进程环境取得，报告只保存任务 ID、URI、内容哈希、检查结果。

执行：Bubble 的 live 脚本生成本轮任务，随后用 `verify.py --bubble-dir <Bubble目录> --task-id <文章任务ID> --task-id <视频任务ID> --out <报告>` 验接口结果。相同命令在基座重启后复跑。Codex 实际 MCP 工具调用另外记录；单纯 HTTP 成功不能代替该项。

结论：**2026-10-11（北京时间），本轮“可部署、可接入、实际使用”的交付通过。** 正式服务已从仓库主目录及专用 runtime 运行，Agent-bubble 已连接。实际 pi 使用 `deepseek-flash` 生成两份总结，并在另一会话调用搜索、读取；真实 Codex CLI 通过登记的 MCP 入口搜索、读回相同内容。正文与 Bubble 的本地结果逐字节一致，完整重启后三项服务健康，两份总结的 URI 和哈希保持不变。

## 证据

| 判据 | 实测结果 | 记录 |
|---|---|---|
| 正式部署与生命周期 | 固定六项直接依赖及传递依赖；安装、状态、停止、启动通过；源码指向本仓主目录 | [runtime.json](runtime.json) |
| 进程外串行嵌入 | 四个并发批次、八个 512 维向量全部成功 | [runtime.json](runtime.json) |
| 真实文章与视频总结 | 两个新任务由真实 pi 生成，保存成功，来源缓存未改 | [bubble.json](bubble.json) |
| Bubble 跨会话读取 | 文章会话中的实际追问调用 `memory_search` / `memory_get`，读到视频总结；追问不自动保存 | [bubble.json](bubble.json) |
| 另一身份读取与搜索 | 两篇正文与任务结果、本地 Markdown 逐字节一致，搜索可命中 | [before-restart.json](before-restart.json) |
| 真正 Codex 宿主调用 | `personal-memory` 搜索命中、原文读回、哈希一致 | [codex.json](codex.json) |
| MCP SDK 1.30 实际传输 | 初始化、列工具、原文读取成功；读写及幂等声明正确；reader 写入被拒 | [mcp-reader.json](mcp-reader.json) |
| 完整重启与重复请求 | 两篇总结重启后仍可搜索和读取；原请求重放返回 `exists`、原 URI、原哈希，索引 ready | [after-restart.json](after-restart.json)、[retry.json](retry.json) |
| 权限与故障 | 未认证 401；reader 写入 403；上游停机搜索明确返回 `503 / UPSTREAM_UNAVAILABLE`，恢复后原文可读 | [before-restart.json](before-restart.json)、[runtime.json](runtime.json) |
| 凭据检查 | 提交前检查七个凭据在 37 项待提交文件、部署配置/日志、宿主配置及 Bubble 配置/日志中无明文命中；仅记录范围和数量 | [access-checks.json](access-checks.json) |
| 源码导航 | 原有 28 项导航判据全部通过；只额外排除本地 venv 与 AOCI 缓存目录 | [navigation.json](navigation.json) |
| 当前资源占用 | 三个受管理进程树工作集相加约 635 MiB；非峰值或私有内存，共享页可能重复计数 | [resources.json](resources.json) |

文章任务为 `371a0f8f-dd90-4fe5-856c-34b9b7302f1a`（《给 Agent 加了记忆、反思、Skill，真的会变强吗？》）；视频任务为 `38985cdf-9228-442d-b387-a398abc35ece`（DSH Harness / Agent 架构教程）。完整 URI、正文哈希和来源 ID 在记录中。仓库记录不包含全文、带分享参数的来源 URL、访问密钥或原始模型轨迹；原始宿主轨迹保留在本机部署目录的 `acceptance/`。

最终回归：本仓 `pytest tests/unit -q` 为 **689 passed / 3 skipped**（一项既有 Qdrant 服务版本探测 warning），确定性 harness **10/10 预期匹配**；opencode-server 冒烟因本机没有 4096 服务而 **SKIP**，没有计为通过。Bubble 的 build 通过，应用测试 **64/64**、host 测试 **5/5**。正式 runtime 的 `pip check` 无依赖冲突；MCP 1.30 与 2.x 兼容测试各 30 项通过。

## 复跑

先使用根 `venv`，且正式服务与 Bubble 后端正在运行。以下读取验收包含幂等保存重试，不重新生成总结：

```powershell
. .\venv\Scripts\Activate.ps1
python experiments/memory-base-live/verify.py --bubble-dir D:\python_work\work2026-10\Agent-bubble --task-id 371a0f8f-dd90-4fe5-856c-34b9b7302f1a --task-id 38985cdf-9228-442d-b387-a398abc35ece --out experiments/memory-base-live/after-restart.json
python experiments/memory-base-live/refresh_navigation.py
```

`runtime_probe.py` 必须使用正式部署目录的 Python；它会主动停止、恢复上游及全部三个基座服务，并验证同一 URI/哈希，适合维护时复跑。`retry_api.py` 使用真实 Bubble 任务重放同一保存请求。`mcp_probe.py` 在所选 Python 中启动真实 stdio adapter；使用 `--expected-hash` 检查内容。各脚本参数见 `--help`。

`codex_probe.py --cli-defaults` 使用实际 Codex CLI 0.147.0，从用户配置读取已经登记的 `personal-memory` 命令、参数，临时采用 CLI 自身默认设置。原因是桌面配置的 `gpt-6.1-sol` 别名在独立 CLI 的当前账户中不可用；没有改全局模型配置，也没有指定替代模型。无人值守验证只批准本次进程中的两个读工具，禁用其余记忆工具，采用只读 sandbox。批准项见 [官方配置参考](https://developers.openai.com/codex/config-reference/)。这一结果证明实际 CLI 读链路，不证明当前已打开的桌面会话刷新了工具目录。

Bubble 的 `scripts/smoke-memory-live.ts` 会创建新总结任务并实际调用模型，原始报告位于 Bubble 的 `data/knowledge-base-acceptance/`。本轮复用了缓存中的真实来源正文；没有重新抓取远端页面或批量回填历史数据。该脚本运行方法及 UI 重试行为见 Bubble 的 `docs/knowledge-base-integration.md`。

## 交付边界

- **Windows 已部署并验收**。POSIX 薄壳的首次凭据持久化尚未完成；本轮没有 Linux/macOS 成功部署证据。
- **Codex、DSH、opencode 均已登记**，规约已落位；DSH/opencode 的新会话加载及实际对话调用尚未验证。旧 opencode `memory-agent` 入口已禁用，其余配置保留。
- **Bubble 后端已完成受管理重启并正在运行**，手机继续连接原后端 8787。总结自动保存；普通追问不自动新增；失败保留本地结果，可幂等重试。
- 此增量不等于 ADR-0032 四阶段全部完成。项目来源新鲜度在 ADR-0032 实测补充登记，后续由 #82 处理；#78 专门处理偏好护栏。就地映射、偏好升级、完整产品化裁剪和 Windows 开机自动启动未纳入此次验收。此次验收未使用 AOCI；这不代表后来仍未建立索引。
