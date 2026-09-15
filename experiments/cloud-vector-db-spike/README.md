# 实验：云向量数据库多租户 + 权限能力对标（#25）

> 调研日期：2026-09-15 · 分支 `feat/25-cloud-spike` · 方法：**一手来源（官方文档）+ 真实云端 smoke**
> 产出对象：架构会话的 P1 三份 ADR（D1 隔离 / D2 权限强制点 / D3 租户粒度），见 RFC #20。
> 范围锁定（#25）：**默认官方云托管**（Zilliz Cloud 免费额度）；docker 自托管仅做一页对比。

## 1. 问题 → 假设

- **问题**：官方云托管向量库（免费/Serverless 档）能否提供「多租户 + 数据库式权限」？具体地：托管/免费版 RBAC 是否含**行级安全**；若无，隔离与权限由谁强制？
- **假设**：多数托管免费档**没有**行级安全，甚至没有可用 RBAC；因此**强制点唯一可靠的选择是网关**（= #20 D2 默认），store 侧 RBAC 只是加分项。
- **验收要求**（#25）：每个能力有**一手来源**；至少一次真实 smoke（建集合 → upsert → 按 `tenant` 过滤检索）。

## 2. 设置

| 项 | 值 |
|---|---|
| 云资源 | Zilliz Cloud `Agent-Knowledge-Base`，clusterId `in03-a78ddc7c1e91479`，region `ali-cn-hangzhou`，plan `Serverless`，deploymentOption `Free`，状态 `RUNNING` |
| Public Endpoint（非机密） | `https://in03-a78ddc7c1e91479.serverless.ali-cn-hangzhou.cloud.zilliz.com.cn` |
| 数据面凭据 | 只从进程环境 / gitignored `memory_agent/.env` 读（`MEMORY_CLOUD_URI/USER/PASSWORD`）；**未写入任何被跟踪文件、日志或本页** |
| 数据面 SDK | `pymilvus 3.0.1`（运维工具，**不进 requirements.txt**） |
| 控制面 CLI | `zilliz 1.0.2`（装在本项目 venv，不进 requirements.txt）；中国站须 `base_url=https://api.cloud.zilliz.com.cn` |
| smoke 脚本 | `experiments/cloud-vector-db-spike/smoke.py`（建集合 → insert/upsert → 过滤/分区/partition-key 检索 → 只读 RBAC 探测） |
| smoke 结果 | `experiments/cloud-vector-db-spike/smoke_result.json` |

运行方式（凭据仅走环境）：
```powershell
$env:MEMORY_ENV_FILE = "<repo>\memory_agent\.env"
<repo>\venv\Scripts\python.exe smoke.py --suite all      # 跑完自动清理
```

## 3. 对标表（一手来源，文末附 URL）

| 维度 | Zilliz Cloud（默认） | Qdrant Cloud | Pinecone | Weaviate Cloud | VikingDB |
|---|---|---|---|---|---|
| 租户隔离机制 | 共享集合 + `tenant` 标量过滤；`Partition`；`Partition Key`（16 分片）（实测） | 共享集合 + payload `is_tenant` 索引；用户自定义 shard；分层多租户 | 每 index 内 namespace（所有读写**总是**定位到单个 namespace） | 多租户集合：每租户独占 shard + 独立向量索引 | 未核实 |
| 隔离是否**服务端强制** | **否**（漏过滤器即跨租户；见 §4）。分区/分片是性能与作用域旋钮 | **否**（客户端必须带 filter；漏则全量）。shard 路由同理 | **是**（namespace 是所有读写操作的强制作用域；不传落默认 namespace） | **是**（多租户按 tenant 定位 shard；RBAC 下操作需 tenant 级权限） | 未核实 |
| DB 级 RBAC（集合/库/集群） | 有，但 **Dedicated 独占**（Serverless/Free 无 cluster user/role） | Cloud RBAC 仅**账号级**（计费/IAM/集群）；「per-cluster 权限」官方标为未来版本 | org/project 级角色；API key 角色（ReadWrite/ReadOnly/None）；**namespace 无关** | collection/tenant/data-object 级 RBAC，含 tenant 名过滤器 | 未核实 |
| 行级安全（per-tenant predicate） | **无**（权限只到 collection/partition DDL，无行级谓词） | **无**（粒度到 collection 读/写） | **无**（粒度到 project） | **无**（但可下到 tenant——最接近） | 未核实 |
| API key 可限定范围 | 数据面用 DB 用户名/密码或 API key；**Free/Serverless 无 cluster role 可绑** | Granular Access API Key（JWT）可**按 collection 限读/写**，Cloud 默认启用 | API key 绑 project 角色；不能按 namespace 限 | OIDC/API key 绑 role，可限 collection/tenant | 未核实 |
| 客户端能否绕过 | **能**——过滤条件是普通参数，凭据对全库有效 | 能绕过租户过滤；collection 级权限由 token 强制（不可绕过该层） | namespace 层不可绕过；跨 project 由 key 强制 | tenant/collection 级不可绕过 | 未核实 |
| 审计 | **Enterprise+ / BYOC 才有**（且计费；需 Dedicated + Milvus 2.5.x） | **付费集群**才有（v1.17+，写本地盘，可查 `/audit/logs`） | **Enterprise 才有** | 有（细节未核实） | 未核实 |
| 免费额度上限 | Free：**2.5M vCU/月、5 GB、5 集合**、nq≤10、topK≤1024、insert 2 MB/s；闲置 7 天自动挂起；每 org 1 个 Free | Free：单节点 **0.5 vCPU / 1 GB RAM / 4 GB 盘**；无 HA | Starter：**2 GB、2M 写单元/月、1M 读单元/月、5 index、100 namespace/index、≤2 用户、1 project**；**RBAC/SSO/审计均不含** | Free：**100k objects、1 GB 内存、10 GB 盘、1 collection、≤3 tenants**；**含 RBAC 与多租户** | 未核实 |
| Python SDK / CI 可跑 | `pymilvus`，实测可用；云端集成测试须 opt-in | `qdrant-client` | `pinecone` | `weaviate-client` | 未核实 |
| 迁移成本（BGE-M3 1024 维） | 维度上限 32768；**BYOE 自带向量**可行；无 `bge-reranker-v2-m3`（仅 RRF/Weighted + SiliconFlow）→ 神经重排留网关 | 1024 维原生支持；BYOE | 1024 维支持；**托管提供 `bge-reranker-v2-m3`** | 1024 维支持；BYOE | 未核实 |

**docker 自托管备选（一页，不深挖）**：Qdrant OSS 默认**不设防**，须自行开 API key（admin / read-only / 按 collection 的 granular JWT RBAC）+ TLS + 网络绑定，审计 v1.17+ 起支持（来源：Qdrant Security & Access Control）。Milvus 自托管 RBAC 的**一手文档本轮未取到**（`milvus.io` 在本环境超时）→ 见 §6 未决项。结论：自托管能拿到**与云同级的集合级 RBAC**（甚至更自由），代价是运维、加固、内存与无托管审计/配额，适合作 CI/离线兜底（与 #25「备选」一致）。

## 4. 真实 smoke 证据（Zilliz Cloud Serverless/Free）

数据：3 个租户 × 4 条 = 12 条，1024 维确定性向量；集合内带 `tenant` 标量字段。

| 断言 | 实测结果 | 结论 |
|---|---|---|
| 建集合 + insert + upsert + count | `create_ms≈3300`；`num_entities=12`（覆盖 id=0 后仍 12） | 读写通 |
| 带过滤 `tenant == "tenant-a"` | 返回 4 条，全部 `tenant-a`（`ms≈450`；首次冷启动约 `1200`） | 过滤正确 |
| **不带过滤** `id >= 0` | 返回 10 条（limit），**横跨 tenant-a/b/c** | **默认跨租户可见 → 过滤非强制** |
| 显式 `tenant in ["tenant-b","tenant-c"]` | 只回 b/c | 调用方可任意选租户 |
| 物理 `Partition` + `partition_names=["tenant-a"]` | 只回 `tenant-a`；各分区 `row_count=4` | 分区作用域有效 |
| 物理 `Partition` 不带 `partition_names` | 回 a/b/c 三租户 | **分区不隔离**（不传就全扫） |
| `Partition Key`（`num_partitions=16`）带 key 过滤 | 只回 `tenant-a` | 分片键可用 |
| `Partition Key` 不带 key 过滤 | 回 a/b/c 三租户 | **分片键不强制** |
| 数据面只读探测 `list_users` / `list_roles` | `PERMISSION_DENIED: PrivilegeSelectOwnership`（DB 用户名已隐去） | 当前 DB 用户非 owner，**数据面拿不到 RBAC 管理** |
| 控制面 `user/role` 子命令 help | 明确标注 **“Dedicated only”**；`context set` 仍复现 `apikey illegal`（中国站解析坑） | Serverless 无 cluster RBAC；与文档一致 |

**smoke 结论**：Zilliz Cloud Serverless/Free 上，`tenant` 行级隔离是**纯客户端约定**，漏过滤器即泄漏；`Partition`/`Partition Key` 是性能/作用域旋钮而非安全边界。RBAC 与审计在 Serverless/Free 档**不可用**（RBAC 要 Dedicated，审计要 Enterprise+）。

## 5. 推荐（→ P1 ADR 输入）

- **D1 隔离策略：B（共享集合 + `tenant` 字段 + 过滤），预留 C（分层）。**
  理由：(a) 实测过滤语义正确且是唯一跨租户可比召回的形状；(b) Free 档**仅 5 个集合**、Serverless 100 个，A（一租户一集合）在免费/中小规模直接不可行；(c) `Partition`/`Partition Key` 不提供安全隔离，只作后续规模化/局部性优化；(d) C 的「大租户独享」在 Weaviate/Qdrant 有原生对应，可作未来升级路径。
  **安全前提**：网关必须**无条件注入** `tenant` 过滤（白名单式构造 filter），并配**隔离绕过安全测试套件**（#20 P2 已列）。

- **D2 权限强制点：网关唯一强制；store 侧 RBAC 为加分项。**
  理由：Serverless/Free 档 RBAC 整体缺失（实测 + 文档 + CLI help 三证）；Qdrant/Pinecone 的 RBAC 也只到 account/project/collection，均**无行级**。ABAC（`classification`/`residency`）与 tenant 过滤必须在网关落地。若日后升 Dedicated，可再叠加 DB 角色做纵深防御，但不作为前提。

- **D3 租户粒度：仍开放**（组织 vs 团队；本地个人库是否算云租户）。本 spike 提供两条输入：① tenant 过滤是应用层字段，粒度可自由定义，不锁死；② **Free 档集合数上限（5）使「一租户一集合」不可行**，粒度设计必须与共享集合方案兼容。

- **默认选型维持 Zilliz Cloud**：托管免运维、免费额度够 MVP/演示、BYOE 兼容 BGE-M3 1024 维；但**必须接受**「无 RBAC/无审计/无行级安全」，把治理全部放网关——这与 #20 D2 默认一致。
- **唯一值得对比的备选**：Weaviate Cloud（Free 档即含多租户 + tenant 级 RBAC，是四者中唯一能在 store 侧强制到 tenant 的），代价是 Free 档仅 1 collection / ≤3 tenants。若未来「DB 侧强制」升为硬需求，再单独评估。

## 6. 未决项

1. **VikingDB 一手来源未取到**：`docs.volcengine.com` 在本环境需 JS / DNS 解析失败，`markitdown` 亦不可用。已定位到候选文档（`/docs/84313/2488162` 权限资源、`/docs/84313/1827478` 知识库权限资源）但**未渲染成功** → 需在浏览器/控制台复核后再填表，**当前不留记忆性断言**。
2. **Milvus OSS 自托管 RBAC 一手文档未取到**（`milvus.io` 本环境超时）→ docker 一页中该格留空，不影响主结论（主结论只依赖 Zilliz 云实测）。
3. **延迟**：仅一次冷启动数据（search ≈1.2s），样本不足，不作性能结论；真实 p50/p99 属 #21。
4. **当前 DB 用户的角色面**：实测为非 owner（RBAC 操作被拒）。P2 接入需要 **cluster `db_admin` 或具 cluster 访问权的 API key** 才能建用户/角色（若届时升 Dedicated）。
5. **namespace/分区规模的行为差异**（如 Pinecone namespace 数上限、Weaviate 租户 offload）未实测，仅文档。

## 7. 来源清单（均为官方文档，2026-09-15 访问）

**Zilliz Cloud**（`docs.zilliz.com`）
- 访问控制 / RBAC 三层（组织 / 项目 / 集群）：`/docs/access-control-overview`
- 集群用户「**This feature is available only to Dedicated clusters**」：`/docs/cluster-users`
- 权限与权限组（privilege 只到 database/collection/partition DDL，无行级）：`/docs/cluster-privileges`
- 审计日志「**Enterprise plan or higher, and BYOC**」+ 计费：`/docs/audit-logs`
- 上限（Free 2.5M vCU/月、5 GB、5 collections、nq≤10、topK≤1024、insert 2 MB/s；Serverless 100 collections）：`/docs/limits`
- Free/Serverless 生命周期（闲置 7 天挂起、每 org 1 个 Free）：`/docs/free-and-serverless-clusters`
- 计划对比 / RBAC 行：`/docs/select-zilliz-cloud-service-plans`
- 功能可用阶段：`/docs/feature-availability`
- 控制面实测：`zilliz 1.0.2`（`cluster list` 成功；`user/role --help` 标 Dedicated only；`context set` 报 `apikey illegal`）

**Qdrant**（`qdrant.tech/documentation`）
- Cloud RBAC（**账号级**；「Current permissions control access to ALL clusters. Per Cluster permissions will be in a future release.」）：`/cloud-rbac/`
- 集群配置（1000 collections 上限、strict mode 默认、**Audit logs 付费集群 v1.17+**）：`/cloud/configure-cluster/`
- 多租户（payload `is_tenant` / 用户自定义 shard / 分层；无服务端强制）：`/manage-data/multitenancy/`
- 安全（granular access API key = **按 collection 读/写** JWT RBAC；Cloud 默认启用）：`/security/`
- 定价（Free：单节点 0.5 vCPU / 1 GB / 4 GB）：`qdrant.tech/pricing/`

**Pinecone**（`docs.pinecone.io` / `pinecone.io`）
- namespace 语义（读写总是指定单一 namespace）：`/guides/index-data/indexing-overview`
- RBAC（principal × resource，org/project 级）：`/guides/production/manage-rbac`
- 安全总览（**Audit logs 仅 Enterprise**；SSO Standard+）：`/guides/production/security-overview`
- 定价（Starter/Builder/Standard/Enterprise 各维度；**RBAC 仅 Standard+，Audit 仅 Enterprise**）：`pinecone.io/pricing/`

**Weaviate**（`weaviate.io`）
- 多租户（每租户专属 shard + 独立索引）：`/weaviate/concepts/data`
- RBAC 总览（collection / tenant / data-object 权限，tenant 名过滤器）：`/weaviate/configuration/rbac`
- RBAC 角色管理（`Permissions.tenants(...)` / `Permissions.data(tenant=...)`）：`/weaviate/configuration/rbac/manage-roles`
- 定价（**Free 含 RBAC 与多租户**；Free 1 collection / ≤3 tenants / 100k objects）：`weaviate.io/pricing`

**本仓 smoke 证据**
- `experiments/cloud-vector-db-spike/smoke.py`、`experiments/cloud-vector-db-spike/smoke_result.json`

## 8. 文件清单

- `smoke.py` —— 可复跑的云端 smoke（凭据只从环境；无密钥落盘）
- `smoke_result.json` —— 本轮真实结果（§4 表格的原始 JSON）
- `README.md` —— 本页（对标表 + 推荐 + 未决项）
