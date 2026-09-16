# 0018 云平面多租户与权限治理（隔离 / 强制点 / 粒度 / 选型）

Status: accepted（**deferred：共享 / 云冻结**，2026-09-16 owner 决定——云多租户 / 隔离决策**不在当前计划**；网关（#32）保留，见 `docs/adr/0025`）

## 背景

Post-MVP 双平面（ADR-0015）的**云平面**要支持多租户企业 + 数据库式治理。RFC #20 留下开放决策 **D1**（租户隔离策略）、**D2**（权限强制点）、**D3**（租户粒度）。#25 spike 以**一手来源 + 真实云端 smoke** 对标 Zilliz / Qdrant / Pinecone / Weaviate（证据：`experiments/cloud-vector-db-spike/`）。

关键实测事实（决定三问走向）：

- Zilliz Cloud Serverless/Free：`tenant` 行级隔离是**纯客户端约定**——不带过滤即跨租户可见；`Partition` / `Partition Key` 是性能/作用域旋钮，**不是安全边界**。
- RBAC 要 **Dedicated**、审计要 **Enterprise+**；Serverless/Free 档二者皆无（实测 + 官方文档 + CLI help 三证）。
- 四家云**都无行级安全**；唯一能把权限下到 tenant 级的是 Weaviate Cloud（Free 即含多租户 + tenant 级 RBAC）。

## 决策

- **D1 隔离策略 = B：共享集合 + `tenant` 标量字段 + 过滤；预留分层 C。**
  - 安全前提（硬）：网关**无条件注入** `tenant` 过滤（白名单式构造 filter，不允许调用方自选 tenant），并配**隔离绕过安全测试套件**。
  - `Partition` / `Partition Key` 仅作规模化/局部性优化，**不作隔离机制**。
- **D2 权限强制点 = 网关唯一强制；store 侧 RBAC 只作加分项 / 纵深防御。**
  - ABAC（`classification` / `residency`）与 `tenant` 过滤都在网关落地。
  - 不依赖任何 store 侧行级安全（云档普遍没有）。
- **D3 租户粒度 = 分层：组织 = 隔离与计费边界；团队 / 个人 = 组织内的逻辑视图（payload 字段）。**
  - 隔离边界恒为「组织」；团队 / 个人不改变隔离边界，只做组内可见性与组织。
- **D4 默认云选型 = 维持 Zilliz Cloud**（托管免运维、免费额度、BYOE 兼容 BGE-M3 1024 维）；**保留 Weaviate Cloud 作「若 DB 侧强制升为硬需求」的升级路径，不切换。**

## 理由

- **D1=B** 与实测一致，且是唯一跨租户可比召回的形状；A（一租户一集合）在 Free 档 5 集合上限下不可行；C（大租户独享）留作升级路径。
- **D2** 由证据强制：store 侧无行级、云档无 RBAC/审计 → 唯一可靠强制点只能是网关（即 #20 D2 默认）。
- **D3** 分层把「隔离边界」与「组织/展示」解耦：粗粒度保管理性与配额可控，团队/个人仍在组织内细分，且与共享集合方案天然兼容。
- **D4** 维持免运维 + 免费额度 + BYOE；Weaviate 的 store 侧 tenant 强制是唯一替代优点，但被 Free 仅 1 collection / ≤3 tenants 抵消。

Considered options:

- **D1**：A 一租户一集合（弃，免费档不可行）｜**B 共享集合 + 过滤（采用）**｜C 分层 / 大租户独享（预留）
- **D2**：**网关唯一强制（采用）**｜store 侧强制（弃，需换 Weaviate 且牺牲配额）｜双强制（留作升 Dedicated 后的纵深防御）
- **D3**：组织即隔离边界 + 团队为组内视图（采用）｜团队为租户（弃，tenant 膨胀）｜
- **D4**：**Zilliz（采用）**｜Weaviate（备选）｜docker 自托管（CI/离线兜底，见 spike §3）

## Consequences

- **P2 云切片**：`VectorStore` 端口（#23）需承载**强制 tenant 过滤**的调用形状；网关做白名单 filter 构造；**隔离绕过测试套件**为验收硬项。
- **治理全部自建于网关**（权限、审计、配额）——store 只是存储。这抬高了网关的实现责任，是 #20 D2 的既定代价。
- 若未来升 Dedicated / 企业档，可叠加 DB 角色与审计作纵深防御，**不改本决策主结构**。
- **未决**：VikingDB / Milvus OSS 自托管 RBAC 一手来源未取到（spike §6），不影响主结论（主结论只依赖 Zilliz 云实测）。

Relates: #20（RFC，D1/D2/D3）、#25（spike 与证据 `experiments/cloud-vector-db-spike/`）、#23（端口需承载强制 tenant 过滤）、ADR-0015（双平面）。

## 修订（2026-09-15）：D2 的「网关」落在哪（#31）

D2 只说「网关唯一强制」，未定义网关是什么。补：

- **D2.1 authn 在 HTTP / MCP 边界**：`tenant` / `role` 由 **daemon 的 `/mcp` 端点**解析（API key / token → 身份）→ 绑定为**会话上下文**。单租户阶段身份 = 进程配置，但**形状按多租户设计**。
- **D2.2 authz 在工具层（daemon 内）**：每次工具调用据会话身份算 **effective filter**——白名单式构造，**只可收窄、不可放宽**（与 ADR-0019 D3 一致）。
- **D2.3 审计 / 配额挂在同一层**（自建；云免费档没有）。
- **D2.4 proxy 不是信任源**：stdio `proxy` 只做传输，**永不**作为身份 / 权限依据（多租户下客户端不可信）。

Considered options（本次）：

- **形态**：HTTP 边界 authn + 工具层 authz（采用）｜独立策略服务（当前阶段过重，留作企业档）｜proxy 携带身份 + daemon 信任（**否决**：多租户下等于无强制）｜store 适配器内（否决，store 侧不可靠）。

## 修订（2026-09-15）：D2 网关的落地（#32）

D2.1–D2.4 定了形状，本项记**实现**（`memory_agent/gateway/`）。落地后新增：

- **D2.5 身份形状 = `Identity{principal, tenant, role, allowed_classifications, allowed_residencies}`。**
  - 单租户阶段：身份 = 进程配置（`MEMORY_AUTH_PRINCIPAL/TENANT/ROLE/CLASSIFICATIONS/RESIDENCIES`）。
  - 多租户形状：`MEMORY_AUTH_TOKENS`（`<token> -> 身份` JSON）→ daemon 在 `/mcp` 边界校验
    `Authorization: Bearer <token>`；配置了 token 默认进入 `require_token`（无 / 无效即拒绝）。
  - **身份绝不来自可伪造 header**（`X-Tenant` 之类）；token 只做等值查找，绝不回显 / 落日志。
- **D2.6 authz = 白名单构造 effective filter，只可收窄、越权显式拒绝。**
  - `effective_filter = 调用方请求 ∩ entitlement`；受管控维度 = `tenant`（隔离边界）+ `classification` /
    `residency`（ABAC）。调用方请求越权值 → `AuthorizationError`（**不静默放宽**）；省略则无条件注入。
- **D2.7 ABAC 用允许集表达；多值维度下沉为端口 `MatchAny`。**
  - `classification` / `residency` entitlement 是允许集（如 `{private, internal}`）；全集的维度不产生
    子句。多值经 `VectorStore` 端口的 `payload_filter` 序列形态下沉为 Qdrant `MatchAny`
    （就地 amend ADR-0019 D3；关键词通道后置过滤同义）。
- **D2.8 审计 = middleware 记录 `tools/call` + 身份（JSONL，gitignored）。** 配额留 `set_quota_hook`。
- **工具层覆盖**：`memory_search`（注入 + 越权拒绝）、`memory_get`（可见性预检）、
  `memory_add/supersede/archive/reindex`（写角色门槛 + 目标条目可见性预检 + 去重检索收窄）。
- **落地位置**：`memory_agent/gateway/{identity,authz,middleware,context,audit}.py`；
  中间件注册在 `mcp_server.MCPServer(middleware=[...])`。证据 `memory_agent/eval/gateway_authz_32.py`
  + `..._results.md`（13/13），负向回归是核心。

Considered options（本次）：

- **ABAC 粒度**：允许集 + 多值 `MatchAny`（采用）｜单值 pin（够用但无法表达「up to internal」）｜
  entitlement 外置策略服务（留企业档）。
- **身份注入点**：`ServerMiddleware` 在 `/mcp` 边界解析 + `ContextVar` 绑定请求上下文（采用）｜
  工具签名携带身份（**否决**：把信任塞进不可信调用方）｜SDK OAuth `AuthSettings`（形状吻合但当前
  单租户阶段过重，留企业档接入）。
