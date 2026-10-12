# Bubble 四工具桥接 #90

问题：实际 Bubble 的三工具客户端丢弃 scope、revision、path 和 completeness，无法按版本读取实时项目或分页列举。

假设：复用已经安装的 pi SDK 与 HTTP 客户端，扩充四工具和版本契约，能够保持旧总结保存、重试和笔记授权行为。

预先设置（2026-10-12）：
- 公共源码快照/候选基线：`a1f5426adbf7de8efb7877181717ee4385388f10`。
- 实际 Bubble：`D:\python_work\work2026-10\Agent-bubble`，只读；四文件分别记录 raw SHA256 与仅 CRLF→LF 的文本 SHA256。
- 候选：`D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate`，仅修改 `server/memory-base.ts`、`server/memory-tools.ts`、`server/agent.ts`、`server/tests/memory-base.test.ts`。
- `node_modules` junction 只读复用实际 Bubble 现有依赖，不运行安装。
- 测试：现有 `npm run build`、`npm test`、`npm run test:host`；真实 pi SDK，模型和 gateway 均为本机测试替身。不调用外部 LLM，不运行 live/media 付费测试，不操作正式服务。
- 负面验证：版本变化、目录变化、来源失联、越权范围、无凭据/任意上游错误正文泄漏；同时保留既有自动保存/重试与真实用户笔记授权测试。

数据与结论：执行后补充冻结候选 commit、仅四文件 patch、原始命令结果和最终哈希；正式 gateway/宿主部署由 Lead 独立验收。

资产与复原：原始公共源码快照保留在 `D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\source`；本目录记录哈希与补丁，实际四文件必须仍与初始 raw SHA256 一致。
