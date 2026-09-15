# 0019 记忆存储端口与条目驻留/密级（`VectorStore` + `classification`/`residency`）

Status: accepted

issue #23。抽 `MemoryIndex` 对存储的依赖为稳定端口，并给条目加**驻留 / 密级**维度，
为 ADR-0018 的云平面（#26 独立包化、P2 多租户）预备接缝。落地见
`memory_agent/memory/ports.py`（端口）、`memory_agent/memory/store.py`（本地适配器 + 工厂）、
`memory_agent/memory/{entries,index,reindex}.py`；单测 `tests/unit/test_memory_store_port.py`。

## 背景

`MemoryIndex` 原来直接 `import services.vector_store_service` 并把 `db_path`（Qdrant local
mode 假设）当构造参数——要接云向量库、按租户过滤、联邦召回，缺一个不绑定具体存储的接缝。
同时条目没有「驻留 / 密级」维度，无法做私有 / 企业分流（ADR-0018 的 ABAC）。

## 决策

- **D1 端口边界 = `memory_agent/memory/ports.py::VectorStore`（Protocol）。**
  - 方法：`add / search / delete / count / clear / warmup`；`search(query, k,
    payload_filter, tenant)` 返回与 ragcore 检索策略一致的 dict 形，`search_by_keywords`
    为**可选**关键词通道。
  - `MemoryIndex` / `Reindexer` 只经**工厂**（`store.open_store(db_path)`，可注入
    `store_factory`）拿存储，不再 import 任何 Qdrant 细节；测试注入最小端口实现即可
    端到端驱动索引。
  - 本地实现 `QdrantLocalStore` 是**薄适配器**（透传 `VectorStoreService`，不改检索语义）；
    云 store 适配同一端口（P2）。`search_documents` 是 `search` 的策略契约别名。
- **D2 驻留 / 密级 = 可选 frontmatter 字段，索引 payload 只做镜像（C′）。**
  - `classification ∈ {private, internal, public}`，缺省 `private`；
    `residency ∈ {local, cloud}`，缺省 `local`。
  - frontmatter 是真相源；两个字段**可选**，缺失 / 非法值一律回落默认（向后兼容全局 KB，
    `kb.py` 与写网关**不强制校验**）。
  - `Entry` 解析并镜像进 manifest / payload，供检索过滤使用。
- **D3 `search` 结果带 provenance，tenant 只收窄不可放宽。**
  - 每条命中带 `classification` / `residency`（payload 镜像）与
    `provenance = {plane, tenant}`；`plane` 由 store 声明（本地 = `local`）。
  - 网关构造 store 时绑定 `tenant`；`search(tenant=...)` 与 `payload_filter` 中的 tenant
    都不能**放宽**已绑定租户（白名单式合并，ADR-0018 D2）。租户过滤是强制点形状，
    真正强制在网关（P2 落地 + 隔离绕过测试套件）。

## 理由

- **D1** 把「存储行为」与「Qdrant local mode 细节」解耦：换云 store 时 `MemoryIndex`、
  检索链路、评测基座都不动。工厂接缝与既有 `Reindexer(store_factory=...)` 同形，改动小。
- **D2** 与 ADR-0018 D2 的 ABAC 对齐；可选 + 兜底默认保证任何现存条目零迁移即可继续被索引，
  这是「不破坏全局 KB」的硬要求。
- **D3** provenance 是联邦召回的必备标注（回答要能指明来源平面 / 租户）；把 tenant 绑在
  store 构造期而非调用期，避免调用方自选租户（ADR-0018 D1/D2 的强制点形状）。

Considered options:

- **端口返回类型**：dict 形（复用 ragcore 策略契约，采用）｜ typed `SearchHit`
  （更纯但会迫使改动 `DefaultRetrievalStrategy`，回归面大，弃）。
- **本地实现**：独立薄适配器（采用）｜ 直接给 `VectorStoreService` 加端口方法
  （会把 memory 语义塞进 ragcore 通用存储、污染法律链路，弃）。
- **字段校验**：写网关强校验（弃，破坏全局 KB 向后兼容）｜ 可选 + 默认兜底（采用）。

## Consequences

Relates: ADR-0018 D1/D2/D3（端口承载强制 tenant 过滤）、ADR-0008 D5（client 按操作开/关）、
ADR-0013 D3（进程内串行化）、ADR-0011（代 + 指针）、ADR-0006（Markdown 真相源）。
- **未做（P2）**：云 store 接入、网关强制过滤实现、隔离绕过测试套件——本票只给端口 + 本地
  实现 + 字段 + provenance。
- 索引 payload 新增两个字段 → **需要全量重建**才能让存量索引带上它们（`memory_reindex`）；
  旧索引检索仍工作（检索侧对缺失字段兜底默认）。

## 修订（2026-09-15）：检索归 store，策略层退化为薄封装（#31）

D1 只定了「存储 + 过滤」，未定**检索算法归谁**。补：

- **D4 检索由 store 提供**（含其**原生融合与打分**）：端口暴露 **hybrid 检索**（`prefetch` / fusion 参数）；**策略层不再自己融合**，退化为薄封装（注入强制过滤 → 调 store → 返回）。→ **杜绝「双重融合」**（store 已融合，我们再融一次会把关键词信号算两次、排序失真）。
- **D5 各平面用各自 store 的原生检索**（用户 2026-09-15 拍板）：本地 = Qdrant 原生（dense + sparse + RRF/DBSF）；云 = 云原生（BM25 / hybrid + Analyzer）。
- **D6 分数语义 / 阈值 / 评测按平面各自定义**：不同引擎分数量纲不同，**不跨后端共用阈值**；现有基线（nDCG@10=0.9658 / recall@1=0.8593）是**手写融合**时代数字 → 换原生后**必须重设**（→ `#30`）。
- **D7 本地平面形态 = 嵌入（`QdrantClient(path=…)`），不引入本地 server。**
  - 理由：本地平面的价值主张就是「**无服务、数据即目录**」，与 `#26`（干净环境 `pip install` 后 MCP 可启动）一致；本地 server 的功能超集（服务端 BM25、真并发、集群）在**单用户 / agent 记忆**场景**不是痛点**；引入常驻服务与「不做 reranker daemon」的原则（ADR-0013 修订）冲突。
  - **每平面一种形态、不共存**：本地 = 嵌入（BM25 用 `fastembed` 软依赖补齐）｜云 = 远程 `url=`（服务端原生 BM25，免 fastembed）。这是「各平面各用其原生」（D5），不是两套模式维护。
  - **触发条件（满足再议本地 server）**：需要**多机 / 多进程共享同一索引库**时。
  - 背景澄清：**"本地部署"有两种**——嵌入（引擎在客户端进程内）vs 本机 server（Docker/二进制，监听 `localhost`）。本仓一直是前者；此前**未记录过这条选择的理由**，本项补上。
- **实现前提（已实测；证据 `experiments/qdrant-local-mode-capabilities/`）**：
  - Qdrant **local mode** 支持 sparse + `prefetch` + `FusionQuery(RRF/DBSF)`；
  - **BM25 在 local mode 需要 `fastembed`**（客户端编码；「内建 BM25 免 fastembed」**仅 server ≥1.15.3**）——本仓取 **fastembed 路线**；
  - 本地 sparse + IDF 修复需 **`qdrant-client>=1.14.2`**（本机 1.18.0）；
  - `fastembed` 作**可选 extra**（`memory-agent[bm25]`），不压重默认包体（#26）。
- **schema 变更**：现有集合为**单一无名 dense** → 上 hybrid 需换结构并**重建**（memory 走代目录 + `CURRENT` 原子切换；`legal_web` 的 `documents` 需重灌）。
- **退役**：Python 侧子串关键词通道（D1 的 `search_by_keywords`）随策略融合一起退役（基线已证其「关键词优先」比纯向量差 **0.64→0.25**）。

Considered options（本次）：

- **检索归属**：各平面用各自 store 原生（采用）｜策略层保留手写融合兜底（弃：两套路径 + 双重融合风险）｜只在云用原生（弃：两平面行为不同，且本地手写融合已知有害）。
- **本地平面形态**：嵌入（采用）｜本地 server（弃：多一个常驻服务、与 #26 的轻安装冲突；**多机/多进程共享需求出现再议**）｜嵌入 + `url` 双模共存（弃：**过度设计**，两套行为面 = 找罪受）。

## 修订（2026-09-15）：D3 的 `payload_filter` 支持多值（#32）

D3 只说「`payload_filter` 是 {字段: 值} 精确匹配、只可收窄」。网关的多值 ABAC
（如 `classification ∈ {private, internal}`）需要一次表达多个允许值。就地 amend：

- **`payload_filter` 的值可以是序列（list/tuple/set）**：语义 = **任一匹配**，在本地实现里下沉为
  Qdrant `MatchAny`（`ragcore/services/vector_store_service.py::_field_condition`）；关键词通道的
  后置过滤（`ragcore/strategies/default.py::_matches_filter`）同义。标量值仍是精确匹配（`MatchValue`），
  **向后兼容**。
- 归属不变：网关按身份 entitlement **白名单构造**过滤；多值只是「范围内可选」的表达，仍是收窄。
- 条目 payload 新增可选 `tenant` 镜像（缺省 `None`），供绑定租户的 store 过滤与 `memory_get`
  可见性预检使用（frontmatter 是真相源，缺省即单租户未绑定）。
