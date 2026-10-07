# 标准 RAG 集 census（#71 阶段 4）

> 实测时间：2026-10-07；来源：HF `api/datasets/<id>`（许可 / 标签）+ `datasets-server/info`（规模）+ `/parquet`（SciFact 三件套字段核对）。
> 判据：**语言 / 规模（≤~2k 条目锚点）/ 许可 / 是否必须重嵌 / 任务形态**。

| 候选集 | 语言 | corpus 条目 | query（HF queries split 总数） | test qrels 形态 | 许可（HF 卡） | 必须重嵌 | 判定 |
|---|---|---|---|---|---|---|---|
| `BeIR/scifact` | en | 5183 | 1109 | **test 300 query / 339 对 / 283 金标篇（二值，p50 1 篇/题）** | cc-by-sa-4.0 | 是（新语料，需重嵌 dense + sparse） | **采用** |
| `BeIR/nfcorpus` | en | 3633 | 3237 | 分级（graded）；每 query 相关篇数多（未核 test 明细） | cc-by-sa-4.0 | 是 | 备选：qrels 分级 + 相关篇数多 → recall@k 语义更钝 |
| `BeIR/arguana` | en | 8674 | 1406 | 1 篇/题（未核 test 明细） | cc-by-sa-4.0 | 是 | 不采：任务形态是反论点检索（非证据检索） |
| `BeIR/fiqa` | en | 57638 | 6648 | 未核 | cc-by-sa-4.0 | 是 | 不采：语料 5.7 万，超规模锚点 |
| `BeIR/scidocs` | en | 25657 | 1000 | 未核 | cc-by-sa-4.0 | 是 | 不采：语料 2.6 万（引用推荐任务） |
| `BeIR/trec-covid` | en | 171332 | 50 | 未核 | cc-by-sa-4.0 | 是 | 不采：语料 17 万 / 仅 50 query |
| `mteb/T2Retrieval` | zh | 118605 | 22812 | 未核 | apache-2.0 | 是 | 不采：中文但语料 11.9 万，≫2k |
| `C-MTEB/T2Retrieval` | zh | 118605 | 22812 | 未核 | 卡缺 license | 是 | 不采：同规模 + 许可不清 |
| `mteb/DuRetrieval` | zh | 100001 | 2000 | 未核 | 卡缺 license | 是 | 不采：语料 10 万 + 许可不清 |
| `C-MTEB/CmedqaRetrieval` | zh | 100001 | 3999 | 未核 | 卡缺 license | 是 | 不采：语料 10 万 + 许可不清 |
| `mteb/MMarcoRetrieval` | zh | 106813 | 6980 | 未核 | 卡缺 license | 是 | 不采：语料 10.7 万 + 许可不清 |
| `CRUD-RAG` | zh | — | — | — | HF 卡取不到（401） | — | 不采：HF 无数据集卡，数据在 GitHub；且它是**生成**导向基准（检索是子任务） |

## 选型结论

**采用 `SciFact`（BEIR 打包：`BeIR/scifact` corpus+queries / `BeIR/scifact-qrels` test）**，理由：

1. **规模**：corpus 5,183 / test 300 query（283 篇金标）——是候选里**最小的干净单跳集**；另外提供 `--docs 1500` 的 **gold-complete 子采样**档满足「≤~2k 条目」锚点。
2. **任务形态**：claim → abstract 的**单跳证据检索**，正好覆盖本票要的「单跳检索质量」面。
3. **qrels 干净**：二值、p50 1 篇/题（max 5）→ recall@k / nDCG@10 语义清晰；NFCorpus 分级稠密会让 recall@k 变钝。
4. **许可**：HF 卡 `cc-by-sa-4.0`（corpus / queries / qrels 三件套一致）——可核、可归属。
5. **成本**：文档 p50 **1330** 字（MultiHop-RAG 新闻正文 p50 7836 字）→ CPU 重嵌成本远低于「609 篇 ≈47min」的锚点（实测见结果文件）。

**不采中文集的原因**：MTEB/C-MTEB 的中文检索集 corpus 都在 **10 万级**，既超 ≤2k 锚点，多数 HF 卡还**缺 license**；`CRUD-RAG` 取不到 HF 卡且是生成导向基准。
→ 语言维度的边界如实记为**未覆盖**（这正好是「外部集只作机制证据」的一部分）。

## 边界

- 外部语料**只作机制证据**、**不声称本库增益**（ADR-0026 D5/D6）；域（生物医学）/ 语言（英）/
  任务（单跳 claim 检索）**都不可外推**到本 KB。
- `--docs 1500` 子采样是 **gold-complete**（保留全部金标 + 随机干扰项），干扰项密度低于全量 → 数字**乐观于官方 BEIR**，故只作机制 smoke；
  全量档（`--docs 0`）才是可与外部量级对话的那个数。
- 我们**不与 BEIR 排行榜比**：我们的链路是 BGE-M3 + BM25 + DBSF（且条目级整篇送嵌），
  官方榜用不同编码器 / 切块；论文数字只作量级锚点。
