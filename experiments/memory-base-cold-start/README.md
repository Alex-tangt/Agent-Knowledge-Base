# #91 有限冷启动探针

问题：固定实现/依赖的全新安装在标准首次 native 启动中超过 300 秒，已有正式缓存环境正常；#86 原 FAIL 保持封存。

预登记：仅对授权 `C:\Users\Tan\.mb86\r4\d` 已安装的 `lark_oapi` 做非 force、优化级别 0 的 bytecode 预编译。编译命令独立限制 300 秒；服务仍使用原始每组件 300 秒标准 native 条件，不延长。执行前检查空 workspace、空 manager 记录、专属端口和 d 进程均空闲，验证自身 wheel 原始包字节；不满足则不编译、不启动。仅改变一个变量，不 import SDK 来试暖，不准备其他 SDK。

既有红信号：#86 的原始 d 启动 FAIL，以及 f 标准 native 首次启动 FAIL（均已清理）；不为本票重复原失败。既有 census：同版 Lark 1.7.3 有 10,727 Python 文件，formal 10,727 pyc、d 463 pyc、f 0 pyc，依赖版本无差异。可能瓶颈按现有信号排列：Lark 无缓存的源码编译、Lark 大量文件读取、其他启动导入/存储初始化。本票只检验首项，其余仅为竞争解释，不新增探针。

源码与测量：Agent-KB 基线 `609d88a278fe1187e20afb24f890f93d03e98e01`；初始探针运行不改安装器。新证据仅写本目录与 `C:\Users\Tan\.mb86\r4\cold-start-91`，d runtime 的 Lark pycache 为新增授权测量资产。原 #86 报告、f、formal runtime/cache/源、实际服务、AOCI 保持只读或停用。

native 流程复用原 #86 native_probe.py，仅改授权 home/store guard、另存输出和耗时；使用 d 自身原始 wheel、真实 OpenViking、原外部串行嵌入、已有 GGUF、合成 Unicode/CRLF 笔记，无 LLM 生成。每个服务由原 manager 启动，在 finally 按 owned records 清理。

结果、成本及边界在执行后补充。只有预编译后标准 native 通过才修改 install；否则报告 Lead，不扩大编译范围、不 fork 上游。
