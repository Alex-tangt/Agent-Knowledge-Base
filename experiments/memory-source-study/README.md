# OpenViking / MemOS 固定源码研究（2026-10-11）

目的：理解两仓实际的存储、原文保留、检索、记忆加工、任务、权限和恢复边界，为本仓个人记忆基座提出架构建议。本文不是选型跑分，也不宣布生产迁移或模块集成完成。

## 基线与方法

| 对象 | 本地只读研究路径 | 固定基线 |
|---|---|---|
| OpenViking | `D:\python_work\work2026-4\OpenViking` | `v0.4.23` / `df32bf6e50a40843438f9491a26069ca4bd08f1f` |
| MemOS | `D:\python_work\work2026-4\MemOS` | 拉取时 main 的 `a7367d07e55db61099f7b4e2c1108bc5831a24f3`，随后 detached HEAD；不是声明此提交为稳定发布 |
| 本仓现状 | `D:\python_work\work2026-4\Agent-Knowledge-Base` | 以 `f77c811` 的部署交付为对照；保留用户的 AOCI 等未提交工作 |

两个上游均为独立 sibling checkout，保留提交历史；不作为本仓子模块或工作树。本轮不安装上游依赖，不建立 AOCI 索引，不安装 codegraph，不改生产服务或真实记忆。源代码、官方测试与配置优先于宣传描述。报告中的 GitHub 链接固定至上述提交。

阅读按实际链路分工：OpenViking；MemOS Python 主实现；MemOS TypeScript 本地实现。后者同仓有新版 `apps/memos-local-plugin`、旧版 `apps/memos-local-openclaw`、旧形态 `packages/memos-core`，不能混成一个已发布且同构的运行时。各报告标明覆盖范围和未验证处。

证据分三类：**源码事实**（已定位实现）、**隔离探针**（有限输入/替身或内存数据库验证）、**既有实测**（本仓此前真实服务验收）。阅读上游测试不等于本次已经运行上游套件；本轮不对两个完整产品宣称运行通过。

## 报告

- [OpenViking 源码报告](openviking.md)
- [MemOS Python 源码报告](memos-python.md)
- [MemOS Local 源码报告](memos-local.md)
- [架构综合建议](../../docs/architecture-memory-base-source-study-2026-10-11.md)

## 当前部署与源码的对应检查

运行只读脚本 [provenance.py](provenance.py)，结果 [provenance.json](provenance.json)：固定 checkout 与当前已安装 OpenViking 0.4.23 的 **704 个共有 Python 文件归一化文本全部相同**；安装包共 705 个 Python 文件，额外的文件是构建生成的 `_version.py`。因此本轮 Python 链路分析对应当前安装的 Python 实现。

此检查没有比较本地原生库与 Rust/C++ 构建产物，也不是功能或恢复测试。未 import 上游模块、访问生产数据或网络。

MemOS Python 报告附标准库 AST/fake-provider 探针，验证选定 raw 方法的正文传递，不覆盖实际包装配/数据库。MemOS Local 报告附 SQLite 3.53.1 内存探针，验证选定迁移的 FTS 与向量更新边界，不覆盖 TypeScript 产品运行。两份探针的完整源码、结果和范围都保存在对应报告中。

复跑（PowerShell，从本仓根目录）：

```powershell
. .\venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING='utf-8'
python experiments\memory-source-study\provenance.py
python experiments\memory-source-study\check_links.py
```

[check_links.py](check_links.py) 及 [link-check.json](link-check.json) 检查五份文档的本地目标、固定 GitHub SHA、源码行号范围和 reference 定义；不把链接存在视为论点已被证明，也没有联网检查 GitHub 页面可达性。

## 与已有验收的关系

[真实接入验收](../memory-base-live/README.md) 已覆盖 Bubble 实际总结写入、跨会话读取、真实 Codex CLI MCP 读取、正式服务重启、嵌入并发与显式故障。本轮复用这些明确范围的证据，不重复破坏性故障注入。它们不证明项目映射、来源新鲜度、范围级权限和完整异盘恢复已完成。

此次报告是阶段 2 的架构研究材料；架构建议仍待后续任务收敛为具体实施，不自动改写 ADR-0032 的 proposed 状态或旧实验的验收口径。

报告保存于本仓。2026-10-11 owner 授权持续推进后，研究材料作为后续实现的固定依据；执行与验收见 [交付计划](../../docs/memory-base-delivery-plan-2026-10-11.md)。AOCI 按 owner 的按需读取策略使用，提交后的独立维护按既有钩子排队；不改动用户的索引、钩子及其它未提交工作。
