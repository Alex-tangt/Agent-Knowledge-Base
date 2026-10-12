# 人工审核清单（2026-10-08，下一批开工前）

> **用途**：AI 能自证"跑通了 / 数字对得上"，但判不出"**是不是该这么跑**"。
> 下面每条都标了【为什么只能人看】——即机器判不出来的那部分。
>
> **优先级**：**P0** = 开工前必须看（会影响下一步做什么）· **P1** = 本批内看 · **P2** = 有空再看。
> **翻译说明**：多数 prompt 原文就是中文（逐字抄录，不需翻译）；只有 **P-8** 是英文，附中文翻译。

## 0. 建议的阅读顺序

1. **§2「prompt 与运行时行为不一致」** —— 5 条，最可能出真问题，约 5 分钟
2. **§1 的 P-3 / P-5**（工具描述 + SKILL）—— 两个最容易漏、且已被实测打脸的地方
3. **§7 未决决策点** —— 需要你拍的
4. 其余（§3–§6、§8）按需

---

# §1 Agent Prompt 面（共 8 个）

> 前 7 个原文是中文，逐字抄录；第 8 个是英文，附中文翻译。

## P-1 loop 系统提示词（模型每轮都读）

**位置**：`memory_agent/agent_loop/loop.py:29-36`

```
你是「记忆检索循环」中的一步：根据问题与已检索证据，判断是否足以作答。
- 证据不足 → 只回一行：NEXT_QUERY: <聚焦的新 query>（不得重复已用 query）
- 需要读回某条证据的全文 → 只回一行：TOOL: <工具名> <JSON 参数>
- 足以作答 → 回：ANSWER: <答案>
- 找不到 → 只回：INSUFFICIENT
不要输出其它内容。
```

**核对点**
- 【为什么只能人看】它把"**判断是否足以作答**"整件事交给模型自评。而我们实测：这条自评**与"证据齐不齐"脱钩**（44/66 在证据不齐时就自称能答），且**原理上发现不了"缺失不留痕"**。
- 提示词里**没有告诉模型预算是多少**（3 轮 / 最多 20 条）→ 见 §2-①。
- `TOOL: <工具名> <JSON 参数>`：**没说参数名从哪来**（其实来自工具目录），而模型因此 21 次传错参数名 → 见 §2-④。

## P-2 loop 用户消息模板（含**动态渲染的工具目录**）

**位置**：`memory_agent/agent_loop/loop.py:236-241`

```
问题：{question}

可用工具：
{describe_tools(self.tools)}

已用检索 query：
{history_text}

已检索证据：
{evidence_text}

已派发工具结果：
{results_text}
```

其中 `evidence_text` 每条渲染为 `[id] 标题\n摘要`，**摘要只有前 240 字**（`memory/index.py:334`）；
`results_text` 每条截断到 2000 字。

**核对点**
- 【为什么只能人看】240 字摘要 vs 标准事实在文章中的位置 p50 ≈ 2316 字 → **模型第一眼看到的天然看不全**。这是"必须靠工具补"的根因，但提示词里没有任何提示。
- 工具目录是**动态渲染**的（`describe_tools`），所以 P-3 的内容会真的进 prompt —— 见下条。

## P-3 ★ 八个工具的描述（模型实际读到的"工具说明书"）

**位置**：`memory_agent/agent_loop/tools.py:293-451`；渲染规则 `tools.py:783-798`

渲染格式：`- 工具名(参数*, 参数)：描述`——**`*` 表示必填，但 prompt 里从没解释过 `*` 是什么意思**。

| 工具 | 参数（`*`=必填） | 描述（原文） |
|---|---|---|
| `memory_search` | `query*`, `k` | 语义检索记忆条目（可写 KB + 只读语料）。 |
| `memory_get` | `entry_id*` | 按 id 读回条目真实 Markdown。 |
| `memory_list` | `owner, tag, type, status, source_prefix, glob, writable, group_by, limit, offset` | 浏览记忆条目（目录树 / owner / tag / type / status），由宽到窄的第一步；不返回正文。 |
| `memory_grep` | `pattern*, glob, regex, ignore_case, context, limit` | 在可见条目正文里做字面 / 正则精搜（词典外 token：sha、环境变量名、编号）；glob 收窄来源。 |
| `memory_read` | `entry_id*, start_line, end_line, max_lines` | 按 id + 行范围读回条目（1-based 闭区间，省 token）；越界自动夹取并如实报告。 |
| `memory_outline` | `entry_id, limit` | 看标题结构（跳过 fenced code block）；不给 entry_id = 看全局大纲。 |
| `memory_history` | `entry_id*, op, pattern, added_only, rev, limit` | 看条目文件的 git 历史（只读）：log（可 -S 模式 / 只看新增）/ diff / blame。 |
| `memory_links` | `entry_id*, depth` | 看条目的 supersede 链（supersedes / superseded_by 双向 + 链）。 |

**核对点（这条最容易漏，也最该看）**
- 【为什么只能人看】**工具描述就是 prompt**。名字叫得对不对、参数名顺不顺、描述里的动词是不是模型熟悉的，直接决定它会不会用。
  实测后果：**4/6 导航工具 0 调用**；`memory_read` 的 `entry_id` 被模型 **21 次**写成 `id`（运行时返回 error，行为正确）；
  `memory_get` 被传成 list → `TypeError` → **整题中止**（缺陷票 #75）。
- `memory_get(entry_id)` 与 `memory_read(entry_id, ...)` **功能重叠**（一个读全文、一个读行窗）——
  模型要分辨这两者，成本很高；这也是"工具集不轻"的来源之一。
- 我们正在讨论的重构（4 原语 + 状态式 scope）**就是要改这张表**，所以这张表是这次重构的**验收对象之一**。

## P-4 MCP 面的工具描述（宿主 agent 用，不是 loop 用）

**位置**：`memory_agent/mcp_server.py`（未逐字抄录，只确认了结构）

工具：`memory_search` / `memory_get` / `memory_add` / `memory_supersede` / `memory_archive` /
`memory_reindex` / `memory_index_status` / `memory_ingest_list|include|exclude`

**核对点**
- 【为什么只能人看】**同一批工具在两处各写了一遍描述**（这里 + `SKILL.md` 的表格）→ 天然会漂。
  建议：挑 `memory_search` 与 `memory_get` 两个，看两处说法是否一致。
- 注意：本次要重构的 agent loop **不用这一面**（它用 P-3），所以这一面可以放到后面看。

## P-5 ★ `skill/SKILL.md`（随包交付给宿主 agent 的规劝）

**位置**：`memory_agent/skill/SKILL.md`（166 行）

**★ 最需要你看的一处：第 59–61 行**

```
> 依据本仓库实测（`experiments/agentic-rag-census/phase_b/`）：迭代使答案 **+10.2pp** / 证据召回 **+9.1pp**，
> 增益主要补「有证据但没排前」；**两跳拿走大部分**；**裸改写无可靠增益**；LLM 自判的**早停率 35.8%**
> 是最主要失效（故第 6/7 条）。**这是外部语料上的机制证据，非本产品保证。**
```

**核对点（P0）**
- 【为什么只能人看】这句话里的 **+10.2pp / +9.1pp 是 #48 Phase B 的数字**——那是**旧的评测臂**
  （不是交付运行时、没有导航工具、且证据额度未对齐）。而 **A″（交付运行时，N=200）实测：同题同深度下
  增益不显著（+0.011），追平深度后归零**；本轮还查明 **"早停率 35.8%" 是拿分子分母算出来的上界**，
  且 A″ 之后又发现"早停是症状不是病根"。
  ⇒ **skill 正在向 agent 宣称一个已被后续测量削弱的结论。** 这句话怎么改（删掉？改成 A″ 的口径？加限定？）
  **需要你决定**——它直接决定宿主 agent 会不会继续相信"多迭代就有用"。
- 第 54 行「停止（任一即停）：① 要点已被覆盖 / 已得答案」= **模型自评**，与我们测出的"自评与证据齐不齐脱钩"冲突。
- 第 50–52 行的"再查一跳"规则**没有给"换检索面"的动作**——因为工具面根本没有这个能力
  （这正是我们要补的 scope）。所以**规则与能力目前不匹配**。
- 第 3 行（frontmatter description）也很长，它会被宿主读到 → 建议也过一眼。

## P-6 子代理定义 `agent/memory-research.md`

**位置**：`memory_agent/agent/memory-research.md`（45 行，随包交付）

**前置声明**（frontmatter）：
```yaml
description: 多跳/多约束记忆检索：在独立子会话里按迭代规则检索全局记忆与只读语料，只回结论与 id 引用，检索过程不进主对话。用于跨条目/多事实/多约束的问题。
mode: subagent
hidden: true
temperature: 0.1
steps: 8
permission:
  "*": deny
  "skill": allow
  "memory-agent_memory_search": allow
  "memory-agent_memory_get": allow
  "memory-agent_memory_index_status": allow
```

**正文（关键几行原文）**：
```
1. 需要组织约定与工具用法时，先用 `skill` 加载 `memory-agent`（若尚未加载）。
2. **先做一次 `memory_search`**（正常 `k=5`）。回答依赖持久事实时加 `exclude_retired=True`（取新弃旧）。
3. **逐要点核对**：把回答该问题所需的**要点 / 约束**列出来，逐条看命中的 `snippet` 是否支持。
4. **有缺口 → 再做一跳**：从**已命中的条目内容**里找**缺口 / 实体 / 未满足的约束**，写一个**聚焦**的新 query。
   **别只把原句泛泛改写**——改写本身没有可靠增益。
5. **预算**：默认**最多 2 次追加检索**（共 ≤3 轮）；两跳通常拿走大部分增益。够了就停。
6. **停止（任一即停）**：① 要点已被覆盖 / 已得答案；② 本轮**没有新条目**；③ 用满预算。
7. **别早停**：仍有要点无证据支持就再补一跳；补不到才收手。
8. 要看原文用 `memory_get(id)`。
## 输出（严格）
- 正常：一段**结论**，附**依据 `id` 列表**。
- 预算用尽仍缺证据：**单行** `INSUFFICIENT`（不硬答、不编造）。
## 边界
- 只读：**不写记忆**。
- 你看不到主对话：检索 query 必须自足。
```

**核对点**
- 【为什么只能人看】第 3、4 条**已经就是你想做的事**（"先列出要点/约束，再逐条核"）——
  也就是说这个想法**已经以"规劝"的形式存在**，但**没有机械核验兜底**，所以它可以被无视（实测：37.7% 的点名来源从未被核）。
- 第 7 条"别早停"是**规劝**，而实测后续轮只贡献 4.6% gold —— 规劝对了一个不存在的病？
  ⇒ 这条措辞要不要改，属于 §7 决策点。
- `permission: "*": deny` 在前、`allow` 在后（依赖"最后匹配胜出"）—— **这条依赖 opencode 的权限语义**，
  值得你确认一下它是否符合预期（我们没在**真实宿主**里验证过隐藏子代理的权限是否按此生效）。

## P-7 插件 `plugin/memory-research.js`

**位置**：`memory_agent/plugin/memory-research.js`

工具描述（原文）：
```
多跳记忆检索（阻塞）：在独立子会话里用 memory-research 子代理检索全局记忆与只读语料，
返回结论与依据 id，检索过程不进主对话。hop 预算由本工具控制。
适合跨条目 / 多事实 / 多约束的问题；query 需自足（代词 / 省略先消解）。
```

每跳发给子代理的提示词（原文，`buildPrompt`）：
```
（检索协议：若仍需追加检索，**只回一行** `NEXT_QUERY: <聚焦的新 query>`，不要给结论；
否则给出结论 + 依据 id 列表；无证据回 `INSUFFICIENT`。最多追加检索 {maxHops} 次。）
```
第 0 跳 = `query + 协议`；后续跳 = `基于你已检索到的证据，继续检索这个缺口：{query} + 协议`。

**核对点**
- 【为什么只能人看】**同一套协议第三次被重写**（P-1 的 `NEXT_QUERY` / P-6 的 `INSUFFICIENT` / 这里的协议文本）。
  三处措辞不一致 → 建议人工统一一次。
- `hops` 的终止条件：`if (!next || next === query) break` —— **只要回复里任何一行匹配 `NEXT_QUERY:` 就续跳**。
  如果子代理在长结论里顺带提到这个词，会被误当跳转请求。值得你判断风险。
- 返回文本会被追加一段 `[memory_research: hops=N elapsed=Ns]` **混进结论里**——主对话看到的是"结论+元数据"。
  UX/卫生问题，你决定要不要留。

## P-8 评测 prompt（英文）— `experiments/agentic-rag-census/phase_b/prompts.py`

> 这一面**不是产品 prompt**，是 #48 评测用的。放在这里是因为它里面有一条**已被实测打脸的假设**（见 ★）。

**① 改写（B2）** `REWRITE_TEMPLATE`（英文原文）
```
Rewrite the question below into ONE concise search query for a document retrieval system.

Rules:
- Preserve every distinguishing entity, person, organization, date, number and comparison/relation that the question asks about.
- Do not answer the question and do not add facts that are not in the question.
- Remove filler, politeness, and attribution phrases that do not help identify the answer (e.g. "according to X", "as reported by Y").
- Output a single line: only the rewritten query. No explanation, no quotation marks.

Question:
{question}

Rewritten query:
```
**中文翻译**
```
把下面的问题改写成一条简洁的检索 query。

规则：
- 保留问题涉及的每一个区分性实体、人物、组织、日期、数字、比较/关系。
- 不要回答问题，不要添加问题里没有的事实。
- 删掉对识别答案无帮助的填充词、客套话与**出处短语**（例如 "according to X"、"as reported by Y"）。
- 只输出一行：改写后的 query。不要解释、不要引号。

问题：
{question}

改写后的 query：
```

**★ 核对点（P0）**
- 【为什么只能人看】第三条规定**明确要求删掉出处短语**（"据 X 报道"）。
  而我们刚测出：**出处/来源正是一条 37.7% 从未被核验的约束**，而且在 `mhr2475` 那个案例里，
  缺掉的标准文章**恰恰就是那条出处指向的稿件**。
  ⇒ **这条规则把"该保留的约束"当成"噪声"删掉了。** 它要不要改、改成什么，需要你决定。
  （注：这正是 #48 的 B2 臂，实测"裸改写无增益"——现在看，一部分原因可能就是它删掉了约束。）

**② 充分性判据（B3）** `JUDGE_TEMPLATE`（英文**节选**，省略号处为省略）
```
Determine whether the retrieved evidence below is sufficient to answer the original question. Use ONLY the evidence; do not use outside knowledge.
...
Return ONLY one valid JSON object with exactly these keys:
{"enough": true or false, "missing_information": "...", "next_query": "...", "evidence_ids_used": ["E1","E2"], "reason": "one short sentence"}
Rules for "next_query":
- If the evidence is sufficient to answer, it MUST be an empty string.
- If it is insufficient, it MUST use an entity or fact already present in the evidence, and MUST NOT repeat any previous search query.
- It MUST be about the information still missing, NOT a restatement of the original question.
```
**中文翻译（对应节选）**
```
判断下面检索到的证据是否足以回答原问题。**只用证据**，不要用外部知识。
……
只返回一个合法 JSON 对象，键恰好是：
{"enough": 真/假, "missing_information": "还缺什么或空串", "next_query": "新 query 或空串",
 "evidence_ids_used": ["E1","E2"], "reason": "一句话理由"}
next_query 规则：
- 若证据已足够，必须是空串。
- 若不足，必须用**证据里已出现**的实体或事实，且**不得重复**任何先前的 query。
- 必须针对**仍然缺失**的信息，不是把原问题换个说法。
```

**核对点**
- 【为什么只能人看】这里的充分性判据**比 loop 的版本好得多**：它要求
  ① 只用证据、② 显式列 `missing_information`、③ 显式给 `evidence_ids_used`。
  **而我们交付的运行时（P-1）把这三条全丢了，只留"你能不能答"。**
  ⇒ 值得你判断：**要不要把 B3 这套判据搬进产品运行时**（我们第四轮讨论里说的"机械核验优先"，
  可能就是从这个版本演化来的）。

**③ 答案 / 判定** `ANSWER_TEMPLATE` / `ANSWER_JUDGE_TEMPLATE`
- 答案 prompt 说证据不足时回 **`Insufficient Information`**；而 loop / 子代理 / 插件用的是 **`INSUFFICIENT`**
  ⇒ **同一件事三种写法**（跨面不一致，P1）。
- 判定 prompt 明说"接受同义/别名/缩写/重排"、"拒绝不同实体/矛盾/说信息不足"——口径清楚，**暂无异议**。

---

# §2 Prompt 与「运行时实际行为」的一致性（P0，最可能出真问题）

| # | Prompt 说什么 | 运行时实际做什么 | 风险 |
|---|---|---|---|
| ① | **从没说预算是多少** | `Budget(max_rounds=3, max_evidence=20)`，实际展示上界 15 | 模型不知道自己有多少额度 → 实测 **72% 提前停** |
| ② | P-5/P-6 说"最多 2 次追加（≤3 轮）" | loop 是 3 轮；两者一致，但**产品 loop 的 prompt 没写** | 只有读 skill 的宿主知道预算，跑 loop 的模型不知道 |
| ③ | P-1 说"证据不足 → `NEXT_QUERY:`" | 实测 **13% 的题**在这里失配（`fallback`，输出读不出下一步） | 协议没被稳定遵守；`#63` 是 26%，样本加大后 13%，**仍高于任何可接受阈值** |
| ④ | P-3 渲染成 `entry_id*`，**没解释 `*`** | 模型 21 次写 `id`、1 次传 list | 接口摩擦**有实测**，是"工具不轻"的直接证据 |
| ⑤ | 多处出现"逐要点/约束核对"（P-5、P-6） | **没有任何机械核验**，全靠模型自觉 | 实测 **37.7% 的点名来源从未被核** |

---

# §3 数字的分母与口径（P0 —— 最容易被悄悄改掉的地方）

| 数字 | 分母/口径 | 常见写错 |
|---|---|---|
| 证据召回 | **176**（可答题）；不是 200 | 把 24 条 `null_query` 算进分母 |
| 「有答案的题」 | **66 / 176** | 误写成 176（本次 issue 就写错过，被 teammate 纠正） |
| 来源约束未核 | **严格口径 66/175 = 37.7%**；宽松口径 35/175 = 20% | **不带口径引用**（我自己第一次就踩了） |
| 判分三档 | 主口径 95.45% / 97.73%；严格档 59.09% / 84.09% | 只报一个档 |
| `fallback` | 26/200 = 13.0%；分母是**全部 200**，不是可答 | 换分母 |
| 逐轮边际 | 后续轮合计 **4.6%** gold；救回 **6/176** 题 | 说成"迭代完全无用" |

---

# §4 结论与边界声明（P0 —— 防止过度声称）

- **`Cad`（深度匹配对照）必须永远标"事后、探索性"**，不能当预注册结论引用。
- **#63 的脚本臂 +0.263** 只是"面 + 工具执行"的上界（决策有答案泄漏），**不许当 agent 显著性**。
- **A″ 的负结论不能推出"报告更差"**（#76 已确立：缺 gold 没让答案变差）。
- **外部语料只作机制证据**，不得声称"本知识库产品提升了 X%"。
- **A‴ 的 2×2 只有 2 个错答案** → 只能说"**没有证据显示有害**"，不能说"证明无害"。

---

# §5 ADR / 地图文档里的更正（P1 —— 重点核"引用链"）

本轮我（AI）**改口过两次**，都落在 `docs/adr/0030`：

1. 「首轮即收手 = 54%」→ **错**，正确是 **127/176 提前停**（54% 是"在**某一轮**自称够了"）。
2. 「下一个杠杆是停止判据」→ **修正**为「停止判据是**症状**；病根是迭代没换检索面」。

**核对点**：请抽 2–3 个数字，核对它能否**追到一张表或一个脚本**（`a3_results.json` /
`control_rankings.json` / `lead_verify_76.py` / `lead_round_yield.py`）。
【为什么只能人看】机器能验证"脚本跑通"，但验证不了"这句话配得上这个数字"。

---

# §6 安全与证据保全（P0）

- [ ] 密钥只在进程环境：`DASHSCOPE_API_KEY` 取自宿主既有 `.env`，**未提交**、未回显、未落日志
- [ ] 入库文件**不含**数据集原文 / 宿主绝对路径（已抽查 `a3_results.{md,json}`、`report.md`）
- [ ] `experiments/agentic-rag-census/phase_a2/artifacts/trace/` 被 `.gitignore` 覆盖（含案例文件）
- [ ] **本地测量基座已保全**：`data/`（语料）· `store/`（索引，重建要 ~47 min）·
      `a1_traces.jsonl`（原始轨迹）· `a3_grades.jsonl` + `a3_constraint.json`（判分与核验证据）
- [ ] `git worktree list` 只剩主树；`git status` 干净

---

# §7 未决决策点（P0 —— 需要你拍）

1. **P-5 那句 +10.2pp/+9.1pp 怎么改**（skill 正在向 agent 传播被削弱的结论）
2. **P-8 那条"删掉出处短语"要不要改**（它删的正是 37.7% 没被核的那类约束）
3. **B3 的充分性判据（只用证据 / 列 missing / 列 evidence_ids）要不要搬进产品运行时**
4. 工具重构：4 原语确认（`search` / `match` / `read` / `list`）+ 状态式 scope + **只可收窄**
5. `final.report` 字段表（你已定"采用 report"）
6. **E1（作用域收益上界，免费）现在跑不跑**

---

# §8 AI 自身复核的盲区（P1 —— 提醒你重点看这里）

- **我写的复算脚本仍可能与被测脚本共享口径**（我尽量用了独立实现 + 手写 bootstrap，但值得你扫一眼
  `lead_verify_74.py` / `lead_verify_76.py` 的取数是否独立）。
- **本轮的 2× 口径差异说明同类错误还会再发生**：凡是我给的百分比，都请顺手问一句"**分母是什么**"。
- **未经验证的推断**目前有两条挂在讨论里（清单漏报率、作用域收益），**都还没测**，不要当结论用。

---

# §9 可以跳过不看（省时间）

- `legal_web/**` —— 已排除的上一版产品，不优化、不依赖、不作对象
- `tests/unit/**` 的断言细节 —— 有 CI 兜底（`pytest` 647 passed / 3 skipped）
- 实验脚本内部实现（`run_a2.py` / `a3_*.py`）—— 已由 Lead 独立复算覆盖，除非你要改它们
