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

只有预编译后标准 native 实际通过才修改 install；否则报告 Lead，不扩大编译范围、不 fork 上游。正式 runtime、formal 缓存、f home、原 R4 报告与既有 store 未写入，服务未启动，AOCI 未调用。
