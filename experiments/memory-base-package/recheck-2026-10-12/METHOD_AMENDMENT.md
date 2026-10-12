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

## R4 前：保留短 repo 路径失败，扩充专属外部 fixture 资产域

第三轮 `store/3` 越过 pywin32，但完整依赖 pip 在 `volcenginesdkadvdefence20230308/models/biz_in_kbps_flow_for_describe_biz_flow_and_conn_count_output.py` 报 Errno 2，并明确提示 Windows 长路径支持未启用；CLI/pip/编排自然结束、退出 1。真实失败副本为 [short_path_platform_failure.json](short_path_platform_failure.json)。未到自身包安装、native 或 fresh；旧资产、原始日志全部保留，无重启或清理。

仅 pywin32 的事前 census 不覆盖云 SDK 的更长文件名。只读 SDK RECORD census 为 [short_path_census.json](short_path_census.json)，包含已生成 pyc 的保守上限：53k 左右条目、8713 个预测路径 >=260。它解释为何同 repo 写域仍不足，不代替新目标 pip 结果。

Lead 已明确扩充**fixture 资产**授权，仅 `C:\Users\Tan\.mb86\r4` 及父 `.mb86` 的本票说明；repo 写域仍仅本 R2 目录。不改正式实例、产品、HKLM，也不加 junction。开跑前只读全部 formal distribution RECORD，核拟目标全部路径 <250，完整数据见 [external_asset_path_census.json](external_asset_path_census.json)；其中 compiled 项也计入保守上限。方法及前三次失败记录先提交，再执行 `run_recheck.py --asset-root C:\Users\Tan\.mb86\r4`。

脚本只接受这个专属外部根，检查解析后的父归属和 census，要求根不存在，禁止覆盖/删除旧目录。wheel、export、launcher、两全新目标 runtime、原始日志/报告、数据和 process records 都在 r4 保留；安全 JSON/脚本/结论仍在 repo 白名单。native home 继续必须在本 store 下，保持真实全新依赖 pip 和全部原判据。若短 home 成功，也不推翻前三次失败，不宣称关闭 Windows 长路径支持时任意深 home 可安装。

开跑前完成全部 **175 份 RECORD / 101336 条目** 核验，拟 target site 前缀长 49，最长预测文件路径 **197**，`>=250` 的条目为 **0**。逐文件 physical-resolve 和另一个等价 lexical normpath 上界均给出相同完整结果；父根另做实际 resolve 并禁止 alias/junction。后者避免了对十万不存在文件开 handle 的重复 IO，数据为 [external_asset_path_census_lexical.json](external_asset_path_census_lexical.json)。原慢测量在身份前置保护后已自然结束、退出 0，没有执行 Stop-Process，也没有中断 pip 或重启任何测量；两个 census 均保留。

## R4 native FAIL 后：仅补验独立 adopt→fresh，不重启 native

R4 default 自动 PEP610 artifact 的真实完整依赖+自身包安装成功；target 模块/distribution/source 均来自自己的 site-packages，全新环境无 provider pth/无 system-site-packages，全部 active pins 和 pip check、两处 CWD 导入与 help 通过。但 native `start --timeout 300` 只有 embedding 健康，upstream 未就绪，out/err 为空；未写合成 note、未启动 gateway。实际 FAIL 副本 [native_start_failure.json](native_start_failure.json) 与外部 r4/native.json / native-start.log 保留。

finally 已通过本 home manager `stop`，退出 0、remaining=[]、processes.json={}，三 fixture 端口无残留。失败发生在 start 返回前，因此当次缺少启动 records 副本；补充脚本今后在 stop 前也保存 partial records，但不据这个后加保存逻辑声称 R4 当次已保留该副本。实际 native 行为和三服务健康均不能算 PASS。

Lead 已确认可继续独立的 adopt→fresh 安装判据，不重启任何服务、不扩范围。`continue_fresh.py` 先封存外部 results-before-fresh.json，再在已有授权 r4 的不存在 f home 真实 adopt/explicit wheel fresh、保留 marker/ports/model、核全新目标 origins/active pins/pip check；formal pip check 只读。脚本先提交再执行；最终 overall 始终保留 native FAIL，installer 独立结果另列，剩余 native 未验证项显式列出。

独立 continuation 已自然结束退出 0，adopt→fresh gate PASS。第二目标 f/runtime 从不存在创建，真实完整依赖/产品 pip 成功，全部 active constraints/origins/PEP610 SHA/source 校验成功，home/model/ports/合成 marker 保留，fresh 和 formal pip check 均为 `No broken requirements found.`。结果封存在 [results.json](results.json)，原 R4 副本与 results-before-fresh.json 不改；native 仍 FAIL。后续只读诊断另存报告。

## 有限对照后：独立标准条件 native（先提交，后执行）

已发现额外 `PYTHONDONTWRITEBYTECODE=1` 会改变普通首次启动条件；R4 原 FAIL 只证明该试验环境未就绪。Lead 已批准标准条件独立复验，同冻结 `9252d52` 的已安装 wheel，使用本票 r4/f 这个**从未启动的 fresh home**，原 manager `--timeout 300`，去掉该额外变量，仍清空 MEMORY_* / PYTHONPATH / PYTHONHOME。不预编译、不改变产品 `--no-compile`、不安装新依赖/引入 fork/调用外部 LLM。

此前只读有限 import 不写缓存；完整 census 已确认 f 的 lark/volcengine/OpenViking/FastAPI/Pydantic pyc 均为 0。f 已有 fresh 案的保留 marker，OpenViking data 顶层仅该 marker，没有初始化上游工作区或 process records。正式服务和三个旧 store 保持原样。

`normal_native.py` 先核 frozen/fresh gate/config/model/source/marker 与不存在的报告根，实际调用 f/runtime 的 `native_probe.py`，分别传 `--asset-root r4` 和 `--store r4/normal-native`。脚本分开 home 资产根和报告根；新子目录报告、process records 快照与日志不覆盖原 r4/native.json、results.json、results-before-fresh.json，并在完成后核验它们的字节哈希。

标准条件实际运行三服务 readiness、native Unicode/CRLF 合成 append、reader get/hash/list/search、anonymous 401/reader 403、SDK1.30 stdio 四工具/get/list；只允许本地 GGUF embedding，不生成总结。finally 由本 f home manager 精确归属清理，保存 stop 前 records、核端口无残留。PASS/FAIL 均另存 [normal_native_result.json](normal_native_result.json)；不改原 FAIL，不因观察等待超时重启或增加服务 timeout。结束后再追加实际结果。
