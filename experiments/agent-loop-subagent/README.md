# agent-loop-subagent —— `memory-research` 子代理的人工小抽查（#60）

> 状态：**待跑**（需先满足下面的 provider 前置）。本目录只放**实验变体**与说明；
> 默认变体随包交付于 `memory_agent/agent/memory-research.md`。
> 决策来源：`docs/adr/0026-agentic-loop-measurement-protocol.md` 追加节（D9–D12）。

## 问题

主对话里跑多跳检索会把 hop / 召回块 / judge 输出灌进主上下文（Phase B 证据上限
20 篇 × 6000 字 ≈ 120k）。把循环放进 **opencode 原生 subagent 的独立子会话**，
能否在**不污染主对话**的前提下拿到结论，并在证据不足时**如实弃答**？

## 假设

- H1：主 agent 经 Task 委派 `memory-research` → 主对话**只出现结论**，**不出现**召回块。
- H2：证据不足的题 → 子代理回 **`INSUFFICIENT`**，不硬答。
- H3：`steps: 8` 能兜住成本（不出现无界迭代）。

## 设置

- **变体**：`memory-research-qwen.md`（钉 `model: dashscope/qwen3.7-flash`；默认变体**不写 model = 继承**）。
- **前置（阻塞）**：opencode 需先有 `dashscope` provider。参考配置（**key 走环境变量，别落盘**）：

  ```jsonc
  "provider": {
    "dashscope": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DashScope",
      "options": {
        "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKey": "{env:DASHSCOPE_API_KEY}"
      },
      "models": { "qwen3.7-flash": { "name": "Qwen3.7 Flash" } }
    }
  }
  ```
  > 坑：`{env:DASHSCOPE_API_KEY}` 缺变量会变**空串**→鉴权静默失败；配之前先确认该环境变量真实存在。
  > 落位变体：把本目录的 `memory-research-qwen.md` 复制到 `~/.config/opencode/agents/`，重启 opencode。
- **题**：3–5 道**多跳 / 多约束**（跨条目）题 + 1 道**本 KB 无证据**的题。
- **模型**：qwen3.7-flash（主 agent 可仍用别的；只看被派发的子代理）。

## 数据

- 逐题记录：主对话里**是否出现召回块**（截图 / 文本）、子代理**结论 + id**、是否 `INSUFFICIENT`、
  子会话**轮数 / tool-call 数**。
- 结果落 `results.md`（本目录）。

## 结论

**待跑**（尚未执行；跑完补此节 + `results.md`）。

## 边界（如实）

- 这只验**上下文隔离 / 弃答 / 成本兜底**，**不验** skill 的迭代是否有效、**不修早停**
  （那仍需 in-package harness + in-domain 多跳集；见 `docs/adr/0026` D1 层 2 剩余）。
- 外部/本 KB 的迁移性限制照 ADR-0026 D5。
