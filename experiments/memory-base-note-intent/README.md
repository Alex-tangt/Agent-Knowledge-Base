# #92 Bubble 明确笔记写入意图

最终 Bubble 候选为 **36f9bf294e4117bf36a4228d63b3a3c7ad715af1**，基线 **0a86cb1013a481aa91b4b97b826d62a4a8456fb8**。修改仅 `server/memory-tools.ts` 和对应测试；实际部署两文件 raw hash 未变，正式 env/data/服务未操作。Agent-KB 记录写域仅本目录。

## 行为与边界

`memory_add.execute` 在 callback、幂等键和 client.add 之前检查本轮真实用户 message。小 helper 去除成对引号、代码块/inline code、Markdown blockquote；未闭合引用采取拒绝。保存动作的否定、撤回尾语、状态/建议/条件讨论、他人/网页指令、混合不清楚请求优先拒绝，然后识别中文保存命令及英文 Please / Can-Could-Would-Will you 保存命令。

支持 `请把这条笔记保存到知识库`、`帮我保存这条笔记好吗`、`请原样保存这条笔记，不要改写`、`Can you save this note to memory?`。Lead 指出的 `Please store this note as it is to memory` 与 `Please save the note that is important to memory` 已加入正向；`Save a note is a useful feature` 仍拒绝。引用内容中有“不要保存”时，只要外部用户明确要求把该内容保存为笔记，可以保存原文；引用本身不能授予权限。

这是可解释的有限规则，不是通用语言解析器；未匹配的表达不写，返回明确示例让用户表达保存请求，没有新增批准弹窗。正文和 original message 不被 helper 改写，来源 JSON 不授予意图；旧无日期 request_id 的四字段机制和有明确日期时的第五字段机制未修改。

## 验证

| 层 | 实际结果 |
|---|---|
| 初始旧入口回归 | RED：真实 execute 对“不要保存”没有拒绝；首次日志保留 |
| 真实 execute 拒写 | 44次直接拒绝覆盖否定、问题/状态、引用、条件、撤回和转述；gateway calls/entries与onTool为0 |
| 正向及兼容 | 13条正向原样保存 Unicode/CRLF、未知日期不填；原有 source injection、旧key重复与明确日期区分回归通过 |
| 实际 pi SDK 1.1.0 + 本地合成 SSE | 主动发出9次未经授权 memory_add；9次可操作工具错误反馈、18次本地SSE请求；gateway calls/entries及onTool为0，不靠模型不选工具 |
| 最终冻结 npm run build | PASS，退出0 |
| 最终冻结 npm test | PASS，73/73，退出0 |
| 最终冻结 npm run test:host | PASS，5/5，退出0；52.08s |

命令均在候选 worktree 使用已有 node_modules，未安装新依赖、没有真实/付费模型调用或正式网关写入。完整原始输出在 `store/frozen-36f9bf2/`，命令/PID/退出码/输出hash见 [checks.json](checks.json)。real pi SDK 测试通过合成SSE服务强制发出工具调用，而不是根据真实模型是否选择工具推断写入保护。

首冻结8b7327f的build/73项测试也通过，但host生命周期测试在原120s超时取消（4 passed / 1 cancelled），日志出现backend启动后state.json atomic rename EPERM。此失败与原始输出单独保留：[checks-first-8b7327f.json](checks-first-8b7327f.json)、[first_host_failure.json](first_host_failure.json)、`store/first-host-debug/`。当次manager/child精确PID均已自然退出，55704无listener，没有执行kill/stop。没有修改launcher或timeout；最终一次PASS不证明已修复这个宿主问题。

## 交付

[delivery.json](delivery.json) 包含实际原文件raw基线、候选raw/normalized SHA及LF artifact SHA。两artifact与冻结 Git blob逐字节相同；候选工作树为CRLF，正式基线与交付为LF。

- [memory-tools.ts](artifacts/server/memory-tools.ts)：`2dfe82df62eb129564a76586bebdb40998e06a964c1e99393310ef911d1353de`。
- [memory-base.test.ts](artifacts/server/tests/memory-base.test.ts)：`1d1e25b86c1b5168a0ebaec3fad1a5e9a415a90440ac23ca358b6b8ca1c6a13c`。
- [两文件 patch](note-intent-two-files.patch)：`e65750b6e5b9af3be35e76f5dd78fac1d04989d7ba9c42ea7d080770dc6d467c`。

实际 raw 基线：tools `339a852a374af74e471502afa083c48ca6787811bd57cdded03f08bd0b4ee686`，tests `6dc351fd6a015d8a98011e4f83b2f7c791de112a3652f2c738871999e8017256`。原始两文件和原失败均在 ignored store保留，不覆盖或删除。Lead负责冻结复跑、正式只读接入核验、备份后应用及自有manager部署；本任务没有应用正式目录。AOCI未调用或恢复。

Lead 已报告独立在36f9bf2复跑 build退出0、npm test73/73、host5/5全通过，日志位于其 `.scratch/lead-bubble-92`。原EPERM已另登记#93，由独立scripts候选处理，不触本票两份server文件；此记录不把首次失败抹去，也不宣称helper修复了launcher。
