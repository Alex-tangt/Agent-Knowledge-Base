# #86 独立 memory-base wheel 验收

**产品验收未通过，#86 保持 open 等待安装修复后复验。** 冻结实现 `d8a670905f93c86b36c6957b601fd8c2f02d0f45` 可产生完整 wheel；该 wheel 的独立安装、仓库外导入、CLI help 和真实 MCP 四工具协议均通过，但安装后的 wheel 无法通过默认 `memory-base install` 或 `--fresh-runtime` 把自身部署到新 runtime。未修改实现、地图、正式实例或宿主配置。

## 问题与假设

问题：当前 `memory_base` 是否已经是能够独立交付的安装包，不依赖旧 `memory_agent` / `ragcore` / `legal_web` 或 `experiments` 源码？假设分为两层：wheel 内容与运行入口可独立安装；安装器能从已安装 wheel 引导目标 runtime。第一层通过，第二层存在确定的控制流缺陷。

原始任务卡和 [#86](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/86) 中的旧 SHA `b289afa` 已按 Lead 开工指令替换为上述完整冻结 SHA。预注册为 [PREREGISTRATION.md](PREREGISTRATION.md)，先提交 `fc0b0e9dcb418b3b8b6cf8d93247198c541eb162`，再运行。

## 设置与证据边界

Windows / Python 3.12.14。主树 venv 先激活，绝对主 Python 仅构建包与运行实验编排。被测源码通过 `git archive` 从冻结 SHA 导出到实验 store，在导出副本构建，未在实现目录留下 build/egg-info。

新实验 venv 使用 `--system-site-packages`，只安装本次 wheel（`--no-deps --no-index --ignore-installed`）。它显式复用 `C:\Users\Tan\.agent-memory-base\runtime\Lib\site-packages`，在新 venv 的 `.pth` 中把该只读提供方放在本地 site-packages 之后、base 系统 site-packages 之前，并仅处理正式依赖的 `pywin32.pth`。**没有在全新依赖环境安装完整 OpenViking 栈，没有 pip 修改正式 runtime。** FastAPI 0.143.0、Pydantic 2.14.0、MCP 1.30.0 的实际模块 origin 均在正式依赖提供方；12 个 `memory_base` 模块的实际 origin 均在实验新 venv。

清空 `PYTHONPATH` 并移除继承的 `MEMORY_*` 配置后，分别从 `C:\Users\Tan\AppData\Local\Temp` 和 `C:\WINDOWS\System32` 验证。`__main__` 按它的 CLI 副作用，用 `--help` 导入并核对预期退出 0；其它 11 个模块直接 import。两个目录均检查 module CLI、console CLI 和 MCP CLI help。

MCP 测试启动的是本次 wheel 的实际 stdio server，用实际 SDK client 完成 initialize、list_tools 和四次 tools/call。HTTP 端为仅含合成数据的 loopback stub；该证据证明工具目录、schema 与 HTTP 透传，真实 OpenViking 数据行为由 #85 承担。工具集合恰为 `memory_add`、`memory_get`、`memory_list`、`memory_search`，四份 schema 都为 `additionalProperties=false`；协商协议 `2025-11-25`。

安装器观察明确捕获而不执行 subprocess，模拟直接版本已满足，阻止凭据操作。这能复现真实入口的分支/返回值，不能证明上游依赖安装成功。POSIX 未验；当前实现非 Windows 的首次凭据供应依赖进程环境，尚无本次持久化验收证据。

## 数据与判定

| 检查 | 结果 | 证据 |
|---|---|---|
| wheel inventory / 源码字节 / RECORD | PASS | 20 个 ZIP 成员；12 个 Python 模块、3 个包数据文件、5 个 dist-info 文件；不含旧三模块或实验 |
| 新 venv `--no-deps` 安装 wheel | PASS | wheel 42,831 bytes；本地安装的 distribution 与 module origin |
| 两处仓库外 CWD 的全部模块及 CLI | PASS | 24 次模块检查、6 个外部 CLI help 命令，均退出 0；未载入排除命名空间 |
| 直接 pin 一致 | PASS | project、wheel METADATA、requirements、constraints 对应条目和 formal runtime 六个直接版本一致 |
| 真实 MCP stdio 四工具 | PASS | initialize / list_tools / 4 次 tools/call，经 loopback stub 验路由与参数 |
| 安装器解释器边界观察 | PASS（观察完成） | 同解释器配置分支可达；跨解释器/fresh 分支拒绝，均未执行 pip |
| formal runtime 只读 `pip check` | PASS | 退出 0：`No broken requirements found.` |
| wheel 默认新 home 与 fresh-runtime 部署 | **FAIL** | 两案 CLI 退出 1；没有请求安装 `memory_base` 本身 |

主结果为 [results.json](results.json)，`package_protocol_overall=PASS`，**`overall=FAIL`**。首轮测试环境引导失败在 [initial_environment_probe.json](initial_environment_probe.json)；R1 配置修正和补充判据的时间/提交边界如实记在 [METHOD_AMENDMENT.md](METHOD_AMENDMENT.md)。首轮不是 wheel 内容缺陷，且未被抹去。第二轮原始包/协议结果保留在 store 的 `results.json`，最终汇总另加默认 CLI 产品闸门；不得仅引用第二轮原始 PASS 替代整体产品结论。

## 最小复现与交给 Lead 的输入需求

已安装 wheel 的 `memory_base.deploy.main(["install", "--home", <empty-home>])` 以及再加 `--fresh-runtime` 都返回 1，stderr 为：

```text
memory-base: Installing into a new runtime requires the source checkout; this package was installed as a wheel
```

捕获的调用只包含当前解释器 `-m venv <home>/runtime` 和目标解释器 `-m pip install --no-compile -r <installed-package>/requirements.txt`；没有 `-e` 或 `.whl` 安装调用。拒绝发生在 `deploy.install` 的 wheel 分支（冻结 `memory_base/deploy.py:173`），目标 Python 与当前 Python 不同且 wheel 不含源码 `pyproject.toml`。即使目标上游依赖都已满足，默认入口仍无法把包自身装入目标 runtime。

这属于产品安装缺陷，交给 Lead / 后续修复票；本实验不修实现。可供 Lead 决策的修复输入是一个明确的本地包产物接口（例如 wheel 路径及其 hash），让安装器能够在目标解释器安装同一 `memory_base` 产物，并在写部署配置前验证目标解释器从其自身 site-packages 导入该包。当前实际 wheel 路径与哈希在下一节；是否新增参数、从已安装包获取产物或改默认 runtime 策略由 Lead 决定。

可在当前保留的实验 venv 上廉价复现，命令只对实验 store 写合成结果，subprocess/凭据操作全部捕获：

```powershell
& 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1'
$env:PYTHONPATH = $null
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location 'C:\Users\Tan\AppData\Local\Temp'
& 'C:\Users\Tan\.codex\worktrees\memory-package-acceptance\Agent-Knowledge-Base\experiments\memory-base-package\store\run-20261011T172533Z\venv\Scripts\python.exe' 'C:\Users\Tan\.codex\worktrees\memory-package-acceptance\Agent-Knowledge-Base\experiments\memory-base-package\reproduce_default_install.py' --store 'C:\Users\Tan\.codex\worktrees\memory-package-acceptance\Agent-Knowledge-Base\experiments\memory-base-package\store\run-20261011T172533Z'
```

## 资产与复原

两次测量基座完整保留在本工作树 `experiments/memory-base-package/store/`，局部 `.gitignore` 仅禁止提交，并不允许清理或归档。第二轮为 `run-20261011T172533Z`，第一轮为 `run-20261011T172343Z`。

| 资产（相对实验目录） | SHA-256 |
|---|---|
| `store/run-20261011T172533Z/wheels/agent_memory_base-0.1.0-py3-none-any.whl` | `378be72647e7df6d9f0c86010b97afd99c08322b0fee0f892c100b3372a1c722` |
| `store/run-20261011T172533Z/source.zip` | `3427b22e05712c68288f8907422970a6cd0a911d2f46859ae77320b07cb6c06c` |
| `store/run-20261011T172533Z/venv/pyvenv.cfg` | `695b11351f54ef9f38610c806513ab4630064fc883b6fd3bdfe6ef12fb8b5d1d` |
| `store/run-20261011T172533Z/default-install-reproduction.json` | `04ce008b2778f52e8af6b2326c34b426dc5dca5d40225f837e83d7edb9c34bcb` |
| `results.json` | `96ce9817a5945085245a2fc49109c4a84949632295748d83fe7ba2f73da6b5ff` |
| `initial_environment_probe.json` | `7dab979a71685fefa16800c64b70edb205d93d345e745b5330cd4e5b1ccfaeb4` |

完整命令、inventory 单文件 hash、`.pth` 与提供方 hash、stdout/stderr、MCP 请求与合成响应都在 JSON。没有真实秘密、正式配置全文或正式记忆正文。

完整复跑从本工作树执行，会创建新的 store run 并保留旧资产。当前冻结实现下末项预计 FAIL，编排退出 1；不能将该命令称为通过的安装验收：

```powershell
& 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1'
& 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe' 'experiments\memory-base-package\run_acceptance.py'
```

wheel ZIP 时间字段会影响重建产物的整体 hash；本次具体产物身份以上表为准，跨次源码一致性使用 inventory 中单文件 hash 核对。已有资产不需要为再次查看/独立复核而重建。AOCI 在本实验中未调用、扫描或同步。
