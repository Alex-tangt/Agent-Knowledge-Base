# MultiHop-RAG one-shot census（#47 Phase A 层 1）

- 数据：`yixuantt/MultiHopRAG`（ODC-BY），语料 609 篇 / 查询 2556 条（可答 2255 + null 301）。
- 检索链路：`MemoryIndex.search` → `MemoryRetriever` → `DefaultRetrievalStrategy`（向量 + 关键词）；pool=50（生产默认 14，另列 recall@14）；rerank 关。
- 单位：条目级（金标 = evidence `url` 对齐的语料文章）。
- k = (1, 5, 10, 20, 50)。

## recall@k（全部可答题）

| run | recall@1 | recall@5 | recall@10 | recall@20 | recall@50 | recall@14 | nDCG@10 | MRR | 完整覆盖@50 |
|---|---|---|---|---|---|---|---|---|---|
| base:hybrid | 0.2526 | 0.6504 | 0.7833 | 0.8998 | 0.9645 | 0.8470 | 0.6657 | 0.7430 | 0.9175 |
| base:vector | 0.2459 | 0.6324 | 0.7720 | 0.8922 | 0.9645 | 0.8364 | 0.6508 | 0.7259 | 0.9175 |

## 分题型 recall@k

### base:hybrid

| 题型 | n | recall@1 | recall@5 | recall@10 | recall@20 | recall@50 | 完整覆盖@50 |
|---|---|---|---|---|---|---|---|
| inference_query | 816 | 0.2033 | 0.5452 | 0.7110 | 0.8501 | 0.9675 | 0.8995 |
| comparison_query | 856 | 0.2973 | 0.6980 | 0.8148 | 0.9192 | 0.9546 | 0.9147 |
| temporal_query | 583 | 0.2559 | 0.7276 | 0.8382 | 0.9408 | 0.9748 | 0.9468 |

### base:vector

| 题型 | n | recall@1 | recall@5 | recall@10 | recall@20 | recall@50 | 完整覆盖@50 |
|---|---|---|---|---|---|---|---|
| inference_query | 816 | 0.1958 | 0.5352 | 0.7014 | 0.8452 | 0.9675 | 0.8995 |
| comparison_query | 856 | 0.2891 | 0.6774 | 0.8045 | 0.9104 | 0.9546 | 0.9147 |
| temporal_query | 583 | 0.2524 | 0.7024 | 0.8230 | 0.9314 | 0.9748 | 0.9468 |

## 分金标篇数（unique evidence）recall@k

### base:hybrid

| 篇数 | n | recall@1 | recall@5 | recall@10 | recall@20 | recall@50 |
|---|---|---|---|---|---|---|
| 2 | 1169 | 0.3272 | 0.7468 | 0.8465 | 0.9204 | 0.9594 |
| 3 | 774 | 0.1865 | 0.5883 | 0.7420 | 0.8979 | 0.9729 |
| 4 | 312 | 0.1370 | 0.4431 | 0.6490 | 0.8269 | 0.9631 |

### base:vector

| 篇数 | n | recall@1 | recall@5 | recall@10 | recall@20 | recall@50 |
|---|---|---|---|---|---|---|
| 2 | 1169 | 0.3186 | 0.7246 | 0.8315 | 0.9123 | 0.9594 |
| 3 | 774 | 0.1817 | 0.5728 | 0.7382 | 0.8928 | 0.9729 |
| 4 | 312 | 0.1322 | 0.4351 | 0.6330 | 0.8157 | 0.9631 |

## 覆盖 vs 排序（pool=50）


| run | gold 全在@50 | 有缺失@50 | 排序亏@5 | 覆盖缺槽@50 | 排序亏槽@5 | recall@5 | rank 天花板(=recall@50) | 排序余量 |
|---|---|---|---|---|---|---|---|---|
| base:hybrid | 2069/2255 | 186 | 1317 | 204 | 2039 | 0.6504 | 0.9645 | 0.3142 |
| base:vector | 2069/2255 | 186 | 1352 | 204 | 2137 | 0.6324 | 0.9645 | 0.3321 |

## 送嵌窗口 × gold 槽 recall（截断表征诊断）

`all` = 该题该文章的 gold fact 全在 6000 字送嵌窗口内；`partial`/`none` = 部分/全部落在窗口外（`base` 变体看不到）。

| run | status | gold 槽 | recall@5 | recall@50 |
|---|---|---|---|---|
| base:hybrid | all | 3976 | 0.7080 | 0.9791 |
| base:hybrid | partial | 29 | 0.8621 | 1.0000 |
| base:hybrid | none | 1903 | 0.4335 | 0.9364 |
| base:vector | all | 3976 | 0.6904 | 0.9791 |
| base:vector | partial | 29 | 0.8621 | 1.0000 |
| base:vector | none | 1903 | 0.4188 | 0.9364 |

## null_query（描述性，不校阈值；ADR-0017）

| run | n | top-1 分数 mean | top-1 max |
|---|---|---|---|
| base:hybrid | 301 | 0.49163 | 0.647521 |
| base:vector | 301 | 0.471388 | 0.618355 |

## 确定性

| run | run_hash | elapsed_s | 延迟 mean/p50/p95/max (s) |
|---|---|---|---|
| base:hybrid | 48503510bedd9655 | 17.49 | 1.0767/1.075/1.2468/1.7316 |
| base:vector | fd57550e387d13f8 | 0.33 | 0.8157/0.8049/0.9048/1.2767 |

## 隔离

```json
{
  "before": {
    "agent_kb_dir": "C:\\Users\\Tan\\.config\\opencode\\knowledge",
    "prod_memory_index_dir": "D:/python_work/work2026-4/Agent-Knowledge-Base\\memory_agent\\vector_db",
    "kb_status_sha": "6381b2d41674fb59",
    "prod_index_current": "gen-2",
    "prod_index_dir_sig": "fe1f57d378a4c3c8",
    "daemon_health": 200
  },
  "after": {
    "agent_kb_dir": "C:\\Users\\Tan\\.config\\opencode\\knowledge",
    "prod_memory_index_dir": "D:/python_work/work2026-4/Agent-Knowledge-Base\\memory_agent\\vector_db",
    "kb_status_sha": "6381b2d41674fb59",
    "prod_index_current": "gen-2",
    "prod_index_dir_sig": "fe1f57d378a4c3c8",
    "daemon_health": 200
  },
  "unchanged": true
}
```
