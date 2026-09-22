# 待 apply：ADR-0026 追加节（D10 补充 · 形态 B）

> 用途：等主树 `docs/adr/0026-agentic-loop-measurement-protocol.md` 干净后，把下面
> `---8<---` 之间的内容**原样追加到该文件末尾**（紧跟既有「追加（2026-09-20）」节之后）。
> 现在不 apply：主树正被 #60 会话改动，避免双写者。
> 证据：`experiments/opencode-plugin-subagent/results.md`（worktree `wk-60-plugin`）。

---8<--- 从此行以下追加 ---

## 追加（2026-09-21）：D10 补充——插件工具内驱动子会话（探索性，#60 spike）

结论（**PASS**，限重入 / 控流）：在 opencode **插件工具**的 `execute` 调用栈里回调
`client.session.create({ parentID })` + `client.session.prompt({ agent })` **不重入、不死锁**；
子会话结果经**工具返回值**回到主会话；`session.children(parentID)` 可核到该 child。
即"**委派 + 代码控流 + 返回**"在插件层可行。

- 证据：`experiments/opencode-plugin-subagent/`（worktree `wk-60-plugin`，opencode **1.18.31**，
  `@opencode-ai/plugin` 1.18.31，模型 `deepseek/deepseek-flash`）；P0–P4 原始观测见 `results.md`。
- 与 **D10（形态 A：随包 agent `.md` + Task）** 的关系：**采纳形态 B（插件工具内驱动子会话）**
  为交付形态；**形态 A 保留为无插件环境下的 fallback**（同一子代理定义 `.md` 两用）。
  - 接口：**只返回文本**（消费方是主 LLM；无固定格式 / 成分需求；结构化输出还会撞 provider
    的 `tool_choice` 限制，见边界 1）。`id` 依据沿用提示词里的**纯文本约定**，不上升为 schema。
  - 控流：**hop 预算由插件控**（插件循环调 N 次子会话）；单跳内部的策略仍是子代理提示词。
  - 执行语义：**与原生 subagent 的默认前台模式一致——阻塞**（`execute` 内 `await
    session.prompt`；父会话在 await 期间挂起，返回后把文本作为 tool result 才继续本轮）。
    **不支持后台模式**：插件 `ToolContext` 无 background 原语，Task 的可选 `background: true`
    不适用于本工具。长调用以 `metadata()` 报进度、受 hop 预算与**超时**约束、可 `abort`。
  - 相比 skill 的规劝，B 是**代码契约**（委派与预算由我们的代码定）；代价是**插件随包分发**，
    且插件**运行在宿主进程内、信任级高于外部 MCP daemon**（owner 2026-09-21 采纳）。
- 边界（如实标注）：
  1. `format: json_schema` 结构化输出在当前模型**不可用**——provider 返回 400
     `Thinking mode does not support this tool_choice`（`info.error`，`structured_output=null`）；
     需换支持该 tool_choice 的模型，**与重入无关**（同一调用正常返回并带回错误）。
  2. 本 spike 只用一次性 `session.prompt` 返回，**未验证**流式 / 中途事件。
  3. 仍只解决**隔离与控流**，不修早停、不构成对 skill 行为的验证（同 D10 与 #60 后置）。
  4. **隔离未被独立验证**：探针只回传子会话最终文本，主会话结构性看不到召回块——**设计使然**，
     未对父会话消息列表做断言（H2 记为"未验证"）。
  5. P2 的记忆检索结论受工具 `-32001` 超时污染，**不作为检索质量证据**引用。
- **D8 不变**：宿主 enforcer / 插件随包分发仍是部署决定，非包能力。

---8<--- 到此行以上追加 ---
