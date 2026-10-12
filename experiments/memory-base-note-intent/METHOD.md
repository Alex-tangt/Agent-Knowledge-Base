# #92 明确保存笔记意图

Agent-KB 起点 f5e3988dd5cf33290decc8f03926bab0742000a8；Bubble 候选起点 0a86cb1013a481aa91b4b97b826d62a4a8456fb8。Agent-KB 写域仅本目录；候选仅 server/memory-tools.ts 与 server/tests/memory-base.test.ts。实际 Bubble 两文件仅捕获 raw baseline，正式 env/data/服务不写。

任务卡已指定两个公开测试 seam：memoryTools 创建的真实 memory_add.execute（本地 HTTP client fixture 观察外部 add 请求），以及真实 pi SDK + local synthetic SSE 主动发出 memory_add（工具错误反馈返回 SDK，gateway 收不到 add）。不以模型选择不调用工具证明权限，不调用真实/付费模型，不安装依赖，不新增批准弹窗或泛化解析框架。

先在 execute seam 加一个否定保存回归并保存原关键词实现实际失败；之后实现有限确定性 helper，逐步覆盖明确否定、状态/是否应该讨论、引号/Markdown/JSON引用、来源注入与混合不明指令。清晰中文/英文命令、Can you save、不要改写作为正向；保留旧无日期request_id和明确日期区分。tool callback 与外部写请求都须在授权检查后。

候选最终只两文件 commit、npm build/test/test:host 在冻结候选运行。原失败和全部安全原始输出保留于 ignored store；两文件 LF artifact、patch 与 raw/normalized hash 交 Lead，Lead 独立复跑并负责应用到正式 Bubble。AOCI 不安装、不恢复、不调用。
