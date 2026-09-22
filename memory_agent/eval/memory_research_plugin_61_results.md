# #61 宿主 E2E：memory_research 插件工具（形态 B）

- 日期：2026-09-22
- 环境：opencode **1.18.31**；模型 `deepseek/deepseek-flash`（主 / 子）；全局安装
  `~/.config/opencode/plugins/memory-research.js`（+ agent `memory-research`）。
- 方式：`opencode run --auto "<显式要求调用 memory_research 并贴回原始输出>"`，用
  `Start-Job + Wait-Job -Timeout` 包住（死锁即超时）；workdir = 主仓。
- runner：`C:\Users\Tan\AppData\Local\Temp\opencode\plugin-e2e-run.ps1`。

## 观测

### P1 正常检索（有证据）

prompt：`请调用 memory_research，query 传 '本仓库 memory_agent 默认的词法路是 BM25 还是 tfidf？给出依据 id'…`

工具被调用，返回（节选）：

```
**结论**：本仓库 memory_agent 本地平面的默认词法路是 BM25（不是 tfidf）…
**依据 id**：
- repo:agent-knowledge-base/docs/adr/0019-memory-store-port-residency-classification.md
- topics/memory-runtime …
- repo:agent-knowledge-base/docs/retrieval_optimization_report.md
- repo:agent-knowledge-base/experiments/local-lexical-40/README.md

[memory_research: hops=1 elapsed=101.5s]
```

- wall **125.9s**；子会话 `elapsed=101.5s`；**未死锁**。

### P2 无证据（期望弃答）

prompt：`必须调用 memory_research，query 传 '2027 年诺贝尔文学奖得主是谁？'…`

```
INSUFFICIENT

[memory_research: hops=1 elapsed=11.1s]
```

- wall **32.4s**；**未死锁**；**如实弃答**（未硬答）。

## 判定

| 项 | 结果 |
|---|---|
| (a) 返回结论 + id | ✅ P1 |
| (b) 父会话无召回块 | ✅ **程序化断言 PASS**（见下）：父会话 part 里**不含**子会话任何检索 chunk 片段 |
| (c) 无证据回 `INSUFFICIENT` | ✅ P2 |
| (d) 不死锁 | ✅ P1/P2 均在超时内 DONE |

## H2 程序化断言（父会话隔离）

脚本：`memory_agent/eval/h2_parent_isolation_check.py`（只读 `opencode.db`）。做法：

- 取 `title like 'memory_research:%'` 的子会话与其 `parent_id`；
- 抽子会话里 `type=="tool" && status=="completed"` 的 **`state.output`**（= `memory_search/get`
  的返回正文，即 chunk）；按 200 字切片；
- 断言这些片段**不出现在父会话的任何 part**（子会话最后的 assistant `text` = 插件正常回传
  的答案，**不参与**检查）。

结果：

| 子会话 | 完成工具数 | chunk 字节 | 检查片段 | 泄漏 |
|---|---|---|---|---|
| P2（无证据） | 1 | 3022 | 15 | **0** |
| P1（有检索） | 4 | 20418 | 100 | **0** |

**PASS**——父会话未出现任何检索 chunk（此前"观察口径"的残留已补）。

## 旁证：安装器修复（真实环境暴露）

首次真实安装发现 `~/.config/opencode/package.json` **本就存在**（`@opencode-ai/plugin: 1.17.18`，
他工具所装），旧实现把它**覆盖成 `^1.18.0`**。已修：**已存在则不覆盖**；卸载**不删共享依赖**。
已从备份恢复为 `1.17.18`，重跑安装显示 `依赖已是最新 / plugin unchanged`。回归见
`tests/unit/test_deploy.py`（`test_merge_package_dependency_adds_but_never_overwrites` 等）。

## 复现

```powershell
# 前置：memory-agent install（默认落 plugin；--no-plugin 可跳过）
& powershell -NoProfile -File C:\Users\Tan\AppData\Local\Temp\opencode\plugin-e2e-run.ps1 `
  -Prompt "请调用 memory_research，query 传 '<问题>'，把工具返回的原始文本原样贴回来。" -TimeoutSec 300
```
