# 0020 reranker 序列长度上限：默认 512 且只作用于重排输入

Status: proposed

## 背景

`ragcore/services/reranker_service.py` 未设 `max_length` → 吃 `bge-reranker-v2-m3` 的
tokenizer 默认 `max_seq_length=8192`。rerank 占 legal e2e 延迟 ~83%
（`experiments/e2e-latency/results.md`），#28 要在 `#24` harness 上 A/B
`{8192, 1024, 512}` 选一个不掉质量的默认值。

实测前提（sbert 5.4.1）：`CrossEncoder.predict` 按长度排序 + 按 batch 内最长对动态
padding，故 `max_length` **只在 query+doc 真的超限时截断**，不是恒定 pad。证据
`experiments/rerank-latency-survey/maxlen_results.md`。

## 决策

- **D1 默认上限 512**：`ragcore/config/config.py` 新增 `DEFAULT_RERANK_MAX_SEQ_LENGTH=512`，
  `RERANK_MAX_SEQ_LENGTH`（读 env `RERANK_MAX_SEQ_LENGTH`）为生效值；`RerankerService`
  默认取该值，对 legal 与 memory 同时生效。这是 core 层配置（无密钥、import 不校验，ADR-0016）。
- **D2 上限语义 = 只截断送进重排器的 query+doc，不截断回给 LLM 的正文**。截断唯一的
  影响面是**重排排序**，不改变生成上下文。
- **D3 可一键回退**：env `RERANK_MAX_SEQ_LENGTH` 设 `none/off/0/空串` → 不设上限，
  回到模型默认（≈8192）；也可显式传 8192。
- **D4 本票不追大延迟收益**：A/B 证明 legal 真正 binding 的是 ~820 token（1024/512 都不
  改变结果），token 上限要下到 ~256 才 ~1.4x 且有排序漂移。降 legal rerank 延迟的主杠杆
  改走**候选池裁剪**（池 20→10 近线性减半），token 上限保留为兜底。

## 理由

- memory 侧 1119 个 query→doc 对 max=381 token，{8192,1024,512} 全部 no-op；
  512 档实测 `run_hash` 与基线 `d9d2311f2342a7b8` **逐位一致** → nDCG@10=0.9658 守门通过。
- legal 侧 20 题真实池：1024/512 与 8192 的 top-8 重合 / Kendall tau **均为 1.000**、
  min_dist 均值逐位相同（0.2316）、判拒数不变（3）→ 512 不掉质量。
- 512 只削最长法条块的尾部、改动太小 → 延迟无收益；256 才 1.4x 但触及分数标定
  （0.2316→0.2535）与轻微排序漂移。用 512 作默认 = 零质量代价的兜底上限。
- 回退语义清晰：不设上限即旧行为。

Considered options:
- **A 默认 512（采用）**——零实测质量变化 + 对超长输入兜底；代价是没拿到延迟收益。
- B 默认 1024——对当前 legal 严格 no-op（等价旧行为）；对更长文档的兜底弱于 512。
- C 默认 256——~1.4x 延迟，但超出本票 A/B 范围、会截断 759/1119 个记忆对（需另验 nDCG）、
  且有排序/标定漂移；弃。
- D 不设上限（维持现状）——放弃兜底，且把「送排长度」杠杆留在未文档化状态；弃。

## Consequences

- `RerankerService(max_seq_length=None)` = 旧行为；`ragcore` 配置多一个 core 层 knob。
- 延迟账：legal rerank 维持 ~11.5s/pool20 量级；真正的延迟下降留给候选池裁剪（#21）。
- 若将来语料长度分布变化（如调大 `ARTICLE_MAX_CHARS`、引入长文档 KB），512 会成为
  binding 上限——届时应重跑本 A/B 再定默认。

Relates: #28、#21、#24、ADR-0016；证据 `experiments/rerank-latency-survey/`。
