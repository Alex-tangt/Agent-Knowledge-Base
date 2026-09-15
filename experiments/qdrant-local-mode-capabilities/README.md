# Qdrant local mode 能力核实（#31 输入）

> 日期 2026-09-15 · 方法：**一手代码/文档 + 本地实测**（`QdrantClient(":memory:")` 与 `path=<temp>`，**未触碰仓库 vector_db**）。
> 环境：仓库 venv `qdrant-client 1.18.0`；`fastembed` **未安装**。
> 目的：判定"**各平面用各自 store 的原生检索**"在**本地嵌入模式**下是否可行。

## 结论

| 能力 | local mode 结论 | 证据 |
|---|---|---|
| 稀疏向量（named + `SparseVector` + `modifier=Idf`） | ✅ **支持** | `:memory:` 与 `path=` 均通过 |
| `prefetch` + `FusionQuery(RRF / DBSF)` 原生 hybrid | ✅ **支持** | RRF/DBSF 均返回融合排序（实测） |
| BM25（`Document(model="Qdrant/bm25")` + `Idf`） | ⚠️ **local mode 需要 `fastembed`**（客户端编码）；**IDF 由 Qdrant 施加**（本地有效） | 无 fastembed → `ImportError`；装 fastembed 后全部通过 |

**关键区分**：Qdrant **原生支持 BM25**——但"服务端内建、免 fastembed"那条**只对 server（≥1.15.3）生效**。代码证据：`qdrant_client/embed/model_embedder.py` 里 `self._is_builtin_embedder_available = not is_local_mode`。**本地嵌入模式没有服务端算词权重，只能客户端用 fastembed。**

**零依赖替代**：自己算 BM25/sparse 词权重 → upsert `SparseVector` + `SparseVectorParams(modifier=Idf)` → 本地可用（实测，含 IDF）。**本仓采用 fastembed 路线**（用户 2026-09-15 定）。

## 版本门槛（重要）

| 特性 | qdrant-client ≥ |
|---|---|
| 稀疏向量 | 1.7.0 |
| Query API + `prefetch` + `Fusion.RRF` + `Idf` modifier | 1.10.0 |
| `Fusion.DBSF` | 1.11.0 |
| **local mode sparse+IDF 修复** | **1.14.2** |
| 内建 BM25（免 fastembed，**仅 server**） | 1.16.0（+ Qdrant server ≥1.15.3） |

**本机 = 1.18.0** → 本地 sparse/hybrid 全部可用。
**依赖债**：`legal_web/requirements.txt` 是**裸名** `qdrant-client`（无下限）；根 `requirements.txt` **未列**（列的是 chromadb）。→ 要靠本地 native hybrid，应声明 **`qdrant-client>=1.14.2`**（声明性卫生，不是当前故障）。

## fastembed 的代价（对 #26 可安装包）

- 加 `fastembed` 会带 **onnxruntime** 运行时（重依赖）；首次使用下载 `Qdrant/bm25` 模型（HF，需网络一次）。
- BM25 encoder **不是神经大模型**（不像 reranker 吃 GB），对 daemon 内存影响小。
- 建议：**做成 optional extra**（`memory-agent[bm25]`），不压重默认包体。

## 对设计的影响（→ ADR-0019 修订）

- **检索归 store**：端口暴露 hybrid（`prefetch`/fusion 参数）；策略层退化为**薄封装**（只注入强制过滤）。
- **schema 变更**：现有集合是**单一无名 dense 向量** → 上 sparse/hybrid 要换结构 → **重建**（memory 有代目录 + `CURRENT` 原子切换；`legal_web` 的 `documents` 需重灌）。
- **避免双重融合**：store 已融合 → 我们的策略**不再叠**关键词通道（现有 Python 子串通道退役；基线已证其"关键词优先"比纯向量差 0.64→0.25）。
- **重设基线**：nDCG@10=0.9658 / recall@1=0.8593 是**手写融合**时代的数字，换原生后要重测（→ #30）。

## 未核实

- local mode 与 server 的 **BM25 打分是否数值一致**（只验方向性）。
- 真实语料上的 sparse/hybrid **性能**（只做了 3 点 smoke）。
- local mode 的**完整不支持特性清单**（client README 无 feature matrix）。

## 原始证据

临时目录 `C:\Users\Tan\AppData\Local\Temp\opencode\qdrant-local-mode\`：`exp_local_mode.py`、`exp_bm25.py`、`exp_*.utf8.txt`。
