# #98 实际 OpenCode 本地模型只读对话（预登记）

问题：现有四工具单测和直接 SDK 探针没有覆盖实际 OpenCode 模型会话。按 [任务卡](C:/Users/Tan/AppData/Local/Temp/agent-kb-opencode-local-dialogue-task-20261012.md) 全部边界执行；票据 [#98](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/98)。产品冻结基线 `a852fab33db109e72a19665877f573e7bd6c2d15`，仅写本实验目录。

假设：已安装 OpenCode 1.18.35 能通过已有本地 Ollama 模型和已注册 `personal-memory` stdio，完成公共项目 README 的发现及带 revision 的读取。

设置：独立 Temp cwd；仅子进程 `OPENCODE_CONFIG_CONTENT` 覆盖供应方、型号和权限。仅启用 Ollama `http://127.0.0.1:11434/v1`，adapter 为已有 `@ai-sdk/openai-compatible`；显式指定 model 与 small_model。默认 deny 全工具，仅 allow personal-memory 的 search/get/list，明确 deny add；保留正式 MCP 入口、home 和 opencode profile。全局配置、auth、正式数据和进程均不改。配置生效验证失败立即停止，不扩大权限或安装组件。

预注册会话：先 `qwen3:0.6B` 一次；仅失败后可 `qwen2.5:3b-instruct-q4_k_m` 一次。每次约 180 秒，最多两次。无 8B、模型安装、daemon 参数变化、商业 provider 或付费调用。提示仅要求发现并 get `projects/agent-kb/README.md`，报告 scope/path/revision；不读 captures 私人正文、不 add、不遵从来源正文指令。

判据：实际 CLI JSON 事件出现已完成的 MCP 工具调用，输入 scope/path 正确，get 输出 revision 与当前公共 README 本机原始字节 SHA256 相同；仅模型口述不算通过。保留失败和观察时的 PID/创建时间/命令，不将工具观察 timeout 视为子进程终态。只清理核实身份的自有 CLI/MCP 子树，绝不终止 Ollama、正式服务、Bubble 或他人进程。

记录：精确无密钥 overlay/命令、模型版本和能力、工具名称和安全 input/output 元数据、原始 trace 的 SHA256；全文 trace 只留 Temp。Git 不存正文、private session、令牌。前后全局配置 hash、三个正式 health 和 capture 文件数量，只作不变性核对。

官方依据（2026-10-13 查阅）：[Ollama provider](https://opencode.ai/docs/providers/#ollama)、[权限最后匹配规则](https://opencode.ai/docs/permissions/)、[运行时配置和 provider allowlist](https://opencode.ai/docs/config/)。仅采用配置能力，不扩展官方建议的模型上下文或 daemon 设置。

数据与结论（2026-10-13）：最多两次预算已用完，两次均自然 exit 0、未触及 180 秒，全部自有 CLI/MCP 子树自然退出，没有执行任何终止动作。实际完成工具事件只有 `personal-memory_memory_list` / `personal-memory_memory_get`；没有 add、文件、shell 或其他 MCP 工具事件。

| 模型 | 实际对话时间 | 完成工具 | 核心判据 | 未覆盖/未遵从 |
| --- | ---: | --- | --- | --- |
| qwen3:0.6B | 72.531 秒 | list 一次 | FAIL：没有 get | 最终文字虽有正确 path/hash，仍不算实际读取 |
| qwen2.5:3b-instruct-q4_k_m | 131.359 秒 | list 一次、get 一次 | PASS：实际读及返回版本独立核对 | get 的 `expected_revision=null`；最终模型文字没有要求的 path/hash，完整提示规约未通过 |

3B 的 get scope/uri 均为 `projects/agent-kb/README.md`。正文重新编码 UTF-8 为 **10053 字节**，SHA256 **`3612771cabaf820bd4d09aeb72459d2184ee1755848620525094e5fab48148fb`**，同时等于 get 返回 revision/content_hash、list revision 和正式源文件原始字节 hash。仅对此实际 OpenCode 本地 list→get 通路及版本结果判 PASS；不能声称该小模型使用了版本前置条件、完整遵循提示，或具备一般产品质量。memory_search、拒绝权限和 add 执行均未在本次模型对话测试。

第一次配置探针发现 `opencode.jsonc` 与 `opencode.json` 合并后，额外启用 markitdown/github；在模型前通过子进程 overlay 显式 disabled，第二次真实解析确认 enabled MCP 仅 personal-memory、plugin 为空。两个模型会话均沿用该收窄配置，只改变显式 model/small_model。本地 adapter 来自已安装 CLI binary 直接 bundled creator 映射 `@ai-sdk/openai-compatible → B:/~BUN/root/chunk-jat3gdkd.js → createOpenAICompatible`；未虚称独立 npm package/cache 安装。`verified.json` 仅保留最终有效权限及必要覆盖；原 debug 输出只在内存处理并 hash，不保存凭据或全文。

两次会话前后全局目录顶层 json/jsonc/package 文件和既有 auth.json **6 个 SHA 相等**；配置预检最初记录的 global opencode.json SHA 也未变。正式三个 health 前后均 200，captures 路径内文件数 **8→8**（只目录枚举；这不是逻辑笔记数，不读私人正文或 URL）。正式公共 README hash 未变。JSON 原始会话事件含正文，仅留本机 Temp，Git 只收录安全元数据和 trace hash。

方法中的廉价 harness 错误均在模型前/离线解析阶段：首次 port 键读错，未进入真实 debug/config 或 LLM，保留 `preflight-harness-failure.json`；首轮 list 摘要初稿漏识别 items，离线 `analysis-1.json` 补全而不重跑；3B 离线摘要初稿曾将最终答复格式并入核心 PASS 判据，最终按本预登记的工具结果判据拆出提示遵从维度，原始 trace 不改，初稿留 Temp。provider/工具权限验证整个 CLI 配置阶段实际两次 config 解析和一次 agent 摘要，随后模型只执行两轮。

不外推默认 DeepSeek、DSH 桌面对话、手机或真实登录启动验收。没有新增工具、依赖、模型或第二库/模拟 gateway；正式配置、数据、服务不由本实验操作。AOCI 不调用、不恢复。

资产与复原：`C:\Users\Tan\AppData\Local\Temp\memory-base-opencode-98-ysneij2m` 保留全部原始资产。`session-1/trace.jsonl` SHA256 `74b5118df8ecb96b9ef9e4b7abdc2ee85a2c3e7444292df4ec5dc4f43bd66279`；`session-2/trace.jsonl` SHA256 `b64c6dd8aeadfed5ffd78abb28744e095f927b61cd23a0186320664b34bb7ec5`。live.json/result-safe.json 保存精确 argv、PID 和创建时间；受控 exec 句柄 31670/4277 已 exit 0，PID 42556/29392 及所有记录子进程均已不存在。`probe.py analyse` 仅离线读已有 trace；`seal.py` 无模型调用，收集版本及不变性终检并生成 hashmanifest。只保留既有第一次失败资产，不删除旧测量基座。
