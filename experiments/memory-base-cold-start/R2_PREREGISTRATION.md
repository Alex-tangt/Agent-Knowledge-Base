# R2 限定续编预登记

Lead 已明确批准：在 R1 300.056 秒停止后，只续编同一 d runtime 的 Lark SDK，non-force，独立上限 60 秒；不扩大 SDK，不改 upstream/version/dependency。R1 原始报告、清理记录和日志保留。

执行 `run_lark_resume.py`：重新验证 d workspace 空、进程/记录/端口空闲、自身 wheel 包字节和 Lark 来源，然后只对未有效缓存的 Lark 文件续编。证据写到 `C:\Users\Tan\.mb86\r4\cold-start-91\r2`，不覆盖 R1 或 R4。累计准备成本 = R1 的 300.056 秒 + R2 编译 wall time；不称 60 秒 fresh 全量准备。

只有续编在 60 秒内实际成功，才以同一仍空的 d workspace 执行原每组件 300 秒标准 native 流程（完整 HTTP/MCP/权限/原文检查）；finally 使用原 manager 的 owned records 清理。若续编再次到上限，不自动加时或启服务。此阶段安装器不改。
