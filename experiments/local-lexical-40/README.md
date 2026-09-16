# #40 本地平面词法路：fastembed BM25 vs 自制词频哈希（落地 ADR-0019 D5/D7）

> 2026-09-16 · 分支 `feat/40-local-bm25` · worktree `wk-40`
> 对象 = **记忆检索本地平面**的**词法稀疏编码**（#24 评测集 51 题）；不调 LLM、不碰 legal、不碰共享/云。
> 脚本 `build_hybrid.py`（免重嵌迁移）+ `eval_hybrid.py`；证据 `<backend>_<fusion>.json`。

## 问题 → 假设

- **问题**：ADR-0019 D5 要求本地平面「各平面用各自 store 的原生检索」（dense + sparse +
  fusion），D7 把本地 BM25 路线定为 **fastembed**——但**定了没落地**。现状本地平面仍是
  `dense + Python 子串关键词加法增强`（app 层手写，`DefaultRetrievalStrategy`），
  而 #33 实测 store 原生 hybrid 配**自制词频哈希** sparse 反而差（同时自定义路**低精度**：
  CJK 二元组 + 无长度归一化）。
- **假设**：换 **fastembed BM25** 作词法路，能把 store 原生 hybrid 从「拖后腿」拉回可用；
  即差异的主因是**词法表精度**，不是「store 原生融合」本身。

## 设置

- 索引：gen-2 快照（**134 条，与 #24 baseline / #29 锚点同**）。用 `build_hybrid.py`
  把纯 dense 集合迁成 `dense + sparse(IDF)` hybrid——**复用原 dense 向量，免重嵌**
  （只新算 sparse，分钟级）。
- 词法：`tfidf`（`memory_agent/memory/sparse.py`，现状）｜ `bm25`（fastembed `Qdrant/bm25`）。
- 融合：`rrf` / `dbsf`（store 原生 `FusionQuery`）/ `dense`（单路，无稀疏）。
- 检索：`MemoryIndex(store=..., manifest_path=...)` 显式模式（跳过惰性刷新）；`native_hybrid`
  时 `MemoryRetriever` 退化为薄封装（ADR-0019 D4，无双重融合）。
- 环境：Windows / CPU；`HF_HUB_OFFLINE=1`。

## 数据（51 题 = 45 有答案 + 6 无答案；同 134 条、同 query）

| 变体 | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | miss | 延迟/题 |
|---|---|---|---|---|---|---|---|---|
| tfidf / rrf | 0.5000 | 0.7815 | 0.8556 | 0.9481 | 0.7626 | 0.7254 | 1 | 0.36s |
| tfidf / dbsf | 0.5444 | 0.6852 | 0.8037 | 0.9537 | 0.7518 | 0.7095 | 1 | 0.34s |
| dense 单路 | 0.6407 | 0.8685 | 0.9185 | 0.9741 | 0.8524 | 0.8136 | 0 | 0.32s |
| **bm25 / rrf** | 0.6407 | 0.8426 | 0.9111 | 0.9741 | 0.8385 | 0.7988 | 0 | 0.31s |
| **bm25 / dbsf** | **0.7111** | 0.8463 | **0.9333** | **0.9815** | 0.8765 | 0.8380 | 0 | 0.31s |
| bm25 / dense | 0.6407 | 0.8685 | 0.9185 | 0.9741 | 0.8524 | 0.8136 | 0 | 0.29s |
| *生产：dense + 手写关键词（参照）* | *0.7074* | *0.8685* | *0.9185* | *0.9741* | *0.8817* | *0.8731* | 0 | 0.27s |

- `dense 单路 = 0.6407` 与 #24 `vector` 基线**逐位一致** → 迁移（复用 dense）无失真。
- `tfidf/rrf = 0.5000`（#33 在 163 条上是 0.5222）→ 同方向复现：自制词频路拖后腿。

## 结论

1. **主因是词法表精度，不是 store 原生融合**：同一融合下换 BM25，recall@1
   `rrf 0.5000→0.6407`、`dbsf 0.5444→0.7111`（+14~17pp）；miss 从 1→0。
   → ADR-0019 D5/D7 的「本地走 store 原生 dense+sparse、BM25 用 fastembed」**可行**。
2. **BM25 + store 原生在质量上 ≈ 现状手写融合**：`bm25/dbsf` recall@1 0.7111 ≈ 生产 0.7074、
   recall@5 0.9333 > 0.9185（+1.5pp）；但 nDCG@10 0.8765 < 0.8817、MRR 0.8380 < 0.8731。
   **不是明确的净胜**——差异量级落在噪声带内（+0.4pp recall@1 ≈ 0.18 题）。
3. **`bm25/rrf`（store 默认融合）= dense 单路**：RRF 会把低精确词法表抬过头的老问题，
   换 BM25 后**降级为无损**（不再有害）。
4. **落地口径**：BM25 编码器 + doc/query 不对称接缝已实现，`MEMORY_SPARSE_BACKEND=bm25`
   可选（默认 `tfidf` 不变）；本地 store 新增 `fusion` 选择（ADR-0019 D6）。
   **是否把本地平面默认切到 `bm25/dbsf`（需重建 hybrid 索引）属默认链路变更，待 owner 定。**

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$snap = "C:\Users\Tan\AppData\Local\Temp\opencode\idx-35\gen-2"   # gen-2 快照
$base = "C:\Users\Tan\AppData\Local\Temp\opencode"
# 迁成 hybrid（免重嵌）
& $py experiments/local-lexical-40/build_hybrid.py --src "$snap\qdrant" --out "$base\hyb-bm25\qdrant" --manifest-src "$snap\manifest.json" --sparse-backend bm25
# 对照（同集合换 fusion）
& $py experiments/local-lexical-40/eval_hybrid.py --store-dir "$base\hyb-bm25\qdrant" --manifest "$base\hyb-bm25\manifest.json" --sparse-backend bm25 --fusion dbsf --out experiments/local-lexical-40/bm25_dbsf.json
```

## 局限

- 语料固定 gen-2（134 条）；增长后需重建重测。
- 6 个无答案 query 只作描述，不校阈值（ADR-0017）。
- 差异量级小（45 题），结论以**方向性**为主；「BM25 优于手写融合」**未达显著性**。
- BM25 依赖 `fastembed`（可选软依赖，含 onnxruntime 复用）；**生产默认仍是 `tfidf`+手写融合**。

Relates: #40、#33（自制 TF sparse 的负面证据）、#21、ADR-0019 D4/D5/D6/D7、ADR-0022、
`experiments/{networked-store-33,qdrant-local-mode-capabilities}/`、`memory_agent/memory/bm25.py`。
