# #98 实际 OpenCode 本地模型只读对话（预登记）

问题：现有四工具单测和直接 SDK 探针没有覆盖实际 OpenCode 模型会话。按 [任务卡](C:/Users/Tan/AppData/Local/Temp/agent-kb-opencode-local-dialogue-task-20261012.md) 全部边界执行；票据 [#98](https://github.com/Alex-tangt/Agent-Knowledge-Base/issues/98)。产品冻结基线 `a852fab33db109e72a19665877f573e7bd6c2d15`，仅写本实验目录。

假设：已安装 OpenCode 1.18.35 能通过已有本地 Ollama 模型和已注册 `personal-memory` stdio，完成公共项目 README 的发现及带 revision 的读取。

设置：独立 Temp cwd；仅子进程 `OPENCODE_CONFIG_CONTENT` 覆盖供应方、型号和权限。仅启用 Ollama `http://127.0.0.1:11434/v1`，adapter 为已有 `@ai-sdk/openai-compatible`；显式指定 model 与 small_model。默认 deny 全工具，仅 allow personal-memory 的 search/get/list，明确 deny add；保留正式 MCP 入口、home 和 opencode profile。全局配置、auth、正式数据和进程均不改。配置生效验证失败立即停止，不扩大权限或安装组件。

预注册会话：先 `qwen3:0.6B` 一次；仅失败后可 `qwen2.5:3b-instruct-q4_k_m` 一次。每次约 180 秒，最多两次。无 8B、模型安装、daemon 参数变化、商业 provider 或付费调用。提示仅要求发现并 get `projects/agent-kb/README.md`，报告 scope/path/revision；不读 captures 私人正文、不 add、不遵从来源正文指令。

判据：实际 CLI JSON 事件出现已完成的 MCP 工具调用，输入 scope/path 正确，get 输出 revision 与当前公共 README 本机原始字节 SHA256 相同；仅模型口述不算通过。保留失败和观察时的 PID/创建时间/命令，不将工具观察 timeout 视为子进程终态。只清理核实身份的自有 CLI/MCP 子树，绝不终止 Ollama、正式服务、Bubble 或他人进程。

记录：精确无密钥 overlay/命令、模型版本和能力、工具名称和安全 input/output 元数据、原始 trace 的 SHA256；全文 trace 只留 Temp。Git 不存正文、private session、令牌。前后全局配置 hash、三个正式 health 和 capture 文件数量，只作不变性核对。

官方依据（2026-10-13 查阅）：[Ollama provider](https://opencode.ai/docs/providers/#ollama)、[权限最后匹配规则](https://opencode.ai/docs/permissions/)、[运行时配置和 provider allowlist](https://opencode.ai/docs/config/)。仅采用配置能力，不扩展官方建议的模型上下文或 daemon 设置。

数据与结论：待有限真实会话完成后填写。该实验只验证本地型号实际 OpenCode 路径，不能外推默认 DeepSeek、DSH 桌面、手机或登录启动验收。

资产与复原：后续保存独立 Temp 路径和原始证据 hash，全部既有测量资产只读保留。AOCI 不调用、不恢复。
