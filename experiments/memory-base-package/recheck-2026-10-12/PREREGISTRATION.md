# #86 R2 独立 wheel / 全新 runtime 安装预注册

2026-10-12。冻结实现 `9252d5281ab69ad418c76e1672af4dc6fbdff9e5`，任务卡 `C:\Users\Tan\AppData\Local\Temp\agent-kb-wheel-recheck-task-20261012.md`。本记录先提交，随后才构建和实际安装。原 #86 FAIL、R1 环境错误及所有 store 保留；不修改实现、正式实例或注册表；AOCI 完全停用。

## 假设与方法

修复 #89 后，独立安装 wheel 的 CLI 能自动沿用 PEP610 的本地 artifact 创建新 runtime，也能显式 `--wheel --fresh-runtime` 从已采用 runtime 切换到本 home 的全新 runtime。两案均真实执行依赖 pip、产品 pip、版本检查和 Windows 新 home 命名空间凭据操作；无 install/_versions/venv/credential stub。

激活主树 venv 后，用主 Python3.12 从冻结 tracked `memory_base/` 的 git archive 导出副本构建 wheel。记录每个 Git blob raw hash、archive 导出 hash、CRLF→LF 归一化相等性，以及 wheel 与导出源码逐字节一致性。不得将 archive 字节等同于 Git blob 原始字节。核 inventory、METADATA、entrypoint、RECORD 和六项直接 pin；不含旧三包/experiments。

新 launcher venv 用真实 `pip install --no-deps --no-index --ignore-installed` 安装产物；可仅在 launcher 内显式写 `.pth` 只读复用 formal site-packages，处理 pywin32 标准 bootstrap，记录 pth/hash/origins。不得误称 launcher 是全新依赖栈。formal runtime 仅查询版本、路径和 `pip check`，不修改。

默认案从 installed console CLI 的仓库外 CWD 调用 `install --home <新home> --model <已有GGUF>`；目标 `<home>/runtime` 预先不存在。使用 PyPI 官方索引、requirements 指定的 llama-cpp-python CPU wheel 官方索引和已有 pip cache；不更换产品版本。目标不写 provider `.pth`、不开 system-site-packages；所有依赖 distribution/module origin 必须来自目标 site-packages，source 必须绑定该目标；执行目标 `pip check`。

显式案在第二隔离 home 先真实采用 launcher `--runtime-python`，再以 `--wheel <本地产物> --fresh-runtime` 创建本 home/runtime。中间登记独占 fixture ports 和合成 data marker；fresh 后验证 home/model/ports/marker 不变。此案完整安装 pinned 依赖，目标同样不继承 formal provider，无需重复 native 全流程。

所有子进程清空 PYTHONPATH/PYTHONHOME 和继承 MEMORY_*，分别从 TEMP 与 System32 运行 installed 全部模块导入、console/module/MCP help。可复用原 probe_installed.py 的 imports/mcp 合成协议模式；不调用 install-boundary、不沿用旧失败预期。合成协议与 native 结果分开。

default 案第一次 native 启动前只给测试 deployment.json 分配空闲独占端口，明确记作 fixture setup（CLI 当前没有 ports 参数）。真实起三服务，用新 home vault 身份做 HTTP Unicode/CRLF note add、reader get/hash、search/list、匿名401、reader403；实际 SDK1.30 stdio initialize/list_tools/get/list。仅本地 GGUF 嵌入，无 LLM/总结生成/远端抓取。stdout/report 不含正文或凭据。最后通过本 home manager 校验 PID/birth/full command 停止自有测试服务，保留启动 records 副本并核无残留。

## 预先固定判定

1. wheel inventory/source/export/metadata/RECORD：全部通过。
2. launcher 真实 no-deps wheel 安装及本地 origin：通过，复用边界显式。
3. 两处仓库外 CWD 的全部模块/三个 help：通过（launcher 与 default target 分别记录）。
4. 六项 pin 对 project/requirements/constraints/METADATA/PINS/目标 distribution 一致，目标 pip check 通过。
5. 实际 MCP 四工具合成协议：通过，仅宣称协议；native stdio 另列。
6. default 自动 artifact 与 adopt→显式 fresh 实际安装均 CLI=0；两个目标全新依赖栈及 origin 通过。
7. formal runtime 只读 pip check：通过或明确单列环境失败。
8. default native 三服务、合成原文读写搜索列举、权限、stdio 及自有清理：全部通过。

失败立即保存实际阶段、返回码和安全错误给 Lead，不自修实现。pip 原始安全日志写 ignored store，pip/session 无编排观察超时，不据等待时间重启或丢弃进程。每次运行创建新的 store；wheel、导出源码、venv、数据根和 process records 全部保留。提交安全 JSON/README/脚本时记录本预注册 commit、冻结 SHA、脚本 SHA、artifact SHA 和真实命令。Windows/Python3.12 结论不外推 POSIX 首次凭据持久化。
