# R2 网络环境修正（第二个 store 之前提交）

预注册 `5fbeea837b504c8802ddb739e17ec47092da1fea`、脚本 `0e1fe2f` 的首个真实运行 `store/run-20261012T000202Z` 已自然结束，编排/产品 CLI/pip 均退出 1。wheel 内容、launcher 实际安装与本地产品 origin、两处仓库外 CWD 的全部模块与 help、四工具合成 MCP 协议已通过。默认 home/runtime 确实从不存在创建，`system-site-packages=false`，但官方 llama-cpp-python 0.3.36 CPU wheel 下载在 github.com 经五次重试超时，依赖安装未完成；不将此前协议 PASS 当 native 语义 PASS。

原始安全 pip 日志为 `store/run-20261012T000202Z/command-021-default-real-install.log`，完整阶段/PID/返回码保存在该 store/results.json；本目录 [initial_environment_probe.json](initial_environment_probe.json) 是结束后的安全报告副本。该 store、wheel、导出源码、launcher 和不完整目标 runtime 全部保留，无任何覆盖/删除。尚未启动 native 服务、创建目标 vault 凭据或执行 fresh 案，没有需要停止的自有服务。

原因是验收脚本额外删了 HTTP(S)_PROXY，超出任务卡的环境清洁要求。只读取环境变量的分类后，确认两者均为机器已有 loopback 代理且不带 userinfo；没有打印代理值。Lead 已确认可以保留这两个既有代理。修正只影响实验脚本，不改变产品实现/正式配置/服务/runtime，也不将代理值或私有索引写入日志/报告。

第二个 store 从头真实构建、安装、验收：仍清空 PYTHONPATH/PYTHONHOME/MEMORY_* 与继承 PIP_*，仍禁用用户 pip config、使用 PyPI 及 requirements 的官方 CPU wheel 来源；仅恢复已有无凭据 loopback 网络代理。仍真实执行完整 target pip 安装，不用 provider 或 cache 文件复制替代目标依赖安装。旧 run 不重启，旧日志不抹去。正式 runtime 只读 `pip check` 已独立执行，退出 0：`No broken requirements found.`；最终编排将另存自己的该项结果。

## 第三个 store 前：保留深路径实际平台失败，采用同写域短 fixture

第二轮 `store/run-20261012T000903Z` 在代理恢复后成功解析、下载所有依赖，真实 pip 进入 `Installing collected packages`，随后在 pywin32 312 的 `licenses/com/win32comext/mapi/src/MAPIStubLibrary` 路径报 `[WinError 3]`，CLI/pip/编排自然结束、退出 1。完整副本为 [platform_install_failure.json](platform_install_failure.json)；原始安全日志、wheel、launcher、部分 target 全保留。不把这个实际平台安装失败抹去，也不声称已达到自身包安装/native/fresh 阶段。

失败文件名的实际 Windows Path 长度为 260（错误消息 repr 转义字符串长 282）。Lead 只读核验 `LongPathsEnabled=0`；本执行者不读写 HKLM、不加 junction、不改产品代码。Lead 已批准在相同 write scope 使用新的 `store/3/d/runtime`，对应 pywin32 路径长 230；launcher/fresh home 名为 `l`/`f`。方法先提交，再用 `run_recheck.py --run-name 3` 执行全新完整依赖与包 pip、default native 和 adopt→fresh 全流程。目录必须不存在；不复用或覆盖两个旧 run。

该 fixture 变化限定了最终成功结论的适用范围：短 home 的 Windows/Python3.12 安装；不能据它宣称任意深 home 在关闭 Windows 长路径支持时可用。补充环境探针同时记录全部实际 distribution origin/version，并核对已安装的所有 constraints pin；constraints 中未参与安装的包不强行安装。

Lead 在开跑前另以 formal 的 pywin32 RECORD 做只读独立核对：短 default 布局最长完整文件路径为 238（`MAPIStubLibrary/LICENSE`），`>=260` 的条目为 0。该核对提供短 fixture 的依据，不代替实际 pip/native 验收。
