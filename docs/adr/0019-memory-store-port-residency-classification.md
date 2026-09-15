# 0019 记忆存储端口与条目驻留/密级（`VectorStore` + `classification`/`residency`）

Status: proposed

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
