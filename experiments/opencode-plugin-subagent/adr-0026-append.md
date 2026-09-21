# 待 apply：ADR-0026 追加节（D10 补充 · 形态 B）

> 用途：等主树 `docs/adr/0026-agentic-loop-measurement-protocol.md` 干净后，把下面
> `---8<---` 之间的内容**原样追加到该文件末尾**（紧跟既有「追加（2026-09-20）」节之后）。
> 现在不 apply：主树正被 #60 会话改动，避免双写者。
> 证据：`experiments/opencode-plugin-subagent/results.md`（worktree `wk-60-plugin`）。

---8<--- 从此行以下追加 ---

## 追加（2026-09-21）：D10 补充——插件工具内驱动子会话（探索性，#60 spike）

结论（**PASS**）：在 opencode **插件工具**的 `execute` 调用栈里回调
`client.session.create({ parentID })` + `client.session.prompt({ agent })` **不重入、不死锁**；
子会话结果经工具返回值回到主会话，主上下文不含子会话的召回块 / hop；`session.children(parentID)`
可核到该 child。即"**委派 + 代码控流 + 返回**"在插件层可行。

- 证据：`experiments/opencode-plugin-subagent/`（worktree `wk-60-plugin`，opencode **1.18.31**，
  `@opencode-ai/plugin` 1.18.31，模型 `deepseek/deepseek-flash`）；P0–P4 原始观测见 `results.md`。
- 与 **D10（形态 A：随包 agent `.md` + Task）** 的关系：**形态 A 不变、仍为交付主线**；
  本节仅登记 **形态 B（插件工具内驱动子会话）** 为可选落地形态——相比 skill 的规劝，它是
  **代码契约**（由我们的代码控流），代价是**插件需随包分发**（部署面变化）。
  **本阶段不采用，不据此改动 #60 的交付物**（owner 2026-09-21 决定）。
- 边界（如实标注）：
  1. `format: json_schema` 结构化输出在当前模型**不可用**——provider 返回 400
     `Thinking mode does not support this tool_choice`（`info.error`，`structured_output=null`）；
     需换支持该 tool_choice 的模型，**与重入无关**（同一调用正常返回并带回错误）。
  2. 本 spike 只用一次性 `session.prompt` 返回，**未验证**流式 / 中途事件。
  3. 仍只解决**隔离与控流**，不修早停、不构成对 skill 行为的验证（同 D10 与 #60 后置）。
- **D8 不变**：宿主 enforcer / 插件随包分发仍是部署决定，非包能力。

---8<--- 到此行以上追加 ---
