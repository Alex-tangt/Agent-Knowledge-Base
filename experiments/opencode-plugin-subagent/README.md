# opencode-plugin-subagent —— 插件工具内驱动 subagent 的可行性（重入/死锁）

> 状态：**已跑 → PASS**（2026-09-21）。worktree = `wk-60-plugin`，分支 `experiment/opencode-plugin-subagent`。
> 关联：`docs/adr/0026-agentic-loop-measurement-protocol.md` 追加节（D9–D12）、#60。
> **本实验不改全局 opencode 配置**（插件是项目级 `.opencode/plugins/`）。

## 问题

MCP server 无法驱动宿主 subagent（无 sampling / 无会话 API，D9）。但 opencode **插件**
拿到的是**进程内 SDK client**（`packages/opencode/src/plugin/index.ts`），且
`session.create`（含 `parentID`）、`session.prompt`（含 `agent` / `model` / `format`）都可用
（`packages/schema/src/v1/session.ts`；`session/prompt.ts`）。

真正未知：**在工具 `execute` 调用栈里回调 `session.prompt` 会不会重入/死锁**——Task 走的是
Effect 内部 `promptOps`，插件走 SDK 客户端，可能阻塞同一事件循环。

**若可行**，就能把"委派 + 循环 + 返回"做成**代码契约**（我们的代码控流），比 skill 的规劝稳健，
且用宿主 LLM、无需给 daemon 配 key。

## 假设

- H1（P1）：插件工具内 `session.create({parentID}) + session.prompt({agent})` **能返回**子 agent 结果，
  不超时、不递归报错。
- H2（P1）：主会话**只出现工具结果**（子会话内容不进主上下文）。
- H3（P2/P3）：用 `memory-research-spike` 子代理时，检索过程不污染主对话；`format` 能拿到结构化返回。
- H4（P4 对照）：主 agent 直接 Task 同款子代理，隔离表现可比。
- 反假设：若 P1 死锁/超时 → **否定结论**，维持 #60 的形态 A（agent `.md` + Task）。

## 设置

- **worktree**：`D:\python_work\work2026-4\wk-60-plugin`（分支 `experiment/opencode-plugin-subagent`，HEAD=master `380df00`）。
- **插件（项目级，只作用于本 worktree）**：`.opencode/plugins/spike-tool.js`
  - `plugin_ping`（P0 管道）、`plugin_spawn_probe(prompt, agent?, structured?)`（P1–P3）。
  - **副本随实验提交**：`spike-tool.js`（含 P3 根因诊断转储）。
- **依赖**：`.opencode/package.json` 钉 `@opencode-ai/plugin@1.18.31`（与 `opencode --version` 一致；opencode 启动会 bun install）。
- **子代理（项目级）**：`.opencode/agents/memory-research-spike.md`（`mode: subagent`，只读记忆工具）。
  **副本随实验提交**：`memory-research-spike.md`。
- **`.opencode/` 脚手架（插件/agent/node_modules）故意不入库**：它是 worktree 级运行配置，
  并入 master 会让插件对整个仓库默认加载。实验可复现性由本目录的两个副本保证。
- **MCP**：沿用全局注册 `memory-agent`（绝对路径指向主树 `proxy.py`）——**只读查询**，不写。

## 数据（P0–P4）

| # | 触发 | 记录 |
|---|---|---|
| P0 | 让模型调 `plugin_ping` | 插件是否加载、工具是否可见、返回串 |
| P1 | `plugin_spawn_probe(prompt="说一句你好", agent="build")` | **是否返回 / 是否死锁 / 耗时 / child id / parentID / 主会话是否只见结果** |
| P2 | `plugin_spawn_probe(prompt="<一道记忆检索题>", agent="memory-research-spike")` | 主对话有无召回块；结论 + id |
| P3 | P2 + `structured=true` | `structured=` 是否出现、内容 |
| P4 | 主 agent 直接 Task `memory-research-spike` | 对照隔离 |

**怎么触发**：`opencode run "<显式要求调用 plugin_spawn_probe 并贴回原始输出>"`，或用 TUI。
所有 run 加 **墙钟超时**（死锁即结论）。**不得用宽口径进程名杀进程**。

## 判定

- **PASS**：P1 超时内返回；主会话只有工具结果；`children_of_parent` 含该 child。
- **FAIL（＝结论）**：死锁 / 超时 / 递归错误。
- **PARTIAL**：能返回但未挂 parent，或内容漏进主上下文 → 记录并评估。

## 结论

**PASS（2026-09-21，模型 `deepseek/deepseek-flash`，opencode 1.18.31）**。原始观测见 `results.md`。

- **P1 无死锁**：插件工具 `execute` 内 `session.create({parentID}) + session.prompt({agent})` **正常返回**
  （子会话 2.9s；三轮检索 138s），主进程不阻塞。
- **隔离成立**：主会话只见工具结果；子会话召回块 / hop 不入主上下文（P2 确认无召回块；
  P4 Task 对照同款隔离）。
- **parent 挂接成立**：`session.children(parentID)` 含该 child（P1/P2/P3 均验证）。
- **H3 结构化受限**：`format: json_schema` 在当前模型 400 `Thinking mode does not support this tool_choice`
  → `structured_output=null`、`parts=[]`；**属模型/provider 限制，非重入失败**（同一调用正常返回并带回错误）。
  需结构化输出时换支持该 tool_choice 的模型。
- **旁证**：P2 子代理报告 `memory_search` 多次 `-32001` 超时（global config `timeout=20000`），与本结论无关。

结论：**插件工具内驱动 subagent 可行**——"委派 + 代码控流 + 返回"可做成代码契约，比 skill 规劝硬，
代价是插件需随包分发（部署面变化）。对 #60：形态 A（agent `.md` + Task，D10）仍有效；本实验给出**形态 B**
（插件工具内驱动）备选。

**收尾（owner 2026-09-21 决定）**：形态 A 不变、**不采用**形态 B、**不改 #60**；
ADR-0026 追加节先落成待 apply 片段 `adr-0026-append.md`（主树当时被 #60 会话改动，避免双写者），
等主树干净后再 apply。本分支**暂不合并**。
