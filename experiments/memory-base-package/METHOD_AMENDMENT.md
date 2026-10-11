# 依赖复用设置修正（R1，第二轮之前提交）

第一轮（`store/run-20261011T172343Z`）已实际构建并安装 wheel，inventory、源码字节和 RECORD 检查通过。随后 import `memory_base.mcp_stdio` 时，正式依赖提供方的 MCP 1.30.0 要导入 `pywintypes`，实验 venv 只加入正式 site-packages 路径，没有执行 pywin32 的标准 `.pth` 引导，导致 `ModuleNotFoundError: No module named 'pywintypes'`。该原始运行保存在 `initial_environment_probe.json` 和原 store；未删除或改写。

这是显式复用 Windows 依赖的测试环境配置缺口，并非 wheel 缺文件：正式 runtime 的 `pywin32.pth` 添加 `win32`、`win32/lib`、`pythonwin` 路径，并调用 `pywin32_bootstrap`。第二轮在新实验 venv 自己的 `.pth` 中加入正式 site-packages 后，仅使用 Python `site.addpackage` 处理这一份依赖 `.pth`，记录其 SHA-256。不处理正式环境的 editable package `.pth`，不更改正式 runtime。

冻结实现 SHA、wheel 内容判据、两处独立 CWD、空 `PYTHONPATH`、全部模块 origin、CLI 和实际 MCP 四工具判据保持原样。第二轮仍只安装 wheel `--no-deps --ignore-installed`；依赖栈仍为明确声明的只读复用，不能推断全新上游依赖安装成功。
