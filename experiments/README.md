# experiments/

一次性调查类实验的所在地。**每个实验一个目录，脚本 + 数据 + 结论同处一目录**（ADR-0004 规则 4）。

## 约定

- 目录名 = 实验短名（kebab-case），如 `refusal-root-cause`。
- 每个目录必须包含一个 `README.md`（记录：问题 → 假设 → 设置 → 数据 → 结论）。
- 可运行的脚本留在目录内，禁止把脚本丢在 `ragcore/`/`legal_web/` 根或 `tests/` 里当孤儿。
- 跑完必须写结论；没有结论的实验目录不算完成。
- **进度反馈**：耗时的实验脚本必须带进度条（tqdm）或阶段打印，禁止无输出地长跑；模型加载用 `local_files_only=True`（缓存优先），避免 HF 网络检查卡死。教训：rerank-latency POC 曾因无进度 + HF 重试卡死 15 分钟。

## 现有实验（待迁移/回填）

- `query-rewrite-optimizer/` — 由 `backend/optimizers/` 迁入，回填结论。
- `relevance-calibration/` — 由 `backend/calibrate_relevance.py` 迁入。
- `refusal-root-cause/` — 本周拒答根因调查（迁移 `backend/diagnose_refusals.py` 等，产出结论）。

## 与 canonical 工具的区别

`tests/` 是**评估子系统**（run_eval / score_eval / questions / ground_truth），是常驻工具，不属于 experiments。实验与工具的界限：常驻、被重复使用 → 工具；一次性调查、有结论要留下 → 实验。
