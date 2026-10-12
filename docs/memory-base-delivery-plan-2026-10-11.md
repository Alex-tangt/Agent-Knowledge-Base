# 个人记忆基座交付计划与任务板

2026-10-11。owner 已授权持续自主推进；范围仍遵守 ADR-0032、S1–S3 和旧引擎冻结决定。本文记录完整目标与当前执行单元，单项完成不代表整个产品完成。

## 目标与验收

交付一个在家用 PC 上独立安装、运行、恢复的个人内容仓库，多个 agent 共享同一份记忆。继续使用固定 OpenViking 0.4.23，掌握共同入口、数据契约与部署；源码研究的裁剪建议见 [综合报告](architecture-memory-base-source-study-2026-10-11.md)。

| 要求 | 当前证据 | 本轮完成判据 |
|---|---|---|
| 虚拟内容原样保存、多宿主共享 | Bubble / Codex 已真实通过 | 旧 URI、幂等键和逐字节正文继续成立；新接口不破坏消费者 |
| 项目只读映射 | #85 增改删/失联/恢复与正式当前文件读回通过；#94 来源恢复通过 | 登记一个真实项目；从当前文件读回并带内容哈希；增改删、未提交变化、失联与恢复可区分；不复制项目正文作第二主库，不写源目录 |
| 路径表达日期、类型与项目范围 | #85/#86/#90 范围、日期及版本读回通过 | 接触日明确由提交方提供；未知日期保留 unknown；list/search/get 在同一授权范围，越界被拒；至少两种宿主身份实测 |
| 历史与完整恢复 | #94 实际67文件快照空根恢复与权限/凭据重发通过；自定义 ACL 对象未存在于该快照 | 自动留存提交回执；备份包含正文、ACL/身份状态、来源登记、路径权限和必要控制状态；凭据另行重发；干净数据根恢复后重验正文与拒绝路径 |
| 安装、生命周期与错误 | #86 R5 默认/fresh 独立安装及默认首次运行通过；Windows 实际登录触发仍未测 | 更新后的独立包可安装；状态/索引/来源失败可见；停启限于本部署进程；POSIX 的可复现边界写清 |
| 产品计划与退出 | 固定源码研究与产品计划已保存；发布前复核 #95 执行中 | 保留/剥离清单、模块来源、升级、迁移、数据出口、恢复与维护责任有可执行文档 |

学习、偏好晋升和自动经验演化仍为后续层。#78 涉及人的批准，不以 owner 的开发授权替代对某一偏好的批准。没有真实任务及反馈证据时不启动完整 MemOS 学习管线。

## 实现决定

1. **读取真实项目文件**：显式来源登记，允许的 Markdown 子树，规范路径、字节哈希、预期版本检查。先在查询时确定性扫描并提供词法召回，标明检索方法和扫描完整性；不先导入第二份可编辑正文或搭建第二个向量服务。是否扩充语义召回由真实查询缺口决定。
2. **共同路径接口**：旧 capture URI 保持有效；逻辑路径按接触日/类型组织，项目按项目名/相对路径组织。默认共享读取，配置可以收窄路径；writer 仅追加 capture，映射来源始终只读。日期缺失不把接收日伪称接触日。
3. **兼容消费者**：保留 add/search/get 的旧参数与默认行为；增加可选 scope / expected_revision，并增加无需向量索引的 list。HTTP/MCP 共用实现；运维登记与恢复经本机 CLI，不发给普通 agent。
4. **完整备份**：首期采用受生命周期锁保护的停服备份，保存 OpenViking 整个数据目录以保留 ACL 等状态，同时保存 `sources.json`、`access.json`；不以 OVPack 代替完整备份。恢复到独立空数据根，核对清单和哈希，保留目标部署自己的路径与端口，并重新发放凭据。
5. **本机运维**：来源登记运行时重读；路径权限经本机 CLI 显式配置，省略保持共享默认，空数组明确拒绝，变更后重启 gateway。Windows 登录自动启动单独选择，仅管理当前用户、当前 home 的启动项；登记完成和实际登录执行分别验收。

以上是可逆的阶段 2 扩充，具体持久格式与恢复约束同时记录到 ADR-0032。OpenViking 原生向量/文件存储继续复用；不提前拆 RAGFS。MemOS 的可用接缝已存档，按具体任务选择，而非组成第二个原文权威。

## 任务板

Lead 负责本文件、ADR、地图同步和集成验收；执行者各自工作树且写域不重叠。GitHub issue 是执行索引，原生 blocked_by 为依赖权威。

| 单元 | 状态 | write scope | blocked_by | 验收锚点 |
|---|---|---|---|---|
| 两仓源码研究 | 已提交 `747d150` | `experiments/memory-source-study/`, 综合报告 | 无 | 固定 SHA；226 个固定源链接、41 个本地链接通过；有限探针不等于完整运行 |
| [#82 只读映射与路径查询](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/82) | 实现已合入并关闭，最终单元 `b289afa`；原生证据由 #85 补齐 | `memory_base/sources.py`, `paths.py`, `gateway.py`, `upstream.py`, `mcp_stdio.py`, 对应新增/原 gateway 单测 | 无 | 上表映射与范围判据；保留原接入回归 |
| [#83 完整备份恢复与运维登记](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/83) | 实现已合入并关闭，最终单元 `474af7b`；原生证据由 #85 补齐 | `memory_base/backup.py`, `deploy.py`, 对应新增/原 deploy 单测 | 无；集成验收依赖映射格式稳定 | 冷备份哈希；空数据根恢复；ACL/登记/凭据边界；只管理自有进程 |
| [#84 Windows 登录自动启动](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/84) | API 与 CLI 已合入并关闭，最终单元 `55be228`；真实登记由 #85 验证 | `memory_base/autostart.py`, 对应单测；CLI 由 #83 单写者接入 | 无 | 命令路径解析、归属冲突、幂等登记；真实登录另列限制 |
| [#85 原生隔离验收](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/85) | 冻结 `d8a6709` 单次 11/11 通过；Lead 独立清单及恢复目标复核通过 | `experiments/memory-base-recovery/` | #82、#83、#84 | 冻结 SHA；真实路径过滤、变更、离线、完整备份、空根恢复、native ACL、SDK1.30 MCP；WAL 副本事后校核单列 |
| [#86 独立包验收](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/86) | 冻结 `45296a5` R5默认/fresh两个独立栈通过；默认首次start168.13秒、12 native checks；Lead53原件/产物/当前清理复核通过 | `experiments/memory-base-package/cold-final-2026-10-12/` | #91（已通过） | 175 own-site distributions/173约束、默认180秒/组件、真实HTTP/MCP、任意cwd；限Windows短路径/已有pip缓存与GGUF |
| [#87 标准启动请求解析修复](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/87) | 已合入 `d8a6709` 并关闭 | `memory_base/deploy.py`, 对应单测 | 无 | 正式 runtime 请求 200/422；gateway 重启后 MCP 原文及 revision 一致 |
| [#88 正式 MCP 四工具回归](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/88) | 已合入 `1e78f0d`，Lead 正式文章/视频复跑通过 | `experiments/memory-base-live/mcp_probe.py` | 无 | 旧 Bubble URI/hash，实际 stdio 四工具、列举、版本读回、只读写拒绝；无新 LLM/总结生成 |
| [#89 wheel 专用 runtime 安装修复](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/89) | 实现 `9252d52` 已合入；#86 R5真实默认/fresh安装、own-site来源及全量闸门通过 | `memory_base/deploy.py`, 对应单测 | 无 | 本地artifact及副作用前校验、目标来源、源码兼容；实现者fixture回归与独立完整安装分别保留 |
| [#90 Bubble 四工具与版本接入](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/90) | 冻结候选 `0a86cb1` 已应用正式目录；Lead build、68/68 应用测试、5/5 host 测试及实际网关/SDK 复验通过，后端健康 | Bubble 候选 `server/memory-base.ts`, `server/memory-tools.ts`, `server/agent.ts`, `server/tests/memory-base.test.ts`；本仓 `experiments/memory-base-bubble-bridge/` | 无 | 真实 pi SDK 的四工具、项目 scope/list 分页/revision 往返、错误可区分；原四文件基线先核对并保留备份；读回旧总结；未使用新的付费模型 |
| [#91 首次冷启动](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/91) | 实现 `dc3168d`、记录 `45296a5` 已合入；Lead833单测、harness10/10及#86新环境默认首次运行通过 | `memory_base/deploy.py`, 对应单测；`experiments/memory-base-cold-start/` | 无 | 安装限目标own-site Lark准备（独立600秒预算，探针累计341.750秒；R5实际自动准备）；服务默认180不变；上游/正式缓存未改 |
| [#92 Bubble 笔记写入意图](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/92) | 冻结 `36f9bf2` 两文件已备份应用；Lead build、73/73 应用及 5/5 host 闸门、正式工具入口与只读 pi/网关通过；正式 manager 重启健康 | Bubble 候选 `server/memory-tools.ts`, `server/tests/memory-base.test.ts`；本仓 `experiments/memory-base-note-intent/` 和 Lead live 记录 | 无 | 否定/询问/引用和撤回不调用add；明确中文/英文保存、原文和旧key兼容；有限规则拒绝未匹配表达并提供明确示例，不冒称通用语言理解 |
| [#93 Bubble Windows 状态发布](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/93) | 冻结`ba8b5b5`；Lead组合#92为`8e13fae`，build/73应用/13host全通过；实际三scripts已备份应用，自有manager重启健康 | Bubble `scripts/local-backend.mjs`, `host-state.mjs`, 对应scripts测试；本仓`experiments/memory-base-host-state/` | 无 | rename短错误至多暂停200ms，保持原子JSON/同步顺序；持续失败关闭自有child并清理本instance；原120s就绪上限不变，原FAIL保留 |
| [#94 正式快照隔离恢复](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/94) | 固定67文件/39目录实际快照→空data恢复、三原文/来源/权限/凭据重发/MCP通过；Lead独立31原件/清单及实际读回/权限/清理通过 | `experiments/memory-base-formal-restore/`，隔离`.mb94/a`；借R5 f runtime，重装launcher/RECORD及缓存副作用声明 | #86（原生边，已通过） | 原快照不变，target已停；所有方法FAIL保留；不冒称第三新栈、任意ACL对象或真实登录/设备验证 |
| [#95 交付分支复核](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/95) | 固定`4dadc40`范围只读核未发布文件/历史的凭据与实际内容，准备草稿交付 | `experiments/memory-base-publication/`，Lead负责PR与地图 | 无 | 不输出秘密、不改历史/用户文件/正式实例；当前树与历史分开、判据/漏项明确；发布前复核根venv闸门/产品freeze/实际证据链接 |
| 产品化计划、实际项目接入与复跑 | 执行中 | Lead 文档、地图与正式接入记录 | 上述实现和验收单元 | 冻结版本全量单测、已有 harness/冒烟、正式实例与旧消费者兼容 |

## 完成审计

收尾按整个需求表逐项列出已通过、限制和仍待做事项。不能用单测通过代替真实宿主接入，不能用同机重启代替空数据根恢复，不能用词法扫描冒称向量或完整语义理解。AOCI 于 2026-10-12 按 owner 决定卸载，接入、索引和日志已移除；开发直接使用源码、测试与文档。

**Lead 冻结验收（2026-10-12，`d8a6709`）**：802 passed、5 skipped；确定性 harness 10/10 预期匹配；opencode server 缺席，接入冒烟为 SKIP。正式 Python 3.12 的新 wrapper 与旧 `-m` 请求分别通过 200/422 校验，原文空格及 CRLF 保持一致；正式 gateway 重启后记忆 MCP 读回一致。原有用户未提交文件保留，执行写域无遗留改动。#85、#86 和正式来源/启动交付尚未据此标成完成。

**后续真实交付（2026-10-12）**：#85 原生隔离与恢复 11/11 通过，Lead 独立重算 85 文件/50 目录清单，再启动恢复目标核对原文、范围及 native ACL 拒绝后停下。#88 的正式 SDK1.30 四工具、分页/版本及只读拒写复跑通过，旧 Bubble 文章/视频 URI 和哈希不变。正式 `agent-kb` 来源、实时 revision 变化和当前用户登录启动已核验；正式冷备份 67 文件全部核验且不含已知凭据明文，随后服务就绪。实际登录触发、DSH/opencode 对话及 POSIX 仍未据此标成通过。结果在[真实接入记录](../experiments/memory-base-live/README.md)。

**独立安装复验（2026-10-12）**：`9252d52` 全量单测 824 passed / 5 skipped，harness 10/10，opencode server 缺席仍为 SKIP。#86 的前三次新环境安装失败记录保留（下载代理处理、Windows 深路径）；短目录 R4 的默认安装、依赖固定版本、目标自身来源与 pip check 已通过，但原生上游启动 300 秒未就绪，整体仍 FAIL，fresh 安装判据单独继续。正式依赖与 R4 的 Lark SDK 同为 1.7.3，正式 RECORD 有 10,727 个 pyc，R4 安装未预编译；直接上游启动的 15 秒栈停在该 SDK 导入。该证据仅定位排查方向，尚未证明根因或修复。

该 R4 试验的环境清理还额外设置了 `PYTHONDONTWRITEBYTECODE=1`；诊断中发现后另行区分纯导入和普通部署，不能把它的原生 FAIL 直接推广为所有新安装失败。原报告保留，后续运行必须单独登记条件。

### 需求逐项核验（尚未完成）

以交接 S1–S3、ADR-0032 D9–D16 和本文目标为当前决定。2026-10-09 的 DB 草案在字段/schema 方案上已被路径与原文决定替代，其 A–G 所表达的功能要求仍逐项检查；不以此删除跨设备要求。

| 要求 | 当前判断与证据 | 仍须取得的证据 |
|---|---|---|
| S1 / I2 / A：人的共同记忆、新 agent 无迁移读取 | 正式 Bubble 与 Codex 读取同 URI，另 reader 身份原生 stdio 读回；真实宿主轨迹及哈希已保存 | DSH/opencode 登记与规约存在，其实际对话未验证 |
| S2 / E：capture 与实时只读项目共同寻址 | #85增改删/失联恢复/越界、#90/#92实际scope/分页/版本、R5新包capture通过；#94真实快照来源原字节恢复，实际读取当前项目hash，Lead再读当前README | 首期项目词法查询不冒称语义检索；项目正文仍归真实repo |
| S3 / I1：原样保存及回执追溯 | 正式文章/视频一致、重复exists/变更拒覆盖；R5新包Unicode/CRLF/hash/幂等；#94实际快照三原文及来源metadata读回，Lead再读三receipt一致 | 首次安装guard/不合法fixture/Lead错误API路径的FAIL均保留，不能把404/422当权限成功 |
| I3 / F：共同写入合同、只读源权限 | HTTP/MCP共用gateway，writer仅追加capture；#85/R5拒写和范围、#92笔记意图保护通过；#94实际合法reader403无新增、native账户域403，目标7凭据重发不同 | 有限命令规则不覆盖任意自然语言；正式快照未含自定义access/受限文件ACL，不能假称恢复该不存在对象 |
| I4 / D：故障不是空记忆 | 正式上游503、未认证401、reader403；#90/#92版本/目录变化明确反馈；R5权限/版本拒绝通过；#93补状态发布有界重试/失败关闭自有child，组合13host通过并实际部署 | 原真实EPERM、注入RED与恢复路径分别保留，不将fault injection声称原生锁复现 |
| B / C：第二台设备与可见延迟 | 同机两个真实宿主共享已验；2026-10-12 ADB 枚举无连接设备，不能据同机结果宣称两台设备通过 | 实际手机/第二设备读写互查、可见延迟；只使用既有私有网络 |
| G / D15：完整备份、空根恢复 | #85坏清单/空根/权限/重发通过；#94实际67文件/39目录快照空data恢复、真实原文/来源/权限/MCP通过；Lead独立重算及启动目标复核 | 这是该固定快照恢复点；不承诺任意时刻恢复；共享f launcher/RECORD重装副作用及旧before取证缺口明确披露 |
| D9 / D16：安装、运行与无人值守启动 | 正式三服务ready，Run归属/同命令手动执行通过；R5新wheel默认/fresh安装、默认首次native168.13秒通过；清理无残留 | 真实注销/登录未测；POSIX非已交付平台；初次SDK准备仍有数分钟成本 |
| D4：独立产物、退出与维护 | wheel排除旧三包；固定依赖/来源/迁移/出口见计划；833单测及R5独立native、#94实际快照恢复通过；Bubble#90/#92/#93已实际部署 | 跨设备/真实登录和DSH/opencode实际对话未验，不能宣称整个目标完成 |

本表用于决定下一步，不替代原始证据。数据出口可以通过四工具 list/get 读取原文与引用；native 冷备份是恢复介质，不能称通用跨引擎格式。当前工作集测点为三服务树 273.97 MiB，仅是闲置 Windows 工作集相加，非峰值或私有内存，详见真实接入记录。

尚缺的第二设备、真实登录和 DSH/opencode 对话按[剩余真实验收步骤](memory-base-remaining-acceptance.md)取得证据。步骤本身不计为通过，不强制注销用户，不用同机模拟或配置登记替代真实行为。

**早期标准环境启动失败（2026-10-12，修复前）**：#86 对从未启动的 fresh home 去掉额外 bytecode 禁止后，仍在原 300 秒上限未等到 upstream 健康；start 总耗时 314.52 秒包含 embedding 启动，整个验收 331.88 秒。gateway 与后续读写/MCP 没有运行，owned stop 退出 0，端口与进程无残留，旧 FAIL 原字节未变。该结果当时证明普通冷启动仍阻塞部署，不能据安装成功关闭 #86。随后 #91 修复与 #86 R5 默认首次运行通过，当前状态以上方逐项核验和 R5 原始记录为准；此段保留失败历史，不表示当前仍阻塞。
