# #35 reranker 换新模型：jina-reranker-v2 int8 ONNX（memory 作用域）

> 2026-09-16 · 分支 `feat/35-jina-reranker` · worktree `wk-40`
> 对象 = **记忆重排**（#24 评测集，51 题）；不调 LLM、不碰 legal。
> 脚本 `eval_rerank_backend.py`；证据 `retriever_only.json` / `m3_torch.json` / `jina_onnx.json`。

## 问题 → 假设

- **问题**：ADR-0022 D8 选定 `jina-reranker-v2-base-multilingual` int8 ONNX 作 memory 重排器
  （质量持平 + ~4.8x 提速），但当时只在 **12 题子集**上测过，且记录「必须经 `optimum`，会把
  `transformers` 5.x 降到 4.57.x（整仓）」。能否**零新依赖**落地，且在全量 51 题上复核质量？
- **假设**：jina 的 ONNX 图可用已在装的 `onnxruntime` + `tokenizers` 直跑（`input_ids` +
  `attention_mask` → `logits`），无需 `optimum`；全量复核可能暴露子集看不到的差异。

## 设置

- 索引：**显式冻结** gen-2（134 条）快照（`MemoryIndex(store=..., manifest_path=...)`，
  `_explicit=True` 跳过 `_maybe_refresh`），三档跑**同一批候选**。
- 召回：`DefaultRetrievalStrategy(enable_keyword=True)` + pool 14（= 生产默认融合）。
- 重排：`MemoryRetriever`，送排正文截断 `MEMORY_RERANK_MAX_CHARS=512`。
- 环境：Windows / CPU；`HF_HUB_OFFLINE=1`（**必须显式设**，否则 sbert/HF 的
  `auto_conversion` 后台线程会在弱网下抛错中断加载）。

## 数据（51 题 = 45 有答案 + 6 无答案；同索引同候选）

| 重排器 | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR | miss | 延迟/题 |
|---|---|---|---|---|---|---|---|---|
| **m3（torch，对照）** | **0.8815** | 0.9444 | **0.9685** | 0.9796 | **0.9709** | **0.9889** | 0 | 11.68 s |
| **jina-v2 int8 ONNX** | 0.8315 | 0.9333 | **0.9685** | 0.9796 | 0.9443 | 0.9426 | 0 | **2.67 s（4.4x）** |
| （无重排，参照） | 0.7074 | 0.8685 | 0.9185 | 0.9741 | 0.8817 | 0.8731 | 0 | 0.27 s |

- **m3 一栏与 #29 锚点逐位一致**（0.881481 / 0.970870 / 0.988889，`run_hash` 口径同）→
  harness 可信；**jina 一栏与 #29 的 sbert-ONNX 路径在前 12 题排名 0 处不一致** → 手写
  ONNX 实现忠实，差异来自模型本身而非实现。
- **消费者口径（`memory_search` 默认 k=5）两者相同**：recall@5 = **0.9685**、recall@10 = 0.9796
  ——jina 的 −5.0pp recall@1 只影响 rank-1/3 的排序，不影响 agent 实际读到的 5 条。
- 无答案 query 的 top-1 分：m3 mean 0.0046（logit 近似 0），jina mean 0.530（sigmoid 空间，
  不可与 m3 logit 直接比；只作描述，不在本实验校阈值）。

## 结论

1. **可零新依赖落地**：`onnxruntime` + `tokenizers` 直跑 jina int8 ONNX，**绕开 `optimum`
   的整仓降级**（复验：`pip install optimum[onnxruntime]` 会把 `transformers` 5.5.4 → 4.57.6、
   `huggingface_hub` 1.31 → 0.36）。落地于 `memory_agent/memory/onnx_reranker.py`，
   经 `MEMORY_RERANK_BACKEND=onnx` 选择（默认仍 `torch` = m3）。
2. **全量口径下 jina 不是"质量持平"**：#29 的 12 题子集（q001–q012，偏易）掩盖了差异；
   全量 51 题 jina recall@1 **−5.0pp**、nDCG@10 −2.66pp、MRR −4.6pp。
3. **但对消费者（k=5）无差异**：recall@5/@10 两者一致；jina 以 4.4x 延迟换同样的可见收益。
   → 是否把 memory 重排器换成 jina，是**许可（CC-BY-NC）+ 排序口径**的取舍，需 owner 定
   （ADR-0022 D7 的"默认关"不变；本实验只换"若开时用哪个模型"）。
4. **权重解析走 HF 缓存**（`try_to_load_from_cache`，离线可用）；缺文件默认不联网
   （`MEMORY_RERANK_ALLOW_DOWNLOAD=1` 才下载）。

## 复现

```powershell
chcp 65001 > $null; $env:PYTHONIOENCODING="utf-8"
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"   # 弱网必需
$py = "D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe"
$snap = "C:\Users\Tan\AppData\Local\Temp\opencode\idx-35\gen-2"
# 无重排 / m3 / jina（三档同索引）
& $py experiments/rerank-jina-35/eval_rerank_backend.py --qdrant-dir "$snap\qdrant" --manifest "$snap\manifest.json" --no-rerank
$env:MEMORY_RERANK_BACKEND="torch"
& $py experiments/rerank-jina-35/eval_rerank_backend.py --qdrant-dir "$snap\qdrant" --manifest "$snap\manifest.json"
$env:MEMORY_RERANK_BACKEND="onnx"; $env:MEMORY_RERANK_MODEL="jinaai/jina-reranker-v2-base-multilingual"
& $py experiments/rerank-jina-35/eval_rerank_backend.py --qdrant-dir "$snap\qdrant" --manifest "$snap\manifest.json"
```

## 局限

- 单机 CPU、单次会话；延迟含首次加载摊销（jina 加载 ~2s；51 题均摊后仍 <3s/题）。
- 无答案 query 的 jina 分数在 sigmoid 空间，与 m3 logit 不同量纲，仅描述。
- 语料固定 gen-2（134 条）；语料增长后需重建重测。

Relates: #35、#29、#21、ADR-0022 D7–D12、ADR-0021（验收指标）、`experiments/rerank-model-survey/`。
