# Reranker 延迟 POC（CPU）

**问题**：单次检索偶发 2-3 分钟，怀疑主因是 `bge-reranker-v2-m3` 在 CPU 上对候选池打分过慢（每条候选最长 800 字，池达 20+）。

**假设**：rerank 延迟与「池大小 × 文本长度」线性相关；通过缩小池 / 截断输入文本 / 调 batch 可降到可接受范围，且不显著影响排序质量。

**设置**：`bench_rerank.py` —— 用真实法条文本构造候选池，测量 `CrossEncoder.predict` 在池大小（8/10/20/40）× 文本长度（全量/截断 400/300）× batch（默认/1/16）下的延迟。模型：`BAAI/bge-reranker-v2-m3`（CPU，与生产一致）。每条配置重复 2 次取均值。

**数据**：`results.md`（本目录，脚本生成）。

**结论**：

1. **延迟线性于池大小**（≈230ms/对）：pool 40=9.2s / 20=4.6s / 10=2.4s / 8=2.0s。降池是有效杠杆。
2. **截断输入无效**：trunc 400/300/200 与全量几乎无差（4.4-4.5s vs 4.6s），不做截断。
3. **batch 保持默认 32**：bs=1 反而更慢（8.1s）。
4. **分钟级卡顿的真凶是冷启动 HF 网络检查**：首次运行 `CrossEncoder(...)` 默认去 HuggingFace 校验配置，网络不可达时逐文件重试（每文件 5 次指数退避）→ 本次 POC 因此卡死 15 分钟（WinError 10060）。生产 warmup 未完成 + HF 不可达 = 分钟级停滞，正好对应"有时候 2-3 分钟"。用 `local_files_only=True` + `HF_HUB_OFFLINE=1` 后模型 3.7s 加载完。

**建议修复**：
- A. 模型加载统一加 `local_files_only=True`（BGE-M3 与 reranker 均已缓存）——消除冷启动网络卡死。← 主修复
- B. 检索池 `ADAPTIVE_POOL 20→10`——单查询稳态 rerank 从 ~4.6s 降到 ~2.4s。

**补充结论（e2e-latency 后）**：生产实际池达 ~32，E2E 实测 rerank mean ≈15s（占 60-90%）。**pool 截断已决定延后**——保留为 planned optimization（简历谈资：已测出 rerank 是瓶颈、设计过 pre-rerank 截断 knob、权衡后留待后续）。冷启动 `local_files_only` 修复待实施。

**数据**：`results.md`。
