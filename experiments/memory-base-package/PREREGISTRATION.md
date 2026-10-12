# #86 独立包验收预注册

登记日期：2026-10-12。本文件先提交，再开始构建、安装与验收。

## 问题与冻结对象

冻结被测实现为 `d8a670905f93c86b36c6957b601fd8c2f02d0f45`。Lead 的当前任务指令将任务卡和 issue 中旧的 `b289afa` 替换为该完整 SHA；后续实验提交不改变被测源码。任务卡为 `C:\Users\Tan\AppData\Local\Temp\agent-kb-package-task-20261011.md`，执行票为 [#86](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/86)。

问题：`memory_base` 能否构建为单独 wheel，在与仓库无关的 CWD、空 `PYTHONPATH` 下从新的安装位置导入、运行 CLI，并提供实际 MCP 四工具？

包声明：`agent-memory-base==0.1.0`，Python `>=3.12,<3.13`，console entry point `memory-base = memory_base.deploy:main`；直接依赖为 OpenViking 0.4.23、OpenViking SDK 0.1.13、MCP 1.30.0、llama-cpp-python 0.3.36、volcengine-python-sdk[ark] 5.0.51、OpenAI 2.54.0。

## 解释器和依赖复用边界

- 构建前激活主树 `D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\Activate.ps1`。构建解释器为其绝对 `Scripts\python.exe`，实测身份 Python 3.12.14 / setuptools 81.0.0 / pip 24.0。
- 只读依赖提供方为 `C:\Users\Tan\.agent-memory-base\runtime\Scripts\python.exe`，实测身份 Python 3.12.14 / pip 25.0.1。只读取版本、包 metadata，并执行只读 `pip check`；绝不对该 runtime 执行 `pip install`。
- 在实验 `store/` 中新建 `--system-site-packages` venv。嵌套 venv 默认连接的是 base interpreter 的系统包，不能假定自动继承正式 venv 的包；因此在隔离 venv 自己的 site-packages 写入显式 `.pth`，将正式 runtime 的 site-packages 作为只读依赖提供方放在隔离 site-packages 之后、base site-packages 之前。记录两者和实际依赖模块来源。
- 新 venv 只执行本次 wheel 的 `pip install --no-deps`，不安装或更新上游依赖。该验收证明独立 `memory_base` wheel 可安装与运行，**不证明**在全新依赖环境中完整安装 OpenViking 依赖栈。
- wheel 的包源码必须全部来自隔离 venv 的 site-packages；不依赖旧 `memory_agent`、`ragcore`、`legal_web` 或实验命名空间。受控探针记录 sys.path、模块 origin 及旧模块是否被载入。

## 预先固定的方法与判据

1. 从冻结 SHA 导出 tracked `memory_base/` 至 `store/source/`，在那里构建 wheel，避免在被测实现目录产生 build/egg-info。构建命令为主 venv Python `-m pip wheel <export>/memory_base --no-deps --no-build-isolation --wheel-dir <store>/wheels`。
2. 保存 wheel SHA-256、ZIP inventory、METADATA、entry points、RECORD 验证结果。必须包含所有 tracked Python 模块（包括 credentials、sources、paths、backup、autostart）、`skill/SKILL.md`、`requirements.txt`、`constraints.txt`；不得包含旧三模块或 experiments。逐文件核对导出源码与 wheel 字节哈希。
3. 核对 `project.dependencies`、`deploy.PINS`、requirements 六个直接版本和 constraints 对应版本一致；constraints 是约束清单，不等于安装清单。`requires-python` 与实测解释器匹配。
4. 在实验 store 内安装 wheel；从两个与仓库独立的 CWD 启动子进程，清空 `PYTHONPATH`，去除继承的 `MEMORY_*` 配置，设置 isolated state home。导入所有可直接导入模块并断言 `__file__` 在新 site-packages。`__main__.py` 有 CLI 退出副作用，使用其 installed origin 加 `python -m memory_base --help` 证明入口；同时跑 console `memory-base --help` 和 `python -m memory_base.mcp_stdio --help`。任一导入或 CLI 非预期失败均判失败。
5. 从已安装 wheel 启动实际 MCP stdio 子进程。测试进程内的 loopback HTTP stub 提供无秘密网关响应；真实 `initialize` / `list_tools` 必须得到且只得到 `memory_add`、`memory_search`、`memory_get`、`memory_list`，schema `additionalProperties=false`。逐个调用四工具并核对 HTTP 路由与透传参数。该项仅证明协议/转发，不声称真实 OpenViking 读写语义（由 #85 承担）。
6. 对已安装 `deploy.install` monkeypatch subprocess 与凭据边界，保留调用序列：同一解释器的 wheel 模式不得要求 editable source；选择另一个解释器或 fresh runtime 时应记录显式 source-checkout 限制；不能据受控调用声明新的 runtime 完整依赖安装成功。不触碰正式服务、凭据库、注册表或宿主配置。
7. 用正式 runtime 只读执行 `python -m pip check`，记录原样安全输出及退出码。若其依赖不满足则单独报告实际环境失败，不隐去、不将其归因于 wheel 内容。

## 写域、停止规则与交付

只写 `experiments/memory-base-package/`。脚本、预注册、JSON 和 README 白名单提交；wheel、导出源码、新 venv 与受控安装 home 留在本实验 `store/`，由局部 `.gitignore` 忽略。基座保留，不删除、不归档。临时无关 CWD 可为空目录，不写外部数据。

发现真实 wheel 或安装缺陷立即向 Lead 提交最小复现；不自行修实现，不改地图、正式实例或宿主配置。AOCI 完全停用，不调用、扫描或同步。输出只包含合成 fixture、metadata、路径、哈希和安全错误；不读取正式配置全文或秘密。

最终 README 按问题、假设、设置、数据、结论描述，并明确 Windows/Python 3.12 证据、POSIX 首次凭据持久化尚未验证/当前限制，以及独立包与全新依赖安装的界限。报告预注册 commit、最终 commit、冻结实现 SHA、wheel SHA-256 与复跑命令。
