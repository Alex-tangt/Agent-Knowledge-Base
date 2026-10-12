# #93 Windows 原子状态发布

预登记（2026-10-12）：原 #92 首次 host 失败在 backend ready 后，`state.json.<instance>.tmp → state.json` rename 发生 Windows EPERM(-4048)，未保护的回调使 manager 退出，留下 starting 状态。原 FAIL、旧资产不修改；不把再次 PASS 当修复证据。

写域：独立 Bubble candidate93（baseline `0a86cb1013a481aa91b4b97b826d62a4a8456fb8`）中仅 scripts/local-backend.mjs、必要状态辅助模块、scripts/tests；KB 分支 codex/93-host-state 基于 `45296a58fe2559e0b92f4af851c018b56148865a`，仅本实验目录。actual Bubble 源/env/data/manager只读；不碰 #92 server文件或 #86 r5。node_modules readonly junction复用，无下载、安装、外部LLM。

方法：先确认原 FAIL/脚本 raw与CRLF规范化SHA，再在独立 host 子进程的 ready→rename 路径注入暂时/持续 EPERM（明确是fault injection，不能冒称原生复现），保留 RED。仅采用短、有界、同步 rename retry，使状态发布保留原子替换和当前同步顺序；不改 readiness 120 秒。持续失败须日志可见、结束本instance child、清理自有锁/状态/tmp，不能改其他owner的文件或外部backend。

预先验证：暂时 EPERM/EACCES/EBUSY 恢复、持续失败有界退出、完整JSON/阶段顺序、自有child无残留、其他owner资产不动；原host测试全部执行。最后冻结候选跑现有 build/app test/host一次，不跑live或付费模型测试。正式应用/重启归Lead。

## 实现与冻结

Bubble 冻结候选：`ba8b5b5c557fceee93c9e5738b43ecee7ec9b156`，工作树干净，恰好三个 scripts 文件：`local-backend.mjs`、新增 `host-state.mjs`、`tests/local-backend.test.mjs`。没有修改 server、package.json、ready timeout 或实际目录。

状态仍写本instance的 `.tmp` 并通过 rename 原子替换，不 truncate/rewrite 目标。只对 EPERM/EACCES/EBUSY 最多尝试6次，间隔40ms，总重试暂停最多200ms；同步执行避免先前 starting 在较晚 running 之后完成。持续失败只输出固定安全错误代码，不把未验证错误正文当反馈；关闭记录在当前 ChildProcess handle 的自有 child，保持原12s graceful shutdown deadline，退出1。initial失败无child和signal退出后restarting失败也能结束，不等待已经消失的child。每次发布前核对本instance锁；结束时仅清理本instance状态/stop/tmp，锁最后释放，其他owner文件不删。前台 start 能发现自有后台manager已退出，不假称仍活动；120s readiness不变。

## 原始 FAIL 与故障注入

原真实 FAIL 在 `original-fail/`：#92 ready回调 rename 出现Windows EPERM(-4048)，manager退出后starting/lock陈旧。原launcher.log SHA256 `3d413d5c17ec887716cd2cdcf32b7704b1a6238be85042b270c590adbcd2f819`；原报告和日志在收尾重新核验均未变。只读副本没有覆盖原件。

本轮先在实际候选launcher/backend的同ready回调路径，通过独立测试 NODE_OPTIONS preload 注入 rename EPERM，显式为 **fault injection，不是新的原生锁重现**。旧实现两个测试均RED：暂时错误无法恢复，持续错误留下starting/lock。原始输出 `commands/fault-red.txt`，54秒；两个临时实例当次child/manager均已自然结束，测试的精确fallback清理没有触发，port无listener，旧状态保留在 `fault-red/` 及原candidate93 `.tools/state-test-*`。

实现后同ready路径2/2 GREEN；另外initial（未fork child）与restarting（signal退出child）持续失败2/2。真实子进程退出及child身份按PID/creation/command采集，所有GREEN退出后state/lock为null、child和listener均无残留，未触发测试fallback。原始快照和最新冻结GREEN在 `fault-green/`，实际测量资产路径记录在 delivery.json，不删除。

## 最终冻结验证

| 命令/验证 | 结果 |
| --- | --- |
| 原子状态单测 | 4/4：三类短错误恢复、完整JSON、running→stopping同步顺序、持续错误6次上限、foreign state/lock/tmp不删 |
| `npm run build` | 退出0，18.176秒 |
| `npm test` | 68/68，退出0，37.300秒 |
| `npm run test:host` | 13/13，退出0，105.921秒，含原5项和新增8项；既有生命周期120s未变 |

完整命令/PID/退出码/原始输出SHA在 `commands/*.json` 和原始 `.txt`。最终host handle `58851` 正常结束；候选路径 Node进程只读核对为0（process-check.json）。原host生命周期继续验证启动、重复启动、crash恢复、graceful stop以及外部backend不被manager停止。未因为随机FAIL而重复执行最终gates。候选按卡从0a86基线建立，未合入#92两份server更改，所以应用测试为68项；正式#92+scripts组合的独立验证归Lead。

## 交付与边界

`delivery.json` 包含实际脚本raw基线、候选raw/Git CRLF归一化SHA、原FAIL不变检查、每条命令及GREEN归属状态。实际launcher raw SHA仍为 `b4896de296d068f7605d93b5549ee623ff58f6fe44bff1caee5edd3ec5d9c6ad`；实际没有新增host-state模块，未应用正式文件。

三个 `artifacts/scripts/` 文件是冻结Git LF blob原始字节；本目录属性固定patch/artifacts/原始输出为二进制保存，避免autocrlf改变证据哈希。仅三文件 patch `host-state-three-files.patch` 的 SHA256为 `4a606b6a36ef5c32c915a1b5e5a884b23f1836b7cbfaae0ce2e4923d53a9a705`；在原0a86工作树 `git apply --check` 已通过，未实际应用。

本票证明被注入的短暂rename失败恢复、持续失败安全退出以及原host行为兼容；未再次观测到原生随机EPERM，不能宣称覆盖任何无限持有的Windows文件锁。持续底层unlink拒绝会输出固定清理错误，实际文件系统永久不可写时无法保证删除文件，但自有child退出、manager结束，状态不能被判为活跃实例。没有下载依赖、调用外部LLM、停启正式manager、修改实际env/data或触碰#86 r5。AOCI未调用；正式应用、独立复核与部署归Lead。所有旧资产保留。
