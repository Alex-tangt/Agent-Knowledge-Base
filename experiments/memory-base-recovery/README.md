# 原生隔离恢复验收（#85）

问题：本轮范围授权、实时来源、自启动和完整工作区恢复，是否在真实 Windows / OpenViking 0.4.23 / MCP 1.30 链路成立？

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

```powershell
. D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1
$env:PYTHONPATH = 'C:\Users\Tan\.codex\worktrees\memory-native-recovery\Agent-Knowledge-Base'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe' experiments/memory-base-recovery/run_native.py --execute --frozen <Lead冻结的完整SHA> --model <已登记GGUF绝对路径>
```

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

尚未运行。2026-10-12 准备审查结论：前一执行者脚本已保留并补强；实际 unrestricted native top-1 范围外结果与 native allowlist/HTTP top-1 范围内结果成对核验，避免仅依赖 stub。静态语法与 `--help` 是准备检查，不能算真实验收通过。此处在真实冻结验收结束后补充结论与基座路径，失败、跳过和覆盖限界分别列出。
