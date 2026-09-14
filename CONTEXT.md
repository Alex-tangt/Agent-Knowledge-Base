# CONTEXT

> 项目领域语言。本仓库现含两个子域：**Agent 知识库（Agent Knowledge Base）** 的产品形状（记忆能力包为首个能力）与查询改写优化器实验。

## 产品形状（Project Shape）

**Agent 知识库（Agent Knowledge Base）**：
项目定位。把 Markdown 知识库变成 agent 可安全读写、可检索的长期知识底座；当前聚焦"记忆能力包"这一个能力；北向是企业级 agent 知识库管理（多租户访问权限、缓存、降级、内容生命周期）。
_Avoid_: 通用 RAG 框架、个人 RAG 工具（起点形态，已被本定位取代）

**记忆能力包（Memory Capability Package）**：
本阶段交付物。以工具协议（MCP）+ 技能（skill）对外暴露，让 coding agent 读写长期记忆。
_Avoid_: 独立 app、纯库

**适配层（Adapter）**：
对外暴露通道，与核心服务解耦。当前：`legal_web`（网页旗舰 demo，后续升级为 agent 体验用例）与 `memory_agent`（记忆能力包）。
_Avoid_: 把适配层逻辑混进核心服务

**决策路径（Decision Trail）**：
从"问题 → 假设 → 实验 → 数据 → 结论"到最终决定的完整链条，每一步都有明确落点文档可回溯。
_Avoid_: 把 POC 实验、日志、结果混在目录树里却不留结论

**基线（Baseline）**：
敢签名、可上简历的代码状态，作为版本管理的干净起点。是"达标态"而非"现状快照"。
_Avoid_: 把已知缺陷的现状直接当作基线提交

**缺陷分层（Defect Tiers）**：
对已知问题的三档处置——缺陷（bug，基线前必修或明确降级为已知限制）、调优欠账（tuning debt，冻结到"够好"并记录结论）、实验未决（unconcluded research，封存结论）。调优无底洞是本项目被玩坏的头号原因。

## 记忆（Memory）

**记忆（Memory）**：
agent 跨会话持久化的知识，落在知识库的 Markdown 条目中。
_Avoid_: 会话上下文、短期上下文

**会话记忆（Session Memory）**：
单次会话内的短期对话上下文，内存存储、易失，仅用于查询补全。
_Avoid_: 长期记忆、知识库

**真相源（Source of Truth）**：
记忆真正落盘之处——Markdown 条目及其 frontmatter 元数据；可版本化、可回退。
_Avoid_: 索引、数据库

**派生索引（Derived Index）**：
从真相源重建、可随时丢弃的检索加速结构，不承载记忆本体。
_Avoid_: 真相源、存储

**写入网关（Write Gateway）**：
强制"先检索去重 → 写 → 校验"的写入通道；新增可自动，替代/归档类破坏性操作需确认。
_Avoid_: 裸文件写

**supersede（取代）**：
有替代的更新——新条目取代旧条目，旧条目退役但保留，二者互相指认。
_Avoid_: 原地改写、删除

**archive（归档）**：
无替代的退役——内容不再相关，标记归档但不删除。
_Avoid_: 删除（rm）

**只读语料（Read-only Corpus）**：
被检索但不允许写入的来源（如项目仓库文档），与可写记忆区分。
_Avoid_: 记忆

**条目级索引（Entry-level Index）**：
以"一个条目一个索引单元"为粒度的索引约定；增量重建以条目为单位。
_Avoid_: 固定窗口切块

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
