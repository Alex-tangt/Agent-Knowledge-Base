---
name: memory-agent
description: 通过 memory-agent MCP 工具读写 agent 长期记忆——检索过往知识、读回条目、写入新记忆，以及替代/归档旧条目。当用户要求"记住/存入某事实"、要查平台/版本/错误/项目等跨会话知识、或写入前需要判重时使用。破坏性操作（supersede/archive）必须先拿到用户明确同意。
---

# memory-agent — agent 长期记忆

`memory-agent` MCP 服务把长期记忆暴露为工具。**Markdown 是真相源**，向量索引是派生、可重建的。
记忆分两类：**可写 KB**（`writable=true`，可 add/supersede/archive）与**只读语料**
（`writable=false`，如项目仓库文档，只能检索、不可写）。

> 两条纪律：**回答先检索；破坏性写先问人。**

## 工具

| 工具 | 用途 | 破坏性 |
|---|---|---|
| `memory_search(query, k=5, writable_only=False)` | 语义检索条目 | 否 |
| `memory_get(entry_id)` | 按 id 读回条目真实 Markdown | 否 |
| `memory_add(title, body, domain, type, tags, ...)` | 写入新条目（写前去重） | 否（不覆盖） |
| `memory_supersede(old_id, ..., confirm=False)` | 新条目替代旧条目 | **是** |
| `memory_archive(entry_id, reason, confirm=False)` | 归档条目（保留文件） | **是** |

索引维护（#13，客户端暴露时可用）：`memory_reindex(cursor=None, batch=16)` 分块重建（返回
`{done,total,processed,cursor,gen}`，拿 `cursor` 续调到 `done=true`）；`memory_index_status()`
查当前代/条数/是否自洽。

## 读：回答前先查

1. `memory_search` 用自然语言查询；命中字段为
   `id / title / source / writable / type / tags / status / score / snippet`，`score` 越大越相关。
2. 要看原文用 `memory_get(id)`；`source` 指向的文件/URL 才是权威来源，关键结论跟过去核对。
3. 引用时给出 `id`。本仓库内的事实优先于记忆；冲突则修正记忆（见 KB 约定）。
4. **不要凭记忆回答平台/版本/错误/实测数字**——先 `memory_search`；没有就直说没查到。
5. 索引未构建时报错会附上命令：`venv\Scripts\python.exe memory_agent/build_index.py`。

## 写：先搜 - 判重 - 再落

1. **先搜**：用拟写内容的关键词 `memory_search(..., writable_only=True)`。
2. `memory_add` 命中近似会返回 `{status:"duplicate", candidates:[...]}` 且**不写**：
   - 同一事实的更新 → 用 `memory_supersede` 取代旧条目；
   - 确认确实不同 → 以 `allow_duplicate=true` 重试。
3. 结构化字段（路径由工具决定，**没有裸文件写工具**）：
   - `domain`：`topics` | `decisions` | `projects/<slug>`
   - `type`（须与 domain 匹配）：`topics`→`topic`；`decisions`→`decision`/`research`；
     `projects/*`→`project-knowledge`
   - `tags`：从 KB 的 `tags.md` 受控表取，不要自造
   - `slug`：英文 slug；纯中文标题请显式给
4. 成功返回 `{status:"written", id, path, commit, warnings}`；`warnings` 要转述给用户。
5. **索引不会自动刷新**（#13 之前）：刚写入的条目在重建前 `memory_search` 搜不到、`memory_get`
   也读不到。写完后要点出 `id`；需要立刻可检索就调 `memory_reindex`（未暴露时改用
   `build_index.py`，需停服务）。

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

## 边界

- `writable=false` 的只读语料不可写；写入一律走 add/supersede/archive。
- 与 `agent-kb` skill 的分工：`agent-kb` 讲 KB 的组织约定与 `kb.py` CLI；有 MCP 工具时
  **优先用本 skill 的工具**，CLI 只作兜底。
- 不存密钥/token/账号；条目单主题、控制在 ~150 行内。

## Related

- 全局 KB 约定：`C:\Users\Tan\.config\opencode\knowledge\AGENTS.md`（`agent-kb` skill 的落点）。
- 设计：`docs/adr/0008`（读路径）、`0009`（写入网关）、`0010`（生命周期工具）、
  `0012`（本 skill 的位置与确认规则归属）。
