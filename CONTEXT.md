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

**视图（View）**：
一份**具名谓词**，规定哪些**域**的哪些**条目**可见——"看什么"的范围定义；**只读**
（写永远走域，如数据库的 VIEW 与基表）。软件能看见哪些视图 = 授权；网关的 tenant/密级
过滤是对视图的**强制收窄**（只可收窄，ADR-0018 D2）。最终可见 = 视图 ∩ 授权。
_Avoid_: 库、知识库（**技术语境一律改用「视图」**）、把视图当物理集合

**域（Domain）**：
一个带标签的**来源**（一个目录 / 仓库 / 工具自己的存储），**就地索引、不搬运**，有自己的
**所有者**（**写权限单位**）。只读域 = 无写者（如只读语料）。
_Avoid_: 把域当物理集合、把域当视图

**集合（Collection）**：
物理的向量索引结构（向量 + payload），按**同构**切分（同嵌入模型 / 同粒度才可同集合）；
**不等同于视图**——一份集合可服务任意多个视图，视图只是查询期的过滤。
_Avoid_: 把集合当视图、一视图一集合

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

**仓库标签（Repo Label）**：
只读语料根的名字，用作 `source` 前缀以消歧义（不同仓库都有 README/CONTEXT）；只读条目
id = `repo:<label>/<rel>`，标签重复加 `-2` 后缀。
_Avoid_: 裸相对路径（跨仓库会静默撞 id）

**存储端口（VectorStore Port）**：
记忆检索依赖的存储抽象（`add/search/delete/count/clear/warmup`）；`MemoryIndex` 只经端口 +
工厂拿存储，不认识 Qdrant local mode 细节。本地 Qdrant 是当前唯一实现，云 store 适配同一端口。
_Avoid_: 让索引代码直接构造具体向量库客户端

**驻留 / 密级（Residency / Classification）**：
条目 payload 的治理维度。`classification ∈ {private, internal, public}`（缺省 private）、
`residency ∈ {local, cloud}`（缺省 local）。frontmatter 是真相源且**可选**，索引 payload 只是
供过滤用的镜像。
_Avoid_: 把索引里的值当真相、强制要求全局 KB 填写

**检索网关（Retrieval Gateway）**：
在 `/mcp` 边界解析身份、在工具层按身份 entitlement **白名单构造** effective filter（**只可收窄**）
的强制层；`tenant`（隔离边界）与 `classification` / `residency`（ABAC）都在此落地。与
「写入网关（Write Gateway）」不同——后者管写入去重与生命周期，前者管读权限与隔离。
_Avoid_: 把 proxy 当信任源、由调用方自选 tenant

**身份 / 授权（Identity / Authorization）**：
身份 = `{principal, tenant, role, ABAC 允许集}`。单租户阶段 = 进程配置；多租户形状 =
daemon 在 `/mcp` 边界校验的 Bearer token → 身份映射。授权 = 调用方请求 ∩ entitlement，
越权**显式拒绝**。凭证绝不回显 / 落日志。
_Avoid_: 用可伪造 header 当身份、把 UNAUTHORIZED 静默降级为默认身份

**来源标注（Provenance）**：
检索命中自带 `{plane, tenant}`，指明结果来自哪个存储平面 / 租户；联邦召回时逐条区分来源。
_Avoid_: 只给聚合结果、丢失来源

**条目级索引（Entry-level Index）**：
以"一个条目一个索引单元"为粒度的索引约定；增量重建以条目为单位。
_Avoid_: 固定窗口切块

**代（Generation）**：
派生索引的一次完整构建产物，落在独立目录 `INDEX_DIR/<gen>/`；由 `CURRENT` 指针指认当前代。全量重建建好新代后**原子切换指针**，中断不影响在服务的旧代。
_Avoid_: 就地清空重建、把"删集合再建"当原子

**自洽核对（Consistency Check）**：
检索前核对 manifest 条数与集合点数是否相等；不等即显式报错，绝不静默返回空结果。
_Avoid_: 表面正常、实际搜不到

**共享单实例 daemon（Shared Single-instance Daemon）**：
唯一持有嵌入引擎的常驻进程；所有 opencode 会话共享它，而不是各自加载一份模型。只绑本机回环，`/health` 供就绪探测。
_Avoid_: 每会话一份引擎、把 daemon 当可选优化

**代理（Proxy）**：
opencode 每会话的 `local` 命令：先幂等确保 daemon 在跑，再把本会话 stdio 请求透明转发过去。是"谁的进程生命周期负责拉起 daemon"这一问题的答案（见 ADR-0013 D1）。
_Avoid_: 把代理当第二份引擎、让 opencode 直连 remote 却不解决 daemon 启动

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
