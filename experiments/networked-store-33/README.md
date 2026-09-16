# 实验：#33 网络化 store 适配器（共享后端：自建 Qdrant 服务）

> 日期 2026-09-16 · 分支 `feat/33-networked-store` · 对象 = **记忆检索的存储平面**（不是法律 RAG）
> 脚本：`smoke.py`（真实运行 smoke）、`dual_backend_eval.py`（双后端对照）、`fusion_ablation.py`（原生融合消融）
> 证据：`smoke_result.json`、`eval_results.json`、`fusion_ablation.json`、`production_result.json`

## 1. 问题 → 假设

- **问题**：`VectorStore` 端口（#23）只有本地嵌入实现（`path=`）；共享平面无实现。
  Qdrant 客户端 `path=` / `url=` / `url=+api_key=` 是**同一套 API**，能否**一个适配器**
  同时覆盖"自建服务"与"云托管"，并让**同一确定性评测集**在本地与共享两个后端上**指标可比**？
- **假设**：能。检索归 store（服务端 `prefetch` + `FusionQuery`），适配器不叠加自己的融合
  （ADR-0019 D4/D5）；`tenant` / `payload_filter` 只透传（强制在网关，ADR-0018 D2）。

## 2. 设置

| 项 | 值 |
|---|---|
| 共享后端 | **自建 Qdrant server**（docker `qdrant/qdrant:latest` = **1.19.1**，`127.0.0.1:6333`，storage 在 D:） |
| 客户端 | 仓库 venv `qdrant-client 1.18.0` |
| 嵌入 | `BAAI/bge-m3`（dense 1024，BYOE）+ **app 层确定性词频稀疏**（`memory_agent/memory/sparse.py`，IDF 由 Qdrant `Modifier.IDF` 施加） |
| 语料 | 163 条（主树 `readonly_repos.json`：agent-knowledge-base / agent-infra / kg-triplet-sft + 全局 KB） |
| 评测集 | `memory_agent/eval/retrieval_eval_set.json`（#24，51 题 = 45 有答案 + 6 无答案） |
| 凭据 | 自建服务本次无鉴权；`MEMORY_STORE_URL` 走进程环境。`api_key` 只经构造参数，**绝不落盘**（证据里只记 `api_key_set`） |

**同一份点、跨部署**：先建本地 hybrid 集合（`path=`，BGE-M3 嵌入一次），再把**全部点
（命名 dense + sparse 向量 + payload）逐字复制**到 server 集合，两后端喂完全相同的数据。

## 3. 主证据：同一评测集，双后端指标可比

| 后端 | 平面 | 检索 | recall@1 | recall@5 | nDCG@10 | MRR | misses | 延迟/题 |
|---|---|---|---|---|---|---|---|---|
| `local-path` | 本地（嵌入） | 原生 hybrid RRF | **0.5222** | **0.8500** | **0.755689** | **0.730247** | **1** | 0.58 s |
| `shared-url` | 共享（server） | 原生 hybrid RRF | **0.5222** | **0.8500** | **0.755689** | **0.730247** | **1** | 0.27 s |
| 参考：生产 `gen-2` | 本地（指针） | dense + Python 关键词融合 | 0.7074 | 0.9185 | 0.8817 | 0.8731 | 0 | — |

- **`metrics_match: true`**（`eval_results.json`）：两后端 recall@1/@5、nDCG@10、MRR、misses **逐项相同**。
- **生产参考**是**另一语料版本**（gen-2 = 134 条，当前 hybrid 库 = 163 条）→ 只作参考，不与上行逐位比；
  其数字与 `#30`/ADR-0022 记录一致（0.7074 / 0.8817 / 0.8731），说明基准可复现。
- **`run_hash` 跨后端不相等**（`f0e33e…` vs `83d8b2d…`）：稀疏 IDF 在本地嵌入与 server 上的
  浮点精度有 ~1e-6 差异，会让**近并列**的次序不同，但**不动任何指标**。`run_hash` 是 #24 的
  **同后端**可复现性检查（见 §4 的确定性修复）。

## 4. 确定性：server 的融合并列顺序不可复现 → 适配器同分稳定排序

- 实测（固定 query 向量，不走模型）：Qdrant server 的 `FusionQuery(RRF)` 对**同分**并列项
  每次调用换序（dense / sparse **单路是确定的**）；三题并列 RRB=0.16666667 的次序在两次
  相同查询间不同 → 同一集合、同一 query 的 `run_hash` 不稳定。
- 修复：`VectorStoreService._stabilize` 在 hybrid 结果上按 `(score 降序, entry_id 升序)` 重排
  ——只影响**同分**并列、不改语义。修复后共享后端**两次相同查询 `run_hash` 相同**
  （实测 `588ee85177b0ac17` 两次；见 `probe` 记录）。
- 本地面（#24 基线）本就确定，此项只补共享平面。

## 5. 原生融合消融（同一集合、同一评测集）——`fusion_ablation.json`

| 融合 | recall@1 | recall@5 | nDCG@10 | MRR | misses |
|---|---|---|---|---|---|
| **dense 单路** | **0.6407** | **0.9019** | **0.8435** | **0.8075** | **0** |
| RRF（prefetch 双路） | 0.5222 | 0.8500 | 0.7557 | 0.7302 | 1 |
| DBSF（prefetch 双路） | 0.5444 | 0.7852 | 0.7444 | 0.7139 | 1 |

**结论**：在本语料上，**原生 RRF/DBSF hybrid 反而劣于纯 dense**（0.5222 vs 0.6407 recall@1）——
与 #30 的判断一致：CJK 二元组词法路是**低精度**表，等权 RRF 把噪声抬过头。
`store 原生 hybrid` 已按要求实现并可切换（`MEMORY_STORE_FUSION=rrf|dbsf|dense`，
默认 `rrf`）；**按平面选融合**（D6）是后续调优点（#21 backlog：融合机制 / sparse 编码质量）。
适配器**不做** Python 侧二次融合（`DefaultRetrievalStrategy` 的加法增强不在共享平面上叠加）。

## 6. 性能：长连接复用（网络化专属）

网络化 client 首次实现是"按操作开/关"（沿用 local mode 设计）→ **3.0 s/题**（纯 HTTP 被放大）。
改为**进程内长连接单例**后 **0.23–0.27 s/题**（≈11–19×）。本地嵌入仍按操作开/关（local mode 独占锁）。

## 7. 真实运行 smoke（自建服务）——`smoke_result.json`（12/12）

真实 BGE-M3 + 真实 server，8 条语料；断言 = 外部可观察行为：

`port_contract`（`isinstance(store, VectorStore)`）、`plane=shared`、`count_matches`、
`hybrid_returns`、`hybrid_top_is_probe`、`tenant_filter_excludes_other`、
`bound_tenant_not_widenable`、`dense_cosine_self=1.0`、
`shared_add_written` + `shared_add_is_db_mode`、`no_pointer_written`、
`shared_archive_confirm`。

## 8. 与 ADR 的关系

- **ADR-0019 D4/D5**：检索归 store、各平面用各自原生——本实验落地共享平面（`url=` + 原生 hybrid），
  策略层对 `native_hybrid` store 退化为薄封装（`MemoryRetriever._recall`）。
- **ADR-0019 D6**：分数量纲按平面——本地 dense 余弦 ∈ [-1,1]、共享 RRF ∈ (0,1]；**不跨后端共用阈值**
  （去重走 `store.search_dense` 的余弦通道，不套融合分）。
- **ADR-0025 D16**：共享域写入 = **DB upsert/delete**，**无 git / 代目录 / 指针**（`shared_writer.py`，
  单测断言不调子进程、不写 `CURRENT`）。

## 9. 未决 / 后续

1. **融合默认值**：当前语料 dense 最优；是否把共享平面默认改成 dense 或改**加权融合 / 更好的 sparse 编码**
   属 #21（融合机制）。
2. **跨后端逐位一致**：指标已一致；`run_hash` 因稀疏 IDF 浮点精度仍不同——若要逐位一致，需统一
   IDF 计算或对分数定量化（未做）。
3. **云托管**：本适配器同一 `url=` 覆盖，未做云 smoke（无云 Qdrant 凭据；Zilliz 是 Milvus、不适用）。
4. **多写者事务 / 共识、SaaS 运维**：不在本票（D16 明确不做）。

## 10. 复跑

```powershell
# 1) 自建 Qdrant
docker run -d --name qdrant-33 -p 127.0.0.1:6333:6333 -v <D:\path>:/qdrant/storage qdrant/qdrant:latest
# 2) 环境（真实语料 = 主树注册表；凭据只走 env）
$env:MEMORY_STORE_URL = "http://127.0.0.1:6333"
$env:MEMORY_READONLY_REPOS_CONFIG = "<repo>\memory_agent\readonly_repos.json"
$py = "<repo>\venv\Scripts\python.exe"
# 3) smoke（真实 BGE-M3）
& $py experiments/networked-store-33/smoke.py --entries 8
# 4) 双后端对照（首次带 --rebuild：嵌入语料一次；之后靠复制点，不再嵌入）
& $py experiments/networked-store-33/dual_backend_eval.py --rebuild --skip-production
# 5) 原生融合消融
& $py experiments/networked-store-33/fusion_ablation.py
```

> 生产参考基线（§3 第三行）用主树 gen-2 快照 + 预置指纹跑（避免触发全量刷新）：
> `MEMORY_INDEX_DIR=<snapshot> python -m memory_agent.eval.retrieval_eval --mode hybrid`。
