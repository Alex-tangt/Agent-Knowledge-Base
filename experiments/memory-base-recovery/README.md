# 原生隔离恢复验收（#85）

问题：本轮范围授权、实时来源、自启动和完整工作区恢复，是否在真实 Windows / OpenViking 0.4.23 / MCP 1.30 链路成立？

假设：已有固定运行时下，两套独立 home 可以经真实原生链路完成范围约束、实时 Markdown 读取和完整冷恢复；原始字节、native ACL、身份角色及已保存 session context 应保持一致。

本预注册在运行前提交。依赖 #82 / #83 / #84 已由 Lead 按各票定义的 fixture/unit 范围验收关闭，真实原生覆盖由本票完成。冻结实现为 `d8a670905f93c86b36c6957b601fd8c2f02d0f45`；Lead 明确解阻开跑后才运行。脚本拒绝不同冻结 SHA、实现差异、实验脚本未提交改动、非指定 Python 或模型，以及继承的凭据环境。运行结果记录实现 SHA、脚本 commit/文件 SHA、模型 SHA 和实际依赖版本。

## 设置与边界

- 复用已安装的专用 Python 3.12 和 GGUF，核验全部 pins；不执行 pip、不宣称全新依赖安装成功。按安装器的非秘密配置格式初始化两个全新部署，调用真实生命周期及 Windows vault 发放独立凭据。
- 代码必须从本票工作树读取。源 home 在 C: 的专属验收目录；备份及空 data 目标在 D: 的专属基座目录。仅称跨逻辑卷，不推断物理磁盘。
- 不访问正式宿主配置，不停止正式服务，不写正式 home 或真实项目。fixture 是本票自己的 Git 项目，测试未提交改动。
- 所有组件进程由部署模块记录 PID、创建时间和完整命令；报告保存历史记录。SDK 实际 stdio 进程及手动 pythonw 启动器同样记录身份，结束只停本票实例。原始正文、日志、vault 身份及完整备份留在专属基座，报告不收录凭据或原文。
- 真实 HKCU Run 操作只管理本测试 home 的唯一值；前后比较整个 Run 基线摘要。手动执行 pythonw 启动命令只证明命令可运行，不证明实际登录触发。

## 预注册场景与通过条件

1. HTTP add/get/search/list：CRLF 和中文原文 UTF-8 SHA256 一致；显式接触日进入逻辑路径；范围改变重放不改变请求身份，日期改变返回 409。用旧序列化和真实 native write 构造 schema=1 capture，其请求哈希重放幂等。
2. 同一 person 的至少两种 host profile：未认证 401，reader 写 403；受限 host 的旧 URI、get/list/search 越界拒绝或不泄漏。让范围外原文更贴近 query，以真实 native find 的 limit=1 证明授权在候选截断前生效。
3. 本票 Markdown Git fixture：未提交改/增/删立即反映；revision 等于原字节 SHA256；expected_revision 不符 409；离线 503、恢复可读。Windows 实际 junction 403；文件超过 2 MiB 时 list/search 明确 413。冻结实现没有扫描文件数上限，本票不以字节上限冒充扫描条数上限。
4. 冷备份：独立复算 manifest 全部 size/hash、文件与目录清单；以只读 observer 在真实 `_cold` 边界采集独立 data 清单，断言快照逐文件等于该清单。sources.json 与 access.json 保留；备份结束恢复原运行集合；已有目的地预检和冷停后目的地并发出现两条失败路径恢复原运行集合。保留实际 SQLite queue 文件及完整 data。observer 不替换 native 服务、嵌入、复制或恢复行为。
5. 安装配置已存在、data 空的 D: 目标：损坏副本预检拒绝、不发布数据；完整备份恢复发布时，独立逐文件复算等于原冷备份清单。目标 python/model/source/ports 原字节保留；两 home vault namespace 与 root/host key 独立，恢复后 gateway key 重新签发；原 URI、正文、检索、项目实时读取及 gateway 范围有效。
6. 实际 native ACL：建立 restricted 对象，仅 gateway 有权限；普通 native user 在备份前后均被拒绝，ACL API 摘要相同；账户内 user_id/role roster 摘要一致，身份 API 原响应不收录。创建不自动 commit 的 session，恢复后 assembled context 摘要相同。queue 只检查真实文件、manifest 覆盖及 SQLite integrity，不推断待执行任务语义或 exactly-once。
7. 实际 MCP stdio：SDK 1.30 列出四工具，reader 可读取允许原文、越界 get/list/search 不泄漏，写入被拒绝。
8. 真实测试 home 的 autostart enable/status/disable 幂等，手动执行登记命令后全部组件 ready，清理后 Run 基线摘要相同。没有注销、重启或登录触发测试。

## 复跑

`run_native.py --help` 只展示参数；没有 `--execute --frozen <完整 SHA>` 不启动实例。

本次固定调用：

```powershell
. 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1'
Set-Location -LiteralPath 'C:\Users\Tan\.codex\worktrees\memory-native-recovery\Agent-Knowledge-Base'
$env:PYTHONPATH = 'C:\Users\Tan\.codex\worktrees\memory-native-recovery\Agent-Knowledge-Base'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe' -B experiments/memory-base-recovery/run_native.py --execute --frozen d8a670905f93c86b36c6957b601fd8c2f02d0f45 --model 'C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf'
```

每次生成新的 run id 和专属目录。失败立即记录对应检查和已清理状态，不修改被测实现。`results-<run-id>.json` 为固定结果；此目录不收录原文或秘密。

## 数据与结论

2026-10-12（Asia/Shanghai）单次运行 `20261011T172652Z-330308`：预注册 commit `7768344504c32967ef504fc6bf07af2692f205f4`，冻结实现仍为 `d8a670905f93c86b36c6957b601fd8c2f02d0f45`。exec session `60403` 正常结束、退出码 0；11/11 检查通过，最终清理通过。前一执行者脚本已保留并审查，未改任何被测实现。

固定证据：[原生结果](results-20261011T172652Z-330308.json)、[WAL 副本校核](verification-20261011T172652Z-330308.json)。环境为 Windows、Python 3.12.14、OpenViking 0.4.23、SDK 0.1.13、MCP 1.30.0、llama-cpp-python 0.3.36；完整 pins 在结果中。模型 SHA256 `ab9b81d9cd329c712eee379cf0068eabe6a5e2a01d0def61535eba9384085e2c`，开跑和收尾一致。

| 实际检查 | 判定与证据 |
| --- | --- |
| 两个独立 home | 六个动态端口、两个独立 vault namespace；目标 data 初始为空 |
| HTTP 四工具及原文 | CRLF/中文/Unicode 原文 SHA256 `fb293f648b23f906db14fd25c4e4d9ebdd878ed4673331494fa624be20ea5f63` 原样读回；显式接触日、scope 重放、日期冲突 409、缺认证 401、reader 写 403 |
| 旧 schema=1 capture | 用旧序列化真实 native 写入；同 request_id/hash 重放返回 exists |
| 范围过滤在 native top-k 前 | unrestricted native top-1 为范围外 URI；native allowlist 与 reader HTTP limit=1 返回允许 URI；受限 list 恰有两个原文，越界 get/list 403 |
| 实时项目 Markdown | 未提交改/增/删立即反映；原始字节 revision、409、离线 503、恢复可读、超过 2 MiB 的 list/search 413 |
| 实际 Windows junction | get/list 403，清理只移除本测试 junction，外部目标保留 |
| native ACL/身份/context | restricted 对象对普通 native user 返回 403；账户 user_id/role、ACL、session context 摘要在恢复前后相同 |
| 完整冷备份 | 85 文件、50 目录，独立复算全部 size/hash；与冷停时完整 data 清单相同；sources/access 保留；成功及两条失败路径恢复原运行集合 |
| 空目标恢复/损坏拒绝 | 损坏副本预检拒绝，无目标数据发布；完整副本发布时逐文件相同；保留目标原配置，重新签发 gateway key；原 URI、正文、检索、范围、项目读取、native ACL/context 复验通过 |
| 实际 MCP stdio | SDK 1.30 列出四工具；reader 允许 get/list/search，越界读取及写入明确 403 |
| 实际自启动往返 | enable/status/disable 幂等；手动执行登记 pythonw 命令后三组件 ready；HKCU Run 基线摘要还原；最终只停止两个测试 home 自有进程 |

结论：上述具体样例在真实 native 服务、真实本地 GGUF 嵌入、实际 Windows vault/junction/registry 与 MCP stdio 上通过。没有新增 LLM/摘要生成。原始正文、凭据和日志未复制到 JSON 报告。

## WAL 校核与覆盖限界

预注册脚本对 queue 使用 `immutable=1`，实际 4096 字节 base DB 的 `integrity_check=ok`、表数 0；该方式忽略 WAL，不能代表完整逻辑 SQLite。原完整备份真实包含 `queue.db`、3,361,952 字节 WAL、32,768 字节 SHM。经 Lead 明确认可，随后运行 `verify_artifacts.py`，在新的专属 probe 中逐字节复制三文件，使用普通 SQLite 打开副本后检查 WAL：`integrity_check=ok`、3 个表、journal_mode=wal。**这是 post-hoc 补充校核**，没有改正跑脚本或重跑原生实例；原 snapshot 全量 inventory/manifest 在校核前后相同。

该校核调用如下；输出固定证据禁止覆盖。对一次新的 native run 使用对应新 run-id 的结果文件。

```powershell
& 'C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe' -B experiments/memory-base-recovery/verify_artifacts.py --report experiments/memory-base-recovery/results-20261011T172652Z-330308.json
```

没有检查失败。以下是未覆盖或不能外推的范围：

- 复用已安装运行时，只验证 pins 与独立 home 配置；没有验证全新依赖安装或 wheel 的独立运行时安装（该边界由 #86 另行复现）。
- 两个逻辑卷之间备份/恢复，不推断两个物理磁盘。
- session 只覆盖一个明确禁用 auto-commit、含一条 user message 的实际 context API 摘要；queue 覆盖全部文件含 WAL/SHM 与副本完整性，不主张待执行任务、恢复后任务消费或 exactly-once 语义。
- 实际 HKCU Run 往返及登记命令手动执行通过；没有注销、重启或实际登录触发验收。
- 文件 2 MiB 字节上限真实返回 413；没有实现/验证扫描文件数上限。
- 原生验证通过不代表性能、并发压力或所有 ACL/来源故障形态都已覆盖。

## 资产与复原

原文、实际 data/store、完整冷备份、损坏副本及 WAL probe 均保留，不递归删除或归档此工作树。测试实例最终停止，两个 `processes.json` 均为空，Run 基线还原；收尾按 17 条历史 PID/创建时间/完整命令做 CIM 匹配，存活匹配数为 0，包含 stdio 与 pythonw 启动器。实验待提交文件与两测试 namespace 的实际凭据做无回显比较，秘密审查通过。正式实例未停启、未改配置。

- 源 home/fixture：`C:\Users\Tan\.memory-base-acceptance\20261011T172652Z-330308\source`、同层 `project`。
- 目标 home：`D:\python_work\work2026-4\memory-base-recovery-assets\20261011T172652Z-330308\target`。
- 完整备份：同层 `snapshot`；manifest SHA256 `11bab9545ff8c245d9b7c4c8ac0ad5e898b4dbcf1c0fa11ef93eda98cff4864d`；冷停 data 清单摘要 `ce678e99b6d0156b3ddf4594456a790fa03692b4da091274b5be032a61be9368`。
- 故意损坏副本：同层 `damaged-copy`；失败路径留下的空目录 `cold-copy-race`。
- WAL 校核副本：同层 `wal-probe-b8a7f416`；未触碰完整 snapshot。
- 进程完整命令/PID/创建时间的历史在固定 JSON 的 `process_history` 中；当前 vault 凭据按两个 namespace 留存，完整备份不含外部 vault 明文。

复原优先使用保留的完整 snapshot；复算 manifest 全部 size/hash 后，按 README 固定 native 调用可生成一组新的隔离实例与结果。不要向当前非空 target 覆盖恢复，不把有基座的目录当作垃圾删除。
