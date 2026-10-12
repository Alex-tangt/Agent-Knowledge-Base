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

先激活根 `venv`，正式服务与 Bubble 后端需要正在运行。当前 `memory_base` 安装在专用 runtime，下面直接使用其 Python，避免从脚本目录运行时误以为根 venv 已安装新包。以下读取验收包含幂等保存重试，不重新生成总结：

```powershell
. .\venv\Scripts\Activate.ps1
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" experiments/memory-base-live/verify.py --bubble-dir D:\python_work\work2026-10\Agent-bubble --task-id 371a0f8f-dd90-4fe5-856c-34b9b7302f1a --task-id 38985cdf-9228-442d-b387-a398abc35ece --out experiments/memory-base-live/upgrade-2026-10-12.json
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

## 扩充后的正式回归（2026-10-12）

本节是后续证据，保留上方 2026-10-11 的原始口径与报告。基座实现冻结于 `d8a6709`；四工具回归脚本修订于 `1e78f0d`。默认 capture 接入仍兼容 Bubble，无新 LLM 或总结生成。

| 检查 | 结果与范围 | 新记录 |
|---|---|---|
| Bubble 旧文章/视频 | HTTP 原样读回、逐字节核验、搜索、同请求重试通过；原 URI/hash 均未改变 | [upgrade-2026-10-12.json](upgrade-2026-10-12.json) |
| Lead 实际 SDK 1.30 stdio | 四工具、annotations、expected_revision、limit=1 全库分页、reader 写拒绝通过；文章另做一次现有本地查询嵌入 | [文章](mcp-upgrade-article-2026-10-12.json)、[视频](mcp-upgrade-video-2026-10-12.json) |
| 当前 Codex 桌面 MCP 读项目 | 正式登记 `agent-kb`，仅 README/docs；两个当前文件的原字节 revision 等于本机 SHA256，65 个 Markdown 完整列举；一条实际项目词法查询首位命中部署说明 | [project-2026-10-12.json](project-2026-10-12.json) |
| 正式项目即时变更 | 实际文档更新后，旧 expected_revision 返回冲突，新正文含更新且等于本机原字节哈希 | [source-change-2026-10-12.json](source-change-2026-10-12.json) |
| Lead 独立恢复复核 | 重算 #85 的 85 文件/50 目录清单；实际启动恢复目标，原文哈希、reader 列举范围与 native user 拒绝通过；目标已停下 | [lead-recovery-2026-10-12.json](lead-recovery-2026-10-12.json) |
| 正式当前用户登录启动 | 登记命令归属准确，其它 HKCU Run 项不变；手动执行该 pythonw 命令退出 0，三个受管理服务就绪；实际登录触发未测 | [autostart-2026-10-12.json](autostart-2026-10-12.json) |
| 正式冷备份 | 67 文件与完整目录清单独立核验；sources.json 原样保留；7 个已知凭据的 UTF-8/UTF-16 检查无明文命中；服务重启后原总结和教训读回一致 | [backup-2026-10-12.json](backup-2026-10-12.json) |
| 实际正式快照恢复 | #94固定67文件/39目录空data恢复、三原文/当前项目/凭据重发/权限/MCP与清理通过；Lead独立核31归档原件、清单，再起目标读三receipt和当前来源、scope/native403并停下；所有方法FAIL单列 | [完整记录](../memory-base-formal-restore/README.md)、[Lead](lead-formal-restore-2026-10-12.json)、[Lead首失败](lead-formal-restore-first-failure-2026-10-12.json) |
| 新实现完整闸门 | 冻结 `9252d52`：824 passed / 5 skipped；harness 10/10；opencode 冒烟因无 server 为 SKIP | [gates-2026-10-12.json](gates-2026-10-12.json) |
| 冷启动修复完整闸门 | 冻结 `45296a5`：833 passed / 5 skipped；harness 10/10；opencode server 缺席仍为 SKIP；独立新环境首次运行另列 #86 | [gates-cold-start-2026-10-12.json](gates-cold-start-2026-10-12.json) |
| 解释器入口修复最终闸门 | 冻结`4fc5aa9`：837 passed /5 skipped，harness10/10，opencode脚本SKIP；两真实wheel fixture模式包含在全量内；产品只改deploy入口识别，native旧证据仍限45296a5 | [gates-runtime-path-2026-10-12.json](gates-runtime-path-2026-10-12.json) |
| 发布前 Lead 增量复核 | #95 固定范围之后仅地图/历史状态与 CI 版本变更；16 个产品文件仍与冻结闸门一致、60 本地链接有效；CI 原 steps 保持，Python 对齐3.12，远端运行未开始；三服务及 Bubble 当前健康，ADB 无设备 | [lead-publication-delta-2026-10-12.json](lead-publication-delta-2026-10-12.json) |
| 发布隐私复核与最终增量 | #95 固定tree/history实扫1131 blobs，实际47当前凭据/3原文/1私有URL无匹配；6个保留项由Lead独立解析为语法/fixture/环境引用。后续25新blob的4项定位均为语法/计数/明确smoke，保留原始定位与独立补验 | [审计报告](../memory-base-publication/README.md)、[6项独立复核](lead-publication-review-2026-10-12.json)、[最终增量](lead-publication-final-delta-2026-10-12.json) |
| 冷启动后独立安装运行 | R5默认/fresh各175个自身distribution/173约束；默认第一次native start原180秒配置，168.13秒成功、12条checks；Lead53原件/产物字节与当前归属/端口清理核验；原FAIL保留 | [R5](../memory-base-package/cold-final-2026-10-12/README.md)、[Lead](lead-package-cold-2026-10-12.json) |
| Bubble 四工具正式接入 | 候选 `0a86cb1` 四文件先核基线、备份再应用；Lead build、68/68 应用测试和 5/5 host 测试通过；零活跃任务时自有管理器重启后健康 | [应用与闸门](bubble-bridge-applied-2026-10-12.json)、[候选证据](../memory-base-bubble-bridge/README.md) |
| Bubble 真实 SDK / 正式网关 | 本机 synthetic SSE 驱动实际 pi：四工具 schema、项目检索、两页 list、原文 revision、版本/目录冲突反馈、旧 capture 哈希通过；只读，不生成新总结 | [候选](bubble-bridge-candidate-2026-10-12.json)、[正式代码](bubble-bridge-formal-2026-10-12.json)、[复跑脚本](bubble_bridge_probe.mjs) |
| Bubble 笔记保存意图 | 冻结 `36f9bf2` 只改两文件，Lead 73/73 应用与 5/5 host 通过，核实际基线/备份/构建、零活跃任务、自有 manager 重启健康；实际工具拒绝8条、允许5条，假client无正式笔记写入；正式 SDK/网关只读复核及旧文章 hash 与回执一致 | [应用](note-intent-applied-2026-10-12.json)、[工具](note-intent-formal-tool-2026-10-12.json)、[实际 pi](note-intent-formal-pi-2026-10-12.json)、[候选与原失败](../memory-base-note-intent/README.md) |
| Windows 状态发布恢复 | #93冻结三scripts与#92组合`8e13fae`，Lead build、73应用/13host通过；原子同步200ms有界重试、持续失败自有child退出/instance清理；精确actual基线备份应用、零活跃任务重启健康；编译记忆模块与组合版本逐字节相等 | [应用](host-state-applied-2026-10-12.json)、[候选、真实原FAIL与注入](../memory-base-host-state/README.md) |
| 当前闲置工作集 | 核对 PID、创建时间和完整命令后采集三个进程树，共 273.97 MiB；包含控制台子进程，可能重复计算共享页，非峰值/私有内存；不与旧测点作优化幅度比较 | [resources-2026-10-12.json](resources-2026-10-12.json) |
| 宿主规约同步 | Codex、DSH、opencode 的 personal-memory SKILL 均与仓库规约一致，SHA256 `72527b6cc7e74c5c8266f01f85d3dcbe398415ed24404facfe722d1468c5d964`；旧副本保留本机备份 | 只证明规约落位，DSH/opencode 实际会话仍未验 |

这条项目词法查询验证实际接缝，不是检索质量benchmark。项目原文没有复制进native capture，源文件当前状态仍为权威。正式备份位于 `D:\python_work\work2026-4\memory-base-backups\formal-20261012T014904`，manifest SHA256 为 `27dc14f772e61a351be62bf3c5dfb24397c27cec8894612065139a8677541c65`；创建时尚未做自身恢复，后续#94实际恢复与Lead复核已通过，见上表。此前#85证明的是独立合成实例的空目标恢复。

#85完整原生隔离恢复、#86独立包及#94实际正式快照恢复分别记录。#89/#91后的#86 R5已取得真实新栈/默认首次运行证据；保留此前FAIL及环境边界。Bubble#90/#92/#93代码已实际应用，组合Lead闸门73应用/13host通过。跨设备、真实登录及DSH/opencode实际对话仍未验证，不能宣布整个产品目标完成。AOCI按owner决定移除。
