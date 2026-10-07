# 0031 monorepo 双产品：知识库（memory_agent）与检索 agent（retrieval_agent）+ 单向接缝

Status: **accepted**（2026-10-06 owner 定形态：**共享一个仓库，但两者都可独立部署**；
**2026-10-07 owner 接受**——形态与 D1–D9 全部生效，P1（#68）开工）。

Relates: **ADR-0024**（可安装包与命名空间）、**ADR-0025**（个人模式知识库模型）、
**ADR-0026**（agentic 测量协议）、**ADR-0028**（一键部署与跨平台）、
**ADR-0030**（检索 agent 深度路线 **H→A→B**（2026-10-07 由 `H→A→B→C` 收窄，见其 D8），
本 ADR **只改归属，不改路线**）。

## 背景

**触发**：owner 判定——「**知识库就做知识库，agentic 检索就做 agentic 检索，两者本来就是交集
关系**」。同时简历目标（AI Agent 开发岗）要求存在**一个完整的 agent 项目**，而不是知识库里的一个
子功能。

**现状（问题）**：`memory_agent/` 一个包里同时装着两类语义——

| 语义 | 现在住哪 |
|---|---|
| 知识库：基表（文件+git）、派生索引、写入网关、生命周期、治理、MCP 工具面 | `memory_agent/{corpus,memory,gateway,mcp_server,ingest,...}` |
| 检索 agent：代码控循环、工具装配、trace 契约、评测 harness | `memory_agent/{agent_loop,trace.py,eval/harness}` |
| 归属未定 | `skill/SKILL.md`（工具用法 + 迭代策略混合）、`agent/memory-research.md`、`plugin/memory-research.js` |

结果是：agent 的深度（#62 H 已落地）在叙事上被"知识库包"吸收；而真拆两个仓库又会让
**交集证据**（题集 / 联调 / 契约测试）断裂——那才是真正的割裂来源。

## 决策

- **D1 形态 = monorepo 双产品**：`memory_agent`（知识库）与 `retrieval_agent`（检索 agent）
  各自是**独立可安装、可独立部署**的包；**仓库边界 ≠ 部署边界**——三者需要对齐的是
  **包边界 / 部署边界 / 依赖方向**，与 git 仓库无关。物理拆仓 **后置且可选**（见 D7）。
- **D2 依赖方向与接缝**：`retrieval_agent` → `memory_agent` **单向**，且**只经接缝**：
  **MCP 工具面 + 公开接口**，**禁止 import KB 内部**。接缝是**数据契约**（MCP 工具 schema +
  `trace` JSONL），两侧各有契约测试；配单向依赖**守卫测试**（静态 + 运行时双检，
  形状沿既有先例 `tests/unit/test_agent_loop_isolation.py`）。
- **D3 独立部署 = 三条硬指标（验收）**：
  1. **独立安装**：`pip install -e memory_agent` 与 `-e retrieval_agent` 各自成立；
     **agent 侧依赖集不得含 torch / sentence-transformers / qdrant-client / fastembed**
     ——模型与索引留在 KB 的 daemon 里，agent 经 MCP 借它。这条同时是最好的边界测试。
  2. **独立运行**：KB = 常驻 daemon（持模型）；agent = CLI / 插件，可指向**任意 MCP 兼容**后端。
  3. **独立升级**：KB 换索引 / 换融合不触碰 agent 循环；接缝靠契约测试锁死。
- **D4 交集落在 `interop/`**：题集数据 + **跨产品联调脚本** + 数字（"agent → MCP → KB → 在题集上出结论"）。
  **判据：没有跨产品联调证据 = 割裂**；有它，两个产品在叙事上就是一条线。
- **D5 工具面（ACI）归属 = agent 层独占**：
  - 工具的**命名 / 描述 / 参数 / 错误语义 / 权限口径**全部由 `retrieval_agent` 定义；
  - KB 只出**通用接缝**（MCP 工具 / HTTP / SQL 形态的能力），**不承担 agent 语义**；
  - 因此未来加 **web search / 数据库** 等工具 = **agent 层加一个 adapter + 一条工具描述**（一处改动），
    **不产生新的割裂**。反模式（否决）：每个后端各定义一套 agent 工具。
- **D6 归属表**：

  | 归 `memory_agent`（知识库） | 归 `retrieval_agent`（检索 agent） |
  |---|---|
  | 基表 / 派生索引 / 代与指针 | 代码控循环（预算 / 终止 / 充分性接缝） |
  | 写入网关 / 生命周期（supersede·archive） | 工具装配与派发（ACI） |
  | 治理（authn / authz / 审计 / 域 / 密级） | `trace` 契约（运行时只写、评测只读） |
  | MCP 通用工具面 | 评测 harness / 场景集 / 统计 |
  | 语料与条目级 gold | LLM provider 解析（opencode-server / openai-compat） |
  | 一键部署（`install.sh`/`install.ps1`/npx） | 未来的多 agent / 深度研究编排 |

  **有争议物件的细则**：`skill/SKILL.md` **按内容拆**——「工具用法」部分 = KB（像 API 文档），
  「迭代检索策略」部分 = agent；`agent/memory-research.md`（subagent 定义）与
  `plugin/memory-research.js`（组合工具）= **agent**。**交付者 ≠ 安装者**：安装动作仍可由
  KB 的 deploy 一并落位，避免部署入口分裂。
- **D7 迁移路径（先边界、后门牌，不返工）**：
  **P1 逻辑分离**（本仓建 `retrieval_agent/` 包 + 迁移 `agent_loop`/`trace.py`/`eval/harness`
  + 单向依赖守卫 + `interop/` 骨架）→ **P2 接缝**（agent 的工具适配器由进程内 `MemoryIndex`
  改为 **MCP 客户端**；**先量一次往返延迟**再决定是否缓存）→ **P3 物理拆仓**（可选，
  边界已干净则零重构）。**反向顺序（先开新仓）会逼在边界未定时搬代码，是最割裂的路径。**
- **D8 不变量**：**不碰检索默认 / 合成**；冻结区（共享 / 云 #38/#34/#55、ADR-0029）不动
  （**#39 于 2026-10-07 由 owner 解冻开工**，不属本 ADR 范围）；ADR-0025 的基表模型不变；
  **ADR-0030 的路线 `H→A→B` 不变**（2026-10-07 由 `H→A→B→C` 收窄，见其 **D8**），只是
  H/A/B 的归属从 "`memory_agent` 内部"改为"`retrieval_agent` 项目"，且 **A/B 的工具面全部归
  agent 层**（沿 D5）。
- **D9 命名**（可改，不改边界）：包 `retrieval_agent`；console 入口 `retrieval-agent`；
  与既有 `memory_agent` / `memory-agent` 对称。

## 理由

- **交集是可复现的证据，不是共享代码**：题集 / 联调 / 契约测试同仓 → 跨产品结论永远可复现；
  这正是"割裂"的反面。
- **接口演进期一次 PR 改两边**：真拆仓时这条最痛（跨仓 PR + 版本对齐），现在是免费红利。
- **"完整 agent 项目"与"知识库项目"各自成立**：agent 可指向任意 MCP 兼容后端 → 叙事是
  "可插拔后端的检索 agent runtime + 确定性评测"，KB 是本库事实的权威 + 一个后端实现。
- **部署解耦有硬验收**（D3.1）：agent venv 不装 torch 才算真的独立；这条也是最好的架构守卫。
- **加工具不割裂**：D5 把 ACI 收在一处，web / DB 只是新 adapter。

Considered options：

- **单包（现状）**——最省事，但"知识库"继续吸收 agent 的叙事权重；简历要的完整 agent 项目不成立。**否决**。
- **monorepo 双产品（采用）**——边界清楚 + 交集同在 + 独立部署成立 + 未来可零重构拆仓。
- **立刻真拆两仓**——观感上最"独立"，但接口演进期成本高、交集证据易断，且边界未定时搬代码会返工。**后置为 P3**。
- **把 agent 层外包给开源 agent 框架**——会撤掉 H 已落地的稀缺资产（自建 runtime / trace 契约 /
  确定性评测），且 ACI 不再自持（工具描述质量直接决定 agent 质量）。**否决**。
- **每个后端各出 agent 工具（MCP-per-backend）**——上下文塞满无关工具、ACI 不一致，直接拉低 agent 质量。**否决**（见 D5）。

## Consequences

- 新票：**P1**（逻辑分离：建包 + 迁移 + 守卫 + `interop/` 骨架）、**P2**（接缝改 MCP + 延迟测量）、
  **P3**（可选物理拆仓，落地后不再另开 ADR）。
- **地图变更**：P1 落地后 AGENTS.md 从"单产品 + 内部模块"改为"**双产品 + 一个接缝**"，
  `CONTEXT.md` 补 `retrieval_agent` / 接缝 / interop 的领域词。
- **风险**：① MCP 往返引入延迟（P2 必量，先量后优化）；② 迁移期路径变更会牵动测试与文档
  （一次性的 churn，P1 内消化）；③ 若 agent 侧日后引入重依赖，"独立部署"即失效——由 D3.1 的
  依赖验收卡住。
- **对 ADR-0030 的影响**：**路线 = `H→A→B`**（2026-10-07 由 `H→A→B→C` 收窄，其 D8），
  验收锚点不变；`H` 已落地的代码在 P1 中整体迁入 `retrieval_agent`。
