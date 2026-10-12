# 正式个人记忆基座部署

正式运行包为 `memory_base/`，复用 OpenViking 0.4.23。总结与笔记通过共同 HTTP/MCP 追加、搜索、读取和列举；登记的项目 Markdown 实时只读，支持路径范围和字节版本核验。本机 CLI 管理来源、宿主权限、完整冷备份恢复及可选 Windows 登录启动。旧 `memory_agent` 引擎保持冻结，偏好升级仍遵守 #78 的批准边界。实现状态与真实验收分别记录在[交付任务板](memory-base-delivery-plan-2026-10-11.md)。

## 安装和运行

需要 Python 3.12。Windows 在仓库根目录运行：

```powershell
.\install-memory-base.ps1 -Python <Python3.12可执行文件>
```

本轮现场交付与验收针对 Windows，默认使用系统凭据管理器，不将访问密钥写入应用配置。安装固定 OpenViking 0.4.23、SDK 0.1.13、MCP 1.30.0、llama-cpp-python 0.3.36，并固定已验证的传递依赖；原生依赖使用作者提供的 CPU wheel。`install-memory-base.sh` 已保留 POSIX 薄壳，但首次创建上游身份后的凭据持久化尚待补齐，Linux/macOS 部署未完成验收。

默认状态目录为 `~/.agent-memory-base`；专用 Python 位于该目录的 `runtime/`。安装后使用它执行：

```powershell
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base start
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base status
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base logs
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base stop
```

`restart` 重启所管理的服务；`install --fresh-runtime` 选择新的专用环境，保留同一状态目录里的数据与身份。`--home` 可指定另一个部署目录。生命周期命令校验 PID、创建时间和启动命令，不接管占用端口的其它服务。

Windows 可显式登记当前用户登录后的自动启动：`memory-base autostart --mode enable`；`--mode status` 核验，`--mode disable` 移除本部署自己的启动项。使用专用解释器时可写成 `python -m memory_base autostart --mode enable`。命令只管理按 home 区分的 HKCU Run 值，不带凭据；登记成功与实际登录触发是两项证据。正式实例的登记状态见下方交付记录。未登记时，重启后执行 `start` 并确认 `status=ready`。安装失败保留数据与日志，可修复前置条件后重新执行安装。

运行包含三个仅监听本机的进程：OpenViking 8786，进程外串行嵌入 8796，统一接入服务 8788。`status=ready` 要求三个受管理进程均健康。OpenViking 显式采用 key 模式、开启账户 ACL，注册密钥以哈希保存；宿主凭据从系统凭据管理器注入进程环境。手机只连接原有 Bubble 后端 8787。

## 宿主接入

MCP 统一入口为：

```text
<专用Python> -m memory_base.mcp_stdio --profile <宿主名> --home <部署目录>
```

已登记身份名为 `codex`、`dsh`、`opencode`、`agent-bubble`（writer）及 `reader`（只读）。接口暴露 `memory_add`、`memory_search`、`memory_get`、`memory_list`；普通宿主仅能追加 capture，不提供覆盖、删除或管理工具。HTTP 对应 `/api/memory/add`、`/search`、`/get`、`/list`，与 MCP 共用实现。

MCP 声明搜索、读取和列举为只读，追加为非只读；四个工具均声明幂等、非破坏性且仅在受控范围工作。这些提示供客户端展示和审批使用，权限仍由服务端凭据校验。

Codex 可通过其本机 `codex mcp add personal-memory -- <专用Python> -m memory_base.mcp_stdio --profile codex --home <部署目录>` 登记。DSH 的实际入口为 `~/.dsh/profiles/desktop/cordis.patch.yml` 中的 `@deepseek-ai/dsh-mcp-client` 插件，以 `insert` 插入一条 stdio 配置；opencode 在其 `mcp` 配置登记同一入口。配置里只有路径与身份名。宿主规约源文件为 `memory_base/skill/SKILL.md`。

本机注册复跑脚本为 `experiments/memory-base-live/configure_hosts.py`（参数见 `--help`），会备份宿主配置、登记三个 MCP 宿主、落位规约，并补上 Bubble 三个非密钥字段。它保留已有配置，只禁用 opencode 的旧 `memory-agent` 注册。MCP 宿主在新会话生效，Bubble 通过自身 `npm run host:stop` / `npm run host:start` 生效。

Bubble `.env` 只增加 `MEMORY_BASE_URL`、`MEMORY_BASE_PYTHON`、`MEMORY_BASE_HOME` 三个非密钥字段。launcher 调用凭据提供器，把返回 token 放入后端进程环境。首次成功总结原样保存；普通追问不自动新增条目；用户要求保存笔记时可使用 pi 写入工具。保存失败保留本地总结并给出重试状态，重试不再调用模型。Bubble 细节见其 `docs/knowledge-base-integration.md`。

2026-10-12 的 Bubble 接入已扩为四工具：可按项目 scope 检索、分页 list，并用 expected_revision 读原文；版本/目录变化提示重新查询，来源失联与权限错误不当作空结果。候选四文件备份后已应用正式目录，build 与既有测试通过，后端自有管理器重启后健康。Lead 用实际 pi SDK 和正式网关核验读路径，模型为本机 synthetic SSE，因此不将该证据声明为新增的真实模型对话。

笔记工具现单独核对本轮用户的明确保存命令。否定保存、询问状态或建议、引用/来源指令与撤回不能授权写入；支持“请保存这条笔记到知识库”和“Can you save this note to memory?”等明确命令。未匹配表达返回明确示例，正文仍原样提交，未知接触日不填写。此次两文件修改已在正式 Bubble 应用、构建并受管理重启；Lead 全量应用 73/73、host 5/5 及真实工具/只读 pi 接入复核通过。它使用有限命令规则，不声称理解所有自然语言表述。

后续 #93 修复 Windows 状态发布的短暂 EPERM/EACCES/EBUSY：同步原子重试至多暂停200ms，持续失败以安全错误日志关闭自有 child、清理本instance，保持原120秒就绪上限。与笔记修复组合后，Lead build、73/73应用和13/13 host通过；三scripts按基线核对和备份应用，正式manager重启健康。原真实FAIL与故障注入RED/恢复分别保留，不将注入测试冒称原生锁复现。记录见[实际应用](../experiments/memory-base-live/host-state-applied-2026-10-12.json)。

## 数据和边界

正文保存在 `viking://resources/captures/<请求地址哈希>/content.md`，同目录 `capture.json` 保存身份、时间、来源、请求及正文哈希。地址由身份和 request_id 派生；同一请求内容变更返回冲突，不能覆盖原文。并发保障以正式部署的单 gateway 进程为边界。

成功响应的 `index.status=pending` 表示正文已保存但派生索引尚未就绪；重试同一请求会再次尝试索引。不可用为明确错误，不能解释为没有记忆。`observed_on` 是人明确提供的接触日，缺失保留 `unknown`；新增日期和逻辑路径不搬移旧 URI。`memory_get(expected_revision=...)` 核验原始 UTF-8 字节 SHA256，版本变化返回冲突。

## 来源、范围与恢复

以下命令均使用安装后的 `memory-base`，或专用解释器的 `-m memory_base`。其它 home 增加 `--home <部署目录>`。

```powershell
memory-base source-add --name agent-kb --root <项目绝对路径>
memory-base source-list
memory-base source-remove --name agent-kb
memory-base access-set --profile reader --read-scope captures --read-scope projects/agent-kb/docs
memory-base restart --components gateway
memory-base backup --destination <新的完整备份目录>
memory-base restore --home <已安装且数据为空的目标部署> --backup <完整备份目录>
```

来源默认仅包含 `README.md` 与 `docs/**/*.md`，显式登记后按当前文件读回，不复制正文作第二主库。登记规则运行时重读，源失联明确报错，不能回退旧快照。查询项目时指定 `scope="projects/agent-kb"`；默认搜索仍为 captures。项目首期为标明方法的词法扫描，返回版本和扫描范围；`memory_list` 无需向量，按 `next_cursor` 翻页直至空。

`access-set` 持久化 `access.json`，权限在 gateway 启动时装载，改后需重启 gateway。省略规则沿用共享默认；`--deny-read`、`--deny-write` 明确拒绝；scope 只收窄，writer 无权写入项目文件。capture 的逻辑路径为 `captures/<接触日或unknown>/<kind>/<id>/content.md`，旧 URI 与新地址遵守同一授权。

完整备份受生命周期锁保护，停服复制整个 native data、`sources.json`、`access.json` 和哈希清单，结束恢复原先运行的组件。备份目录不得与部署或来源根重叠，不覆盖已有备份。目标必须已安装、data 为空且没有既有来源/范围/恢复状态；恢复先核验全部清单与哈希，保留目标运行路径、模型与端口，重新签发目标凭据。完整冷备份是恢复介质；项目正文仍由源项目保管，迁移后必须核验来源路径可达。该流程提供明确恢复点，不承诺任意时刻回滚。

最终运行证据与复跑方法见 [真实接入验收](../experiments/memory-base-live/README.md)。安装和接口测试通过不替代真实宿主验收。

## 本机交付结果（2026-10-11）

正式状态目录为 `C:\Users\Tan\.agent-memory-base`，运行使用其中的 `runtime\Scripts\python.exe`，源码指向本仓主目录。三个基座服务与 Bubble 后端均在运行。真实 pi 的文章、视频总结已原样保存，跨会话读取成功；真实 Codex CLI 搜索、读回通过。完整停止和启动后，两份内容的 URI、哈希及搜索结果保持一致；上游不可用时返回明确故障。

Codex CLI 验证使用已登记入口及临时 CLI 默认设置，因桌面模型别名在独立 CLI 不可用；全局模型配置保留。DSH/opencode 已完成配置登记，需要新会话加载，其实际对话调用未纳入本轮成功声明。AOCI 于 2026-10-12 按 owner 决定停用，不参与安装、开发或接入。

**2026-10-12 补充**：正式实例已登记 `agent-kb` 的 README/docs，当前 Codex 桌面 MCP 可读到实时文件；文档变更后旧 revision 被拒，新原文立即可读。当前用户登录启动已登记，核验命令并手动执行后服务就绪；实际注销/重新登录触发未测试。

正式冷备份保存在 `D:\python_work\work2026-4\memory-base-backups\formal-20261012T014904`，67 个文件的 size/SHA256 与目录清单独立核验通过，7 个已知凭据的 UTF-8/UTF-16 明文检查无命中；服务恢复后两份旧总结和教训原样读回。该正式快照尚未自行恢复；独立原生验收 #85 已在空数据根验证完整冷恢复、权限拒绝与原文，11 项通过，Lead 另行启动恢复目标复核后停下。复跑与限界见[真实接入记录](../experiments/memory-base-live/README.md)及[原生验收](../experiments/memory-base-recovery/README.md)。

冻结版本 `d8a6709` 的既有全量闸门为 802 passed、5 skipped；#89 修复后的 `9252d52` 为 824 passed、5 skipped，harness 10/10 预期匹配；opencode server 缺席，冒烟 SKIP。#86 曾发现 wheel 默认新 runtime 安装失败，原 FAIL 保留。短目录 R4 的默认完整安装及包来源检查通过，但原生启动超时；该次试验额外禁用了 Python 缓存写入，不能无条件归因于普通部署，标准环境运行证据仍在核验。不能用 CLI help 或安装成功代替服务运行通过。

Windows 未启用长路径时，过深的 home 会使上游 SDK 的生成文件安装失败。优先采用默认 `~/.agent-memory-base` 或独立短路径，不需要为产品试用修改全局注册表。实验中的深路径失败仍保留；短路径安装通过不证明任意路径均可用。

标准运行条件的 fresh 冷启动随后仍超过原 300 秒上限，已排除额外 bytecode 禁止因素，原生运行判据尚未通过。正式已有实例仍可用；新的独立环境不应据安装成功便视为已可部署，后续修复与验收见任务板。

**后续冷启动修复与独立复验（2026-10-12）**：上述失败原样保留。`45296a5` 在真实安装时只准备选定 runtime 自身的 Lark SDK，不触碰继承的 provider；已有有效缓存保留，安装准备有独立600秒预算，服务默认180秒/组件未扩大。R5两个全新目标各175个自身distribution、173条有效约束，pip check与产品原件检查通过；默认第一次native start不传timeout，168.13秒成功，12项原文/幂等/权限/版本/search/list/实际MCP/自有清理通过。Lead另外独立核53份证据及两个目标的产品字节、当前端口与进程归属。原准备探针341.750秒仅是该探针测点，R5准备轮询窗口约300/291秒不等于函数精确耗时。

因此已取得新独立环境的实际运行证据，结论限Windows/Python3.12短目录、既有公共pip缓存和本地GGUF；未验证全新机器的模型下载、真实登录或第二台设备，不宣称深路径/POSIX支持。完整原始记录见[R5验收](../experiments/memory-base-package/cold-final-2026-10-12/README.md)与[Lead复核](../experiments/memory-base-live/lead-package-cold-2026-10-12.json)。

**实际正式快照恢复（2026-10-12）**：前述67文件冷备份已实际恢复到新的`.mb94/a`空数据目录。start前67文件/39目录逐字节匹配；三份旧原文、来源metadata、实时项目、合法reader拒写与native账户域403、7凭据重发、SDK1.30四工具均通过，Lead另重算清单/31证据并启动目标读回原文和权限，再停下。正式实例未为此停启，快照原件保持。

本次借用R5 f已有runtime，非第三个全新安装；显式wheel安装曾重建其console launcher/RECORD，原before映射没有永久落盘，不能声称全部旧runtime字节保持；产品payload/provenance/旧R5数据与报告可证明保持，后续133文件基线已落盘。最初方法排序、非法reader fixture422及Lead错误管理路由404的失败均分别保留；合法fixture及实际路由的独立补验通过。范围和原始证据见[实际恢复](../experiments/memory-base-formal-restore/README.md)、[Lead复核](../experiments/memory-base-live/lead-formal-restore-2026-10-12.json)。
