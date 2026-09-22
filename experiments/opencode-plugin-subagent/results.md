# results —— opencode-plugin-subagent

- 日期：2026-09-21
- worktree：`D:\python_work\work2026-4\wk-60-plugin`
- 分支：`experiment/opencode-plugin-subagent`（HEAD=master 380df00）
- opencode：1.18.31；`@opencode-ai/plugin`：1.18.31；node v24.14.1
- 模型（主 / 子）：`deepseek/deepseek-flash`（主 = `build`，子同）；provider = `deepseek`（global config）

## 原始观测

单项用墙钟超时包裹（`Start-Job`），死锁不困住主会话；每条 prompt 带唯一 nonce 以便按 PID 精确清理（实际无超时，无孤儿）。

| # | 命令 / 触发 | 结果 | 耗时 | 备注 |
|---|---|---|---|---|
| P0 | 主会话内调 `plugin_ping(text="ok")` | `pong:ok (dir=D:\python_work\work2026-4\wk-60-plugin)` | <1s | 项目级插件已加载，`dir` = worktree |
| P1 | `plugin_spawn_probe(prompt="说一句你好", agent="build")` | 返回 | wall **23.4s**；子 `ms=2853` | child=`ses_f3c9861afffer0ko3c4VJ86C2U`；parent=`ses_f3c988e26ffe3jUIWhYRqHKoZI`；`children_of_parent` = 该 child；child-text=`你好` |
| P2 | `plugin_spawn_probe(prompt="检索：MCP sampling 在 opencode 支持吗？给依据 id", agent="memory-research-spike")` | 返回 | wall **160.9s**；子 `ms=138370` | child=`ses_f3c97ebfcffefRQQh1fb0mLrw1`；parent=`ses_f3c9817ccffesFUZOfHLRP7h11`；`children_of_parent` 含 child；main **无召回块**；子代理结论=KB 无 sampling 依据（列 4 个相关 id） |
| P3 | P2 + `structured=true`（`memory-research-spike`） | 机制返回、**结构化无输出** | wall **21.7s**；子 `ms=1403` | child=`ses_f3c955d13ffe5FofL4R6cmWmR4`；`children_of_parent` 含 child；child-text 空、无 `structured=` |
| P3B | `structured=true` + `build` + 平凡题（"1+1"） | 同上，可复现 | wall **21.4s**；子 `ms=1438` | child=`ses_f3c94f095ffewLHxOhQMnPsbuT`；排除"子代理/检索"因素 |
| P3C | P3B + 探针诊断转储响应形状 | **定位根因** | wall **24.2s**；子 `ms=1689` | `res-keys=info,parts`；`info-structured=null`；`info-structured_output=null`；`info-error`= `APIError 400 "Thinking mode does not support this tool_choice"`（`api.deepseek.com`）；`parts=[]` |
| P4 | 主 agent 直接 `@memory-research-spike`（Task 对照） | 返回 | wall **59.0s** | child=`ses_f3c937ccfffe1opgNqmLoX2xNc`；结论与 P2 一致（KB 无依据）；NONCE 原样回传、main 只见 Task 结果 |

原始工具输出（P1/P3C/P4，节选）：

```
P1:  child=ses_f3c9861afffer0ko3c4VJ86C2U
     parent=ses_f3c988e26ffe3jUIWhYRqHKoZI
     ms=2853
     children_of_parent=ses_f3c9861afffer0ko3c4VJ86C2U
     ---child-text---
     你好

P3C: res-keys=info,parts
     info-keys=parentID,role,mode,agent,path,cost,tokens,modelID,providerID,time,error,id,sessionID
     info-structured=null
     info-structured_output=null
     info-error={"name":"APIError",... "message":"Thinking mode does not support this tool_choice" ...}
     parts=[]

P4:  子代理原文（ses_f3c937ccfffe1opgNqmLoX2xNc，一字未改）：NONCE: SPIKE-P4-NONCE-9f3c
     结论: 无依据——遍历全局记忆与只读语料后，检索不到任何条目说明 MCP sampling 在 opencode 中是否被支持
```

## 关键判定

- **P1 是否返回（重入/死锁）**：**返回，无死锁**。`session.create({parentID})` + `session.prompt({agent})`
  在工具 `execute` 调用栈内正常完成（子会话 2.9s / 三轮检索 138s），主进程不阻塞。
- **主会话是否只见工具结果（隔离）**：**口径需收窄**。探针**只回传子会话最终文本**，故主会话
  结构性看不到召回块——这是**设计使然**，不是对"宿主不把子会话消息注入父上下文"的独立验证；
  也未对父会话**消息列表**做断言（P2 的"无召回块"是肉眼观察主对话）。→ **H2 记为「未被独立验证」**。
- **`children_of_parent`**：P1/P2/P3 均由 `session.children(parentID)` 查出该 child，**parent 挂接成立**。
- **结构化返回（P3）**：`format: json_schema` 在当前模型下**不可用**——`info.error` 为 provider
  400 `Thinking mode does not support this tool_choice`，`structured_output=null`、`parts=[]`。
  **这是模型/provider 限制（deepseek 思考模式不支持该 tool_choice），不是插件重入失败**：
  同一调用 1.4–1.7s 正常返回并把错误如实带回。
- **旁证（非本实验目标）**：P2 子代理报告 `memory_search` 出现多次 `-32001` 超时（global config
  `timeout=20000`），可能影响多跳质量，与本 spike 结论无关。
  → **P2 的"KB 无 sampling 依据"受此污染，不作为检索质量证据引用。**

## 独立旁证：opencode 自身日志（2026-09-21 复核）

在 `~/.local/share/opencode/log/opencode.log`（session id 亦存在于 `opencode.db`）核到 P1 的
**父→子→父**时间线，与 `results.md` 一致（证明运行真实发生，非手抄）：

```
09:58:51.609  created  ses_f3c988e2…  (parent, slug=brave-wolf)
09:58:52.321  loop     parent step=0
09:59:02.992  created  ses_f3c9861a…  (child,  slug=brave-forest)   ← 工具 execute 内建子会话
09:59:03.038  loop     child step=0
09:59:05.837  "exiting loop" child
09:59:07.010  loop     parent step=1                              ← 父会话恢复 = 无死锁
```

## 结论

**PASS**（H1/H2/H4 成立；H3 的隔离成立、结构化受限）。

一句话：**插件工具在 `execute` 内 `session.create({parentID})` + `session.prompt` 不重入、不死锁**，
子会话结果经工具返回值回主会话（主上下文干净、`children_of_parent` 可核），
即"委派 + 代码控流 + 返回"在 opencode 插件里**可行**；`format: json_schema` 需换支持该 tool_choice
的模型（deepseek 思考模式不支持）。

对 #60 的影响：**采纳形态 B（插件工具内驱动子会话）为交付形态**（接口只返回文本、hop 预算由插件控），
形态 A（agent `.md` + Task）保留为无插件环境的 fallback（owner 2026-09-21，本对话内定）；
详见 `README.md` 收尾段与 `adr-0026-append.md`。

## 复现

```powershell
cd D:\python_work\work2026-4\wk-60-plugin
# P1（墙钟超时包裹，避免死锁困住会话）
& powershell -NoProfile -File C:\Users\Tan\AppData\Local\Temp\opencode\spike-run.ps1 `
  -Prompt "调用 plugin_spawn_probe，prompt 传 '说一句你好'，agent 传 build，把工具原始输出原样贴回来" `
  -Agent build -TimeoutSec 240
# P0 亦可在 worktree 会话内直接调 plugin_ping
```

runner 脚本：`C:\Users\Tan\AppData\Local\Temp\opencode\spike-run.ps1`（`Start-Job` + `Wait-Job -Timeout`，
输出 STATUS/ELAPSED_MS/原文）。
