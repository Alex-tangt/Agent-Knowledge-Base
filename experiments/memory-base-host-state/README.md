# #93 Windows 原子状态发布

预登记（2026-10-12）：原 #92 首次 host 失败在 backend ready 后，`state.json.<instance>.tmp → state.json` rename 发生 Windows EPERM(-4048)，未保护的回调使 manager 退出，留下 starting 状态。原 FAIL、旧资产不修改；不把再次 PASS 当修复证据。

写域：独立 Bubble candidate93（baseline `0a86cb1013a481aa91b4b97b826d62a4a8456fb8`）中仅 scripts/local-backend.mjs、必要状态辅助模块、scripts/tests；KB 分支 codex/93-host-state 基于 `45296a58fe2559e0b92f4af851c018b56148865a`，仅本实验目录。actual Bubble 源/env/data/manager只读；不碰 #92 server文件或 #86 r5。node_modules readonly junction复用，无下载、安装、外部LLM。

方法：先确认原 FAIL/脚本 raw与CRLF规范化SHA，再在独立 host 子进程的 ready→rename 路径注入暂时/持续 EPERM（明确是fault injection，不能冒称原生复现），保留 RED。仅采用短、有界、同步 rename retry，使状态发布保留原子替换和当前同步顺序；不改 readiness 120 秒。持续失败须日志可见、结束本instance child、清理自有锁/状态/tmp，不能改其他owner的文件或外部backend。

预先验证：暂时 EPERM/EACCES/EBUSY 恢复、持续失败有界退出、完整JSON/阶段顺序、自有child无残留、其他owner资产不动；原host测试全部执行。最后冻结候选跑现有 build/app test/host一次，不跑live或付费模型测试。正式应用/重启归Lead。

运行结果、冻结提交、脚本范围、仅scripts补丁与证据SHA在完成后补充。原始失败和本轮故障注入的RED/GREEN分列；所有旧测量资产保留。
