# Phase A″ 报告（ticket #74）— 真 agent 循环 × MultiHop-RAG，N=200

- 预注册：`prereg_commit=fff00c2` + R1 `prereg_amend=55472c4`（同目录 `PREREGISTRATION.md`）。
- 被测对象：memory_agent/agent_loop (delivered, unmodified) + MemoryNavToolRegistry (8 tools)；`exclude_retired=False`（= 运行时出厂默认，R1(a)）。
- 循环：`Budget(max_rounds=3, max_evidence=20)`、`k=5`；LLM：`qwen3.7-flash` / T=0.0 / seed=42。
- 题集：200 条（可答 176 + null_query 24）；主指标只在可答子集上算。
- 完成 200 / 错误 0 / 缺 0；并发 4；墙钟 3747.8s。
- **边界**：外部语料只作机制证据，**不声称本 KB 增益**；**不判答案正确率**（未调裁判 LLM，`answer` 随 trace 落盘）。

## 1. Step 0（零 LLM）— 控制臂重算 + 与 #47 一致性

| k | 本次重算 recall@k（200 题） | #47 同 200 题 recall@k | Δ |
|---|---|---|---|
| 1 | 0.2495 | 0.2495 | 0.0 |
| 5 | 0.6482 | 0.6482 | 0.0 |
| 10 | 0.7812 | 0.7812 | 0.0 |
| 15 | 0.8537 | n/a | n/a |
| 20 | 0.9029 | 0.9029 | 0.0 |
| 50 | 0.9616 | 0.9616 | 0.0 |

- **逐位一致性（gate）**：full-ranked 逐位相同 **200/200**；top-5 相同 200/200；top-20 相同 200/200；**首次分歧位置 = None**（None = 无分歧）；gate_passed = **True**；与 #48 可比性不降级 = **True**。
- relevant 集合不一致题数：0。
- id 映射：`per_query_ids.json` 存**整数序号**（R1(b)），核对前统一为同一种表示。
- store 目录签名：before `626e174119757612` / after `626e174119757612` → **未变 = True**（只读证明）。
- 金标不在索引里的槽位：0。

### R1(a) `exclude_retired` 的机械差异（零 LLM 实测）

- 同 query 下 `exclude_retired=True` vs `False` 的 top-5 逐位相同题数：**200/200**；不同题 0 条。
- A1 实际用值：**False**（运行时出厂默认）。**不得**把本票结果说成「延续 #63 同一设置下的复核」。

## 2. 三臂召回与配对（主指标 = mean_evidence_recall）

| 臂 | mean_evidence_recall | n |
|---|---|---|
| A1 | 0.6932 | 176 |
| C20 | 0.9029 | 176 |
| C5 | 0.6482 | 176 |
| C15 | 0.8537 | 176 |
| C50 | 0.9616 | 176 |

| 比较 | Δ | 95% CI | 显著 | n |
|---|---|---|---|---|
| 主：A1 − C20 | -0.209754 | [-0.250474, -0.169034] | True | 176 |
| 次：A1 − C5 | 0.044981 | [0.026989, 0.065341] | True | 176 |
| 描述：A1 − C15 | -0.160511 | [-0.19697, -0.124527] | True | 176 |
| 描述：A1 − C50 | -0.268466 | [-0.312027, -0.226326] | True | 176 |

### 读法（额度口径的机制事实）

- `AgentLoop` 每轮 `k=5`、最多 `max_rounds=3` 轮 → A1 **实际展示上界 = 15**（实测 mean 6.445 / p50 5.0 / max 15），`max_evidence=20` 在本题集上到不了。
- 所以 **C20 只是「名义」额度匹配**（20 额）；**C15 才是「实际」额度匹配**（3×5）。两者 A1 都**显著低于**一次性检索（C20 Δ -0.209754、C15 Δ -0.160511）→ 负结论**不是**单一对照选择造成的。

### 预注册分层（`nav_used` / `search_only`）

| 层 | n | A1 | C20 | Δ | 95% CI | 显著 |
|---|---|---|---|---|---|---|
| nav_used | 51 | 0.795752 | 0.941177 | -0.145425 | [-0.218954, -0.081699] | True |
| search_only | 125 | 0.651333 | 0.887333 | -0.236 | [-0.286667, -0.186667] | True |

## 3. `fallback` 协议失配率（预注册 §6，R1(c) 双分母）

- `fallback / completed` = **0.13** （26/200）；95% bootstrap CI [0.085, 0.18]；Wilson CI [0.090282, 0.183664]。
- `fallback / 200`（保守上界）= **0.13**；CI [0.085, 0.18]。
- 判定带 0.1（开跑前写下）→ **判定：undecided**。
- 未决时的算账（Wilson，另加 N）：{"needed_n": 390, "at_rate": 0.130769, "wilson": {"lo": 0.100879, "hi": 0.167862}, "reason": "lower_bound_above_band"}
- 完成题数 < 190 则声明证据不足：**False**（completed=200）。

## 4. 描述性项

- 停止触发器分布：`{"answer": 68, "budget": 64, "fallback": 26, "insufficient": 1, "no_new_ids": 41}`
- A1 每题展示条目数 mean/p50/max：6.445 / 5.0 / 15（循环展示上界 = 15）。
- nav 工具使用率：0.255 （51/200）。
- 逐工具调用次数：`{"memory_get": 65, "memory_grep": 10, "memory_read": 66, "memory_search": 341}`
- 每题 `memory_search` 次数分布：`{"1": 118, "2": 23, "3": 59}`
- 轮数分布：`{"1": 61, "2": 30, "3": 109}`
- 每题耗时 p50/p95/max (s)：46.37 / 203.991 / 420.131（mean 64.1279）。
- LLM 单题耗时 p50/p95/max (s)：44.961 / 203.306 / 418.694；LLM 调用总数 448。
- `memory_grep` 单次耗时（本次 10 次调用）p50/p95/max (s)：0.3989 / 0.9248 / 0.9248。
- 冒烟实测 `memory_grep`（609 条目）：`{"full_scan_no_hit": {"pattern_len": 26, "hits": 0, "elapsed_s": 0.3644}, "early_exit_limit50": {"pattern_len": 3, "hits": 50, "elapsed_s": 0.003}, "mid_frequency": {"pattern_len": 4, "hits": 50, "elapsed_s": 0.0069}}`
- `memory_history` / `memory_links` 调用与形状：`{"memory_history": {"calls": 0, "errors": 0, "shapes": {}}, "memory_links": {"calls": 0, "errors": 0, "shapes": {}}}`
- 错误题数：0（id 见 report.json）；缺失题数：0。
- 断点续跑：**首轮失败 3 题**（2 × LLM 超时 + 1 × 运行时 TypeError，明细见 report.json `attempts`），重试 3 题后**最终 0 错误**；指标用最后一轮记录（总墙钟 3747.8s，2 趟）。

## 5. §7 两条口径（如实）

1. `MemoryIndex.search` 给模型的 `snippet` 只有前 240 字，而 gold fact 在文章里的位置 p50 ≈ 2316 字 → 首轮检索结果天生看不全。
   - A1 展示集里包含 gold、但**不在 C20 top-20**（= 靠 agent 多轮/工具补进来）的题数：**4**。
   - 靠**导航工具**（`memory_read`/`memory_grep`/…）把 gold 补进展示集的题数：**2**（gold 槽位 2 个）。
   - 展示集 gold 超出首轮 `memory_search` top-5 的题数：**20**。
2. `memory_history` / `memory_links` 结构性近空：文章 gitignored（不在 repo 的 git 历史里）+ 无 supersede 链 → memory_history / memory_links 结构性近空；调用次数与返回形状见 descriptive，不算工具失败。

## 6. 自证

- `scorer.evaluate` 与手算逐题 recall 不一致题数：0。
- scorer overall：`{"n": 176, "mean_evidence_recall": 0.693181818, "answer_correct_rate": null, "gold_unreached_rate": 0.596591}`。
- `scorer.answer_correct_rate` 只喂**可答子集**故为 None：本票**不判答案正确率**（不调裁判 LLM；`answer` 随 trace 落盘，后续要判分不必重跑）。

## 6.5 运行时观察（本票不修；报 Lead 定票）

- **defect**（observed=1）：`memory_get` 的 `entry_id` 传成 list（模型侧）时，`MemoryToolRegistry.call` → `MemoryIndex.get(list)` 抛 `TypeError: unhashable type: 'list'`；`AgentLoop._dispatch` 只捕 `KeyError` → 整题中止（无 trace）。注意 `MemoryNavToolRegistry._load` 对非 str 有守卫，但 `MemoryToolRegistry`（GET_TOOL）没有。
- **transient**（observed=2）：LLM `APITimeoutError`（OpenAI 客户端自带重试耗尽）——恢复策略 = 断点续跑重试（见 run_meta.passes）
- **model_aci_misuse**（observed=21）：`memory_read` 用 `id` 而非 `entry_id` → 运行时如实返回 `{"error": "unknown_entry"}`（运行时行为正确，描述性）

## 7. 与预注册的偏差

- 实现注记（非预注册偏差）：Step 0 首轮实现的「#47 参照曲线」误把 24 条 null_query 计入分母（得 0.570 vs 正确 0.648）；发现后修正分母并**重跑** Step 0，入库产物为修正版（逐位一致性 200/200、store 签名未变，结论不变）。

## 8. 边界

- 一次观测，不是可复现性声明（LLM 非逐字节确定）；trace 仍盖 `run_hash` 供重放。
- 三臂共用**同一份** index/store（Qdrant local 同进程单 client）。
- `memory_get` 属检索面（不参与 nav 使用率口径）；`memory_list` / 全局 `memory_outline` 只导航、不带顶层 id，不占证据额度。
- 入库产物不含数据集 query 文本 / 宿主绝对路径 / 密钥；原始 trace 已被 .gitignore 覆盖，不入库。

## 9. 怎么读这份报告（面向**未参与实施**的读者）

### 9.1 这个实验在问什么

产品里有一个"检索 agent"：模型**不是**检索一次就作答，而是可以**多轮**——
搜一次 → 看结果 → 自己决定「再搜一次 / 打开某条细读 / 直接作答」。直觉上这比"检索一次取前 N 条"
更好。本实验只问一件事：**这套"多轮 + 自选工具"的机制，到底有没有比"一次性检索"更好？**

用公开多跳数据集 **MultiHop-RAG**（609 篇新闻 / 200 题）：它的标准答案不是一句话，
而是一**组必须被找到的文章**（回答要跨文章拼证据）→ 可以严丝合缝地量"有没有找到该找的文章"。

### 9.2 代号

| 代号 | 是什么 | 为什么这么设 |
|---|---|---|
| **A1** | **被测对象**：真 agent。每轮先检索前 5 条给模型 → 模型回 `NEXT_QUERY:`（再搜）/ `TOOL: xx`（调工具）/ `ANSWER:`（作答）；≤3 轮、≤20 条 | 产品里**已交付的那套代码，一行没改** |
| **C5** | 一次性检索**前 5 条** | 5 = 产品默认检索条数 |
| **C15** | 一次性检索**前 15 条** | 15 = agent **最多能展示**的上限（3 轮 × 5）→ **实际额度**对齐 |
| **C20** | 一次性检索**前 20 条** | 20 = 运行时 `max_evidence` → **名义额度**对齐（预注册指定的主对照） |
| **C50** | 前 50 条 | 只作参考曲线 |
| **Cad** | **事后补的对照**：逐题按 A1 **自己实际看了几条**截断一次性排名 | 把"看了几条"逐题钉死，只留"选得好不好" |

### 9.3 指标与数字怎么读

- **主指标 = 证据召回**：「该找的文章里，多大比例真的被放到了模型眼前」。1 = 全找到，0 = 一条没找到。
  不量"答案对不对"：那还掺生成 / 措辞噪声，且要再花钱请裁判模型；召回是**检索环节**的干净指标。
- 三个位置：`召回`（越高越好）/ `Δ`（差值）/ `CI [下界, 上界]`（**不确定范围**）。
- **"显著" = CI 不跨 0**；跨 0 就是说"数据说不清有没有差别"。
- **"预注册 / 冻结"**：**跑之前**就把"测什么、跟谁比、怎么判"写进 `PREREGISTRATION.md` 并提交，
  跑完不许改；防的是"跑完再挑一个刚好赢的比法"。所以 `Cad` 必须标**"事后、探索性"**——
  它**不在预注册里**，只能当**解释工具**，**不能**当"显著"的主证据。
- **"外部语料只作机制证据"**：MultiHop-RAG 不是我们的知识库 → 结论只说"这套机制**在这类语料上**如何"，
  **不能**说"我们的知识库产品提升了 X%"（ADR-0026 D5/D6、ADR-0030 D7.5）。
- **"Step 0 闸门 / 逐位一致"**：跑之前先确认"重建的索引给出的排名"与 #47 存的不可变证据**一条不差**
  （**200/200**）→ "与以前的实验可比"这句话才成立。

### 9.4 一句话结论与机制

**在同等证据额度下，agent 循环没有带来可测的检索质量增益。** 它相对 C5 微赢（+0.045）是因为
**多看了 1–2 条**；相对 C15/C20 大输（−0.16 / −0.21）是因为**它没把允许的额度花掉**；
与 `Cad` 打平（+0.011，CI 跨 0）说明**它的选择并不更聪明**。

机制（详见同目录 `stop_analysis.md`，**事后分析、非预注册**）：**127/176（72%）的题在轮次预算
用完前就停了**，平均只展示 ~5.5–5.8 条（允许 15）。根因是**判据对齐错了**——运行时的充分性信号是
「模型给出了终结回复」，它测的是"**我能不能答**"；而评分要求的是"**证据齐不齐**"。
