---
name: memory-agent
description: 通过 memory-agent MCP 工具读写 agent 长期记忆——检索过往知识、读回条目、写入新记忆，以及替代/归档旧条目。当用户要求"记住/存入某事实"、要查平台/版本/错误/项目等跨会话知识、或写入前需要判重时使用。读取时用 exclude_retired 取新弃旧；无替代链接的矛盾条目要先呈现、请人裁决。破坏性操作（supersede/archive）必须先拿到用户明确同意；多事实/多约束问题可迭代检索（默认 ≤2 跳）。
---

# memory-agent — agent 长期记忆

`memory-agent` MCP 服务把长期记忆暴露为工具。**Markdown 是真相源**，向量索引是派生、可重建的。
记忆分两类：**可写 KB**（`writable=true`，可 add/supersede/archive）与**只读语料**
（`writable=false`，如项目仓库文档，只能检索、不可写）。

> 两条纪律：**回答先检索；破坏性写先问人。**

## 工具

| 工具 | 用途 | 破坏性 |
|---|---|---|
| `memory_search(query, k=5, writable_only=False, exclude_retired=False)` | 语义检索条目 | 否 |
| `memory_get(entry_id)` | 按 id 读回条目真实 Markdown | 否 |
| `memory_add(title, body, section, type, tags, ...)` | 写入新条目（写前去重） | 否（不覆盖） |
| `memory_supersede(old_id, ..., confirm=False)` | 新条目替代旧条目 | **是** |
| `memory_archive(entry_id, reason, confirm=False)` | 归档条目（保留文件） | **是** |

索引维护（#13，客户端暴露时可用）：`memory_reindex(cursor=None, batch=16)` 分块重建（返回
`{done,total,processed,cursor,gen}`，拿 `cursor` 续调到 `done=true`）；`memory_index_status()`
查当前代/条数/是否自洽。

## 读：回答前先查

1. `memory_search` 用自然语言查询；命中字段为
   `id / title / source / writable / type / tags / status / owner / score / snippet`，`score` 越大越相关。
   `owner` 是域所有者（只读条目 = 来源 label），读侧可见（#45）。
2. **取新弃旧**：回答依赖持久事实时，用 `memory_search(..., exclude_retired=True)`
   排除 `status ∈ {superseded, archived}` 的已退役条目（#42）。该开关**默认 False**，
   不静默改行为。注意它只排除**已退役**，保留 `current` / `draft` / **无 status**
   （只读语料常无 status，不能被误伤）。
3. 要看原文用 `memory_get(id)`；`source` 指向的文件/URL 才是权威来源，关键结论跟过去核对。
   判断替代关系看 frontmatter 的 `supersedes` / `superseded_by`。
4. 引用时给出 `id`。本仓库内的事实优先于记忆；冲突则修正记忆（见"冲突裁决"）。
5. **不要凭记忆回答平台/版本/错误/实测数字**——先 `memory_search`；没有就直说没查到。
6. 索引未构建时报错会附上命令：`venv\Scripts\python.exe memory_agent/build_index.py`。

## 迭代检索：一次不够就再查一跳

**适用**：问题需要**多个事实 / 多约束 / 跨条目**（"X 与 Y 的关系"、"按 A 和 B 两个条件找"）。
简单单点问题**不要**迭代。

1. **先做一次 `memory_search`**（正常 `k=5`）。
2. **逐要点核对**：把回答该问题所需的**要点 / 约束**列出来，逐条看命中的 `snippet` 是否支持。
3. 有要点**没有**条目支持 → **再查一跳**：从**已命中的条目内容**里找出**缺口 / 实体 / 未满足的约束**，
   写一个**聚焦**的新 query。
   - **别只把原句泛泛改写**——**改写本身没有可靠增益**；要**用已检索到的信息**推进。
4. **预算**：默认**最多 2 次追加检索**（共 ≤3 轮）；**两跳通常拿走大部分增益**。够了就停，别为凑数而搜。
5. **停止（任一即停）**：① 要点已被覆盖 / 已得答案；② 本轮**没有新条目**（增量 ≈ 0）；③ 用满预算。
6. **别早停**：不要因为"看着像"就停——**仍有要点无证据支持就再补一跳**；补不到才收手。
7. **证据不足就明说**：预算用尽仍缺 → 直接说"**没查到 / 证据不足**"，**不要硬答或编造**。
8. 引用仍给 `id`；矛盾条目按「冲突裁决」处理。

> 依据本仓库实测（`experiments/agentic-rag-census/phase_b/`）：迭代使答案 **+10.2pp** / 证据召回 **+9.1pp**，
> 增益主要补「有证据但没排前」；**两跳拿走大部分**；**裸改写无可靠增益**；LLM 自判的**早停率 35.8%**
> 是最主要失效（故第 6/7 条）。**这是外部语料上的机制证据，非本产品保证。**

## 写：先搜 - 判重 - 再落

**写目标 = 全局知识库**（唯一经工具可写的域，所有 agent 可读可写，经写入网关）；
其它域由 agent 自己写工作区文件、本工具只读索引。写侧**无域寻址**——工具面只需
`section`（全局 KB 内的分区），不指定写到哪个域。

1. **先搜**：用拟写内容的关键词 `memory_search(..., writable_only=True)`。
2. `memory_add` 命中近似会返回 `{status:"duplicate", candidates:[...]}` 且**不写**：
   - 同一事实的更新 → 用 `memory_supersede` 取代旧条目；
   - 确认确实不同 → 以 `allow_duplicate=true` 重试。
3. 结构化字段（路径由工具决定，**没有裸文件写工具**）：
   - `section`：`topics` | `decisions` | `projects/<slug>`（全局知识库内的分区；旧名 `domain` 已弃用）
   - `type`（须与 section 匹配）：`topics`→`topic`；`decisions`→`decision`/`research`；
     `projects/*`→`project-knowledge`
   - `tags`：从 KB 的 `tags.md` 受控表取，不要自造
   - `slug`：英文 slug；纯中文标题请显式给
4. 成功返回 `{status:"written", id, path, commit, warnings, index}`；`warnings` 要转述给用户。
5. **写入只落真相源，不同步刷索引**（D13）：`index` 字段为
   `{ok:true, refreshed:false, mode:"lazy"}`——索引由**下一次 `memory_search`** 的廉价指纹检查
   （stat `mtime`+`size`）增量追平，因此刚写的条目在下一次检索即被搜到，通常**无需手动重建**。
6. 只有在检索报**索引不自洽**（manifest 条数 ≠ 集合点数）时，才调
   `memory_reindex(cursor=None, batch=16)` 分块全量重建，拿 `cursor` 续调到 `done=true`；
   `memory_index_status()` 可查当前代与是否自洽。

## 破坏性操作：必须先确认

`memory_supersede` / `memory_archive` 默认 `confirm=false`，**只返回预览、不落盘**：

```
{status:"confirmation_required", written:false, preview:{...}, hint:"..."}
```

硬性规则：

1. 看到 `confirmation_required` 时，把 `preview` 的要点讲给用户：动哪个条目、旧状态、
   新条目或归档原因、影响范围。**不要**只贴 JSON。
2. **只有用户明确同意后**，才以 `confirm=true` 重试；用户没表态就停在这里。
3. **绝不**在用户不知情时直接传 `confirm=true`；也绝不把预览当成功。
4. 两者**永不删除文件**：supersede 双向标注（旧 `superseded_by` / 新 `supersedes`）；
   archive 置 `status: archived` + `archive_reason`。
5. 一次操作一个 git commit，只含本次触及的文件。

## 冲突裁决

读到对同一事实的**矛盾条目**时，按是否有替代链接分两种处理：

1. **有 `supersede` 链**（新条目 frontmatter 有 `supersedes`，旧条目 `status: superseded`）
   → **取新弃旧**：整段链路里采信最新的一条，旧的直接忽略/不提。检索时用
   `exclude_retired=True` 就把旧条目挡在结果外；要确认链关系用 `memory_get` 看两侧
   `supersedes` / `superseded_by`。
2. **无 `supersede` 链接的矛盾条目**（两条都 `current`，却互相打架）
   → **呈现两者、请人裁决**：把两个 `id` 与各自结论一并摆给用户，说明冲突点，
   **不要自动择一、不要静默挑一个回答**。用户裁决后再用 `memory_supersede`
   （**需用户明确同意**，见上节）把错误的一条退役。

> 判据是**有没有替代链接**，不是新旧/分数高低——没链接就是未裁决的冲突，必须交人。

## 边界

- `writable=false` 的只读语料不可写；写入一律走 add/supersede/archive。
- **多消费者共享**（#41 / #44 / ADR-0025 **D19 修订 D17**）：第二个 agent 软件
  （如 DeepTutor）以 MCP `streamableHttp` 连**同一个共享 daemon**（同一基表 + 派生索引），
  并且**可读可写**——全局知识库是**所有 agent 可读可写的共享域**：读 = 整张基表，
  写 = 全局 KB（`add` / `supersede` / `archive`，经本产品写入网关 + daemon `WRITE_LOCK`
  串行化；写调用在 daemon 审计里带 agent 身份，可归属）。索引维护 / 收录 DDL
  （`reindex`、`ingest_include` / `ingest_exclude`）不开放给第二消费者。
  接入命令：`python -m memory_agent.connect`（见 `memory_agent/README.md`）。
- 与 `agent-kb` skill 的分工：`agent-kb` 讲 KB 的组织约定与 `kb.py` CLI；有 MCP 工具时
  **优先用本 skill 的工具**，CLI 只作兜底。
- 不存密钥/token/账号；条目单主题、控制在 ~150 行内。

## Related

- 全局 KB 约定：`C:\Users\Tan\.config\opencode\knowledge\AGENTS.md`（`agent-kb` skill 的落点）。
- 设计：`docs/adr/0008`（读路径）、`0009`（写入网关）、`0010`（生命周期工具）、
  `0011`（索引一致性：代目录 + 指针切换 + 增量刷新）、`0012`（本 skill 的位置与确认规则归属）、
  `0025` D19（读 = 整张基表 / 写 = 全局知识库 / 冲突裁决 + `exclude_retired`）、
  `0026`（迭代检索：测量协议 + 本节的规则来源）。
- 「迭代检索」节的经验依据：`experiments/agentic-rag-census/phase_b/`（#47 头寸 / #48 三臂）。
