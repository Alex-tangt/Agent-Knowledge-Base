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

## 实现与冻结

候选提交：`0a86cb1013a481aa91b4b97b826d62a4a8456fb8`，工作树干净，仅四个白名单源码文件变化。

- 第一单元 `3cc89c3bc95f52f23922efb5ce90065f2a692eda`：保留检索/正文/列举的路径、版本、来源、method/completeness、分页信息；旧 search/get 调用不变。显式 expected_revision 必须匹配，正文与收到的 revision/content_hash 做 SHA256 校验。只将已知公开错误代码映射到固定的恢复反馈，不传播任意上游错误正文或未知代码。
- 第二单元 `0a86cb1013a481aa91b4b97b826d62a4a8456fb8`：真实 pi 注册 search/get/list/add 四工具，增加 scope、expected_revision、cursor/limit 与明确的 observed_on。项目只读、分页和版本错误处理、来源文本不是指令均写入工具描述和系统提示。省略日期保持原幂等键；明确日期纳入键，不推断接触日。真实用户笔记写授权与自动总结保存逻辑保留。
- 真实网关权限代码是 `READ_SCOPE_FORBIDDEN`/`WRITE_SCOPE_FORBIDDEN`；另外兼容卡中的 `FORBIDDEN_SCOPE`。目录变化为 `LIST_CHANGED_RESTART`，版本变化为 `REVISION_MISMATCH`。

## 命令与数据

原始完整输出在 `commands/*.txt`，同名 JSON 记录命令、cwd、UTC 开始时间、耗时和真实退出码。

| 运行 | 结果 | 边界 |
| --- | --- | --- |
| 基线 memory-base 测试 | 9/9，49.094 秒，命令退出 0 | 改动前原有测试；包装器最后打印 Unicode 时因 Windows GBK 出错，原始日志和真实命令退出码已完整保存。之后包装器显式用 UTF-8 输出。 |
| 客户端增量测试 | 3/3，18.656 秒 | 版本/范围/分页保留、正文校验、稳定错误与不泄漏正文 |
| 第一单元 build | 退出 0，58.062 秒 | 已装依赖，不安装新包 |
| 四工具正向 memory-base 测试 | 13/13，21.297 秒 | 之后再补充真实 SDK 负面反馈并跑最终全套 |
| 最终 `npm run build` | 退出 0，20.141 秒 | TypeScript 现有构建 |
| 最终 `npm test` | 68/68，26.063 秒 | 含 13 项 memory-base 测试、真实 SDK 正向/负面往返以及旧保存/重试/授权测试 |
| 首轮 `npm run test:host` | 4/5，退出 1，2.547 秒 | 候选没有 `.tools` 父目录，mkdtemp 在启动前 ENOENT；记录保留 |
| 创建候选 `.tools` 父目录后的 `npm run test:host` | 5/5，退出 0，48.703 秒 | 隔离 test DATA_DIR、随机本机端口、假 provider key、关闭 memory 接入；仅操作候选进程 |

真实 pi SDK 两条链路使用本机 synthetic OpenAI-compatible SSE：旧 search/get 调用兼容，以及 project scope → list 双页 → 错误版本 get → 正确 revision get → 目录变化 → 来源失联 → 范围拒绝 → 明确日期笔记。模型请求验证四工具 schema、无额外写项目入口；工具返回验证 path/revision/completeness/cursor 与可操作反馈，断言无 token 或任意上游错误正文。额外客户端测试覆盖成功 HTTP 响应仍返回错误/缺失 revision、错误正文 hash 和未知错误代码。自动文章/视频总结仍原样保存，未推断 observed_on；保存失败后重试不再调用模型。

## 结论与验收边界

候选在现有依赖环境通过 build、全部 68 项应用测试与 5 项隔离 host 测试。2026-10-12 原 test:host handle `93340` 已正常结束，退出 0；随后只读核对候选路径的 Node 进程为 0，不以等待超时重跑。

这些结果证明 Bubble 客户端、真实 pi 工具注册/执行与本机 HTTP 协议替身间的行为。模型输出为测试预设 SSE；gateway 为本机 HTTP fixture，未接正式 gateway/OpenViking/真实项目来源，也不证明真实模型自主选择正确工具。正式 gateway + synthetic SSE 的独立复验、实际原始 SHA 核对、备份应用和正式宿主部署归 Lead。未调用外部 LLM，未安装依赖，未读写实际 Bubble 的 `.env/data/.tools`，未停启正式进程；AOCI 未调用或恢复。

## 交付文件

- `baseline-hashes.json` / `final-hashes.json`：实际原始、公共快照、候选工作文件的 raw SHA256 与仅 CRLF→LF SHA256；两处 Git blob SHA256 单独列出。`actual_raw_unchanged=true`，实际四文件在收尾仍与初始 raw SHA 相同。不能把 Git LF blob 或候选 raw 字节虚称为实际文件的逐字节相同版本。
- `bubble-four-tools.patch`：基线到冻结候选的仅四文件 Git 补丁（LF 规范化 blob）；40,184 字节，raw SHA256 `f65c17ddd0ef1211aae374a533c0aa9decc62b626c5196dd7c321152139aedf8`。
- `delivery.json`：冻结 commit、patch 哈希、实际依赖版本、命令结果和环境边界。Node 24.14.1；实际复用 pi-ai/pi-coding-agent 1.1.0、typebox 1.3.27、zod 4.6.5、typescript 5.9.3、tsx 4.23.15。
- `capture_hashes.py`、`export_delivery.py`、`run_check.py`：只读采集四文件、导出冻结补丁与捕获现有命令，不读取实际 `.env`。已有封存结果拒绝覆盖。

资产与复原：原始公共源码快照保留在 `D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\source`；本目录记录哈希与补丁，实际四文件必须仍与初始 raw SHA256 一致。

候选的隔离 host 测试资产保留在 `D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate\.tools\host-test-GjWIlM`（完整路径也在 delivery.json）；公共快照、候选、既有实验 store 均未删除或归档。
