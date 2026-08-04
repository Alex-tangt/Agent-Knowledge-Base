# CONTEXT

> 项目领域语言。本仓库包含两个子域：项目产品形状（个人 RAG 工具）与查询改写优化器实验。

## 产品形状（Project Shape）

**个人 RAG 工具**：
项目的定位。以 service 层 + 切分策略为可复用核心，通过适配层对外暴露的个人检索问答工具，政策法规问答是旗舰 demo 而非全部。
_Avoid_: 通用 RAG 框架（面向大众用户的抽象库）、垂直应用（政策问答只是消费方之一）

**适配层（Adapter）**：
个人 RAG 工具对外暴露的通道，与核心服务解耦。当前两个：FastAPI Web 前端（旗舰 demo）与 MCP 服务器（供其他应用集成）。
_Avoid_: 把适配层逻辑混进 service 层

**决策路径（Decision Trail）**：
从"问题 → 假设 → 实验 → 数据 → 结论"到最终决定的完整链条，每一步都有明确落点文档可回溯。
_Avoid_: 把 POC 实验、日志、结果混在目录树里却不留结论

**基线（Baseline）**：
敢签名、可上简历的代码状态，作为版本管理的干净起点。是"达标态"而非"现状快照"。
_Avoid_: 把已知缺陷的现状直接当作基线提交

**缺陷分层（Defect Tiers）**：
对已知问题的三档处置——缺陷（bug，基线前必修或明确降级为已知限制）、调优欠账（tuning debt，冻结到"够好"并记录结论）、实验未决（unconcluded research，封存结论）。调优无底洞是本项目被玩坏的头号原因。

---

## 查询改写优化器实验（Query Rewriting Optimizer）

## Glossary

| 术语 | 定义 |
|------|------|
| **改写 LLM** | 接收原始用户 query + 改写 prompt，输出关键词短语或子查询列表。每次调用无状态。 |
| **优化 LLM** | 接收每轮实验结果 JSON，产出 `{analysis, new_prompt}`。唯一有会话历史的 LLM 实体。 |
| **意图判断 LLM** | 比较原始 query 与改写后 query，输出 0.0~1.0 连续滑移分数。领域无关 prompt，每次调用无状态。 |
| **dev set** | 参与优化轮次的 query 集合，按 `best_dist_orig` 降序筛选（优先选原检索效果差的 query）。 |
| **test set** | 优化终止后一次性验证的 query 集合，不参与任何优化轮次。 |
| **norm_delta** | `(best_dist_orig - best_dist_rewrite) / best_dist_orig`，clamped to [0,1]。衡量改写带来的检索距离改善。 |
| **intent_drift** | 0.0~1.0 连续值，全部子查询拼接后与原始 query 做信息需求匹配打分。0.0=精确保留，0.2=丢一个约束，0.5=方向偏差，0.8=模糊，1.0=输出答案或完全偏离。 |
| **score** | `0.7 × norm_delta - 0.1 × intent_drift`，clamped to [-0.1, 0.7]。综合评分。 |
| **kb_context** | 静态文本，描述知识库内容（21 部法律法规清单），注入改写 prompt 中。仅 KB 内容变更时手动更新。 |

## Architectural Decisions

1. **三个 LLM 上下文完全隔离**：改写 LLM、意图判断 LLM、优化 LLM 各自独立会话，优化 LLM 通过改写 LLM 的**输出结果**间接感知质量，不共享上下文。
2. **优化 LLM 仅产出 prompt**：`analysis` 字段给人看，不触发任何系统级修改。若 `new_prompt` 与当前相同或为 null，视为放弃，优化终止。
3. **优化 LLM 会话记忆**：维护 OpenAI 格式 `messages` 数组，每轮追加 user(JSON metrics) → assistant(`{analysis, new_prompt}`)，全流程一个会话。
4. **intent_drift 修复**：拼接全部子查询后判定意图，保持权重 -0.3，先跑数据再决定是否调整。
5. **20 字符阈值移除**：不再基于长度跳过改写。
6. **dev set 筛选**：候选 query pool 跑原始检索，按 `best_dist_orig` 降序取 top 15+。
7. **KB 概况静态注入**：从 SOURCES.md 在 optimizer 初始化时生成，写入改写 prompt，不动态读取。
8. **输出格式**：优化 LLM 回复 `{"analysis": "<自由文本>", "new_prompt": "<新改写prompt或null>"}`。
9. **intent_drift 连续化**：首版实验 16 条 query 证实二元判定与关键词改写结构性冲突（15/16 被判 drift=1）。改为 0.0~1.0 连续打分，权重从 0.3 降至 0.1。评判 prompt 为领域无关的通用设计：0.0=精确保留，0.2=丢一约束，0.5=方向偏差，0.8=模糊，1.0=输出答案或完全偏离。
