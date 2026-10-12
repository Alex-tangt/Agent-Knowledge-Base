# #91 有限冷启动探针

问题：固定实现/依赖的全新安装在标准首次 native 启动中超过 300 秒，已有正式缓存环境正常；#86 原 FAIL 保持封存。

预登记：仅对授权 `C:\Users\Tan\.mb86\r4\d` 已安装的 `lark_oapi` 做非 force、优化级别 0 的 bytecode 预编译。编译命令独立限制 300 秒；服务仍使用原始每组件 300 秒标准 native 条件，不延长。执行前检查空 workspace、空 manager 记录、专属端口和 d 进程均空闲，验证自身 wheel 原始包字节；不满足则不编译、不启动。仅改变一个变量，不 import SDK 来试暖，不准备其他 SDK。

既有红信号：#86 的原始 d 启动 FAIL，以及 f 标准 native 首次启动 FAIL（均已清理）；不为本票重复原失败。既有 census：同版 Lark 1.7.3 有 10,727 Python 文件，formal 10,727 pyc、d 463 pyc、f 0 pyc，依赖版本无差异。可能瓶颈按现有信号排列：Lark 无缓存的源码编译、Lark 大量文件读取、其他启动导入/存储初始化。本票只检验首项，其余仅为竞争解释，不新增探针。

源码与测量：Agent-KB 基线 `609d88a278fe1187e20afb24f890f93d03e98e01`；初始探针运行不改安装器。新证据仅写本目录与 `C:\Users\Tan\.mb86\r4\cold-start-91`，d runtime 的 Lark pycache 为新增授权测量资产。原 #86 报告、f、formal runtime/cache/源、实际服务、AOCI 保持只读或停用。

native 流程复用原 #86 native_probe.py，仅改授权 home/store guard、另存输出和耗时；使用 d 自身原始 wheel、真实 OpenViking、原外部串行嵌入、已有 GGUF、合成 Unicode/CRLF 笔记，无 LLM 生成。每个服务由原 manager 启动，在 finally 按 owned records 清理。

## R1 测量结果

预登记提交 `6811ae7`。命令（主树 venv 已激活，在独立工作树）：

```powershell
& 'D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe' -c "import runpy;runpy.run_path('experiments/memory-base-cold-start/run_lark_probe.py',run_name='__main__')"
```

live exec handle `65939` 已结束，退出 1。启动前 workspace 空、owned process records 空、专属 63095/63096/63097 端口空闲且无 d Python/CLI 进程。d 解释器实际来源为自身 site-packages，全部 memory_base 包文件与原 wheel 逐字节相同，wheel SHA256 `e7e2dfba47ccc65e47e064f9fadde7b8e9e2d0a6496424122c83f96029b23d03`；Lark 版本 1.7.3，自身来源已确认。

Lark-only 非 force 编译触及本票自行选择的 **300 秒编译测量上限**（300.056 秒）。280 秒采样栈在 compileall → py_compile → importlib get_data，即仍在读 SDK 源文件；没有增添 SDK 或修改其 Python 源码。编译启动前 463 pyc，结束后 10,432 pyc（10,727 py，新增 9,969，剩余 295）。当前最长 Lark 包文件路径 193 字符，未观察到路径长度报错。

**本轮整体 FAIL 停在编译阶段，native 尚未启动。** 不能把此结果当作「预编译后 native 仍失败」，也不能认定 Lark 假设已被否定。安装器与单元测试没有修改。已向 Lead 提出仅继续同一 Lark 的非 force 剩余准备、另存下一有限探针，再使用原 300 秒 native 的候选方向；未擅自继续、扩展 SDK 或延长服务 timeout。

收尾只读核对：编译进程树及 d Python/CLI 无残留；owned records `{}`，三个端口空闲，workspace 仍空。四份受保护原报告 raw SHA256 在前后完全一致（明细在 probe.json），原 d/f FAIL 记录均未覆盖。

## 证据与边界

本目录封存 `probe.json`、`identity-before.log`、`lark-only-compile.log`、`post-compile-state.json`。相同原始字节资产仍留在 `C:\Users\Tan\.mb86\r4\cold-start-91`；d 的部分 Lark bytecode 保留，不删除，不虚称全新无缓存 runtime。不得用下一轮续编耗时替代本轮 300 秒成本。

R1 没有启动服务。正式 runtime、formal 缓存、f home、原 R4 报告与既有 store 未写入，AOCI 未调用。

## R2 绿色信号

Lead 明确批准后，独立预登记 `7b8cef4`，限定同一 Lark 非 force 续编 60 秒，不覆盖 R1。handle `41959` 正常结束，退出 0。续编内部成本 41.311 秒、wall 41.694 秒，**累计准备成本 341.750 秒**；10727 py/10727 pyc，SDK RECORD 摘要未变。不是 fresh 全量准备仅需 41 秒。

在仍空的 d workspace 上运行原 300 秒标准 native：start 191.073 秒成功（包含 embed/身份步骤），三服务 ready、合成 Unicode/CRLF 原文、hash/revision、匿名 401、reader 403、search/list、真实 stdio MCP 四工具/get/list 全通过。stop 18.523 秒，owned 进程和端口完整清理。R1 与原 R4 报告 raw SHA 在前后不变。

该结果是有限准备后首次成功初始化工作区的正向信号；d 之前失败启动产生过部分 SDK 缓存，不能替代完全全新 runtime 的独立复验。**产品当前默认 start 每组件 180 秒，实验 300 秒不等于默认通过**；不修改默认服务 timeout。Lead 批准安装时 Lark-only 准备阶段单独 600 秒预算（基于实测累计341.750秒），准备失败先于部署配置/凭据写入，使用所选 target 的实际 own-site，拒绝写继承 provider cache。

R2 原始报告/命令/manager 日志与归属记录封存在本目录 `r2/`，`evidence-manifest.json` 记录各资产 raw SHA256；原件仍在 `C:\Users\Tan\.mb86\r4\cold-start-91\r2`。d workspace 此后已含本票合成测量基座，保留，不能再称空 workspace 或用于新的 fresh 首次启动。

## 安装修复与回归

冻结实现提交：`0c13d13ae5252fd5848b3979c4e2a3e39cc50031`，仅修改 `memory_base/deploy.py` 和其单测。

install 仍应用原固定依赖约束、保持 source editable / 显式 wheel / PEP610 / adopt 路径，然后校验目标自身运行包来源。新增 Lark-only 准备使用目标解释器与实际模块来源，要求 SDK 为目标 own-site；不写继承 provider 的 cache。只编译 opt0、non-force，已有有效缓存保持字节和 mtime。SDK 定位用 `-B` 防止隐式缓存写入；准备失败（含预算耗尽）显式报错，不传播可能引用源码或凭据的编译错误正文，且发生在模型/成功部署配置/凭据阶段之前。准备阶段独立 600 秒预算，默认服务启动仍每组件 180 秒。

Lead review 发现用户 PYTHONPYCACHEPREFIX / PYTHONOPTIMIZE 会让准备和服务使用不同缓存。真实 `_spawn` HTTP fixture 已先复现红信号（child 继承 user-cache 和 opt2）；修复只在 prepare 与 `_spawn` 的子进程环境清除两变量，不修改用户全局环境。准备成功报告给出 cache_tag/prefix/实际优化状态；真实子进程回归确认默认缓存位置、opt0、调用者环境未改，并通过原 manager 的 owned stop 清理。

| 回归命令 | 结果 | 证据 |
| --- | --- | --- |
| `pytest tests/unit/test_memory_base_deploy.py -k 'prepares_selected_runtime or preparation_failure_preserves' -q`（修复前） | 4 failed，3.39 秒 | install-red.txt |
| `pytest ... -k 'actual_spawn_uses_prepared_cache_policy or actual_target_preparation' -q`（环境修复前） | 2 failed，13.15 秒 | environment-red.txt |
| `pytest tests/unit/test_memory_base_deploy.py -k 'not real_wheel' -q`（冻结实现） | 62 passed / 2 deselected，25.74 秒 | unit-final.txt |
| `pytest tests/unit/test_memory_base_deploy.py -k real_wheel --basetemp <独立 wheel-final 资产> -q -s`（冻结实现） | 2 passed / 62 deselected，66.34 秒 | wheel-final.txt |

62 项包括真实小型自有 Lark fixture 的编译、有效缓存不重写、caller优化设置下拒绝继承 provider、编译失败不泄漏源码、source/fresh/wheel/adopt/same-runtime调用链，以及失败保持既有数据和配置、不发凭据。小型 SDK 是两文件 fixture，不能代表真实 10,727 文件的准备成本；真实成本来自前述 R1/R2 测量。

保留 #89 的两例实际 wheel build→独立 launcher/目标 venv→自身包 pip 安装→实际模块来源→模块与 console CLI 验证。**上游依赖安装/版本查询、新冷启动准备与凭据阶段仍明确为 fixture**，不是完全新依赖栈或首次 native 测量。最终 proof 在本目录 `wheel-final/`，真实 wheel、venv 与原件保留在 `C:\Users\Tan\.mb86\r4\cold-start-91\wheel-final-20261012-91`；此前首轮 wheel 回归资产 `wheel-tests-20261012-91` 同样保留。

`delivery.json` 记录冻结提交、两个实际 wheel raw SHA256、wheel 的 deploy.py 与工作树 raw 字节及 Git CRLF归一化源码一致性、命令结果及 fixture 边界。automatic-default wheel SHA256 `c8e05fd251ad1bb68ade53f16659c7de21b8ba4e225201969f1e063a87588fe0`；explicit-fresh wheel SHA256 `80ba679dc92739849e4b262dafce93a42cee92fedbf967f27706bdb6198d81ea`。

尚未验证：修复后完全全新 runtime 的完整依赖安装、真实 Lark 准备成本和默认 180 秒首次 native 启动。该最终 #86 独立复验归 Lead；本票不会用已经初始化的 d 或 warm 第二次启动冒充 fresh 首次通过。正式更新、全量既有闸门和跨域文档由 Lead 负责。
