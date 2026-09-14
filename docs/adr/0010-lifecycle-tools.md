# 0010 生命周期工具（#12）：supersede / archive 的确认与标注

Status: accepted

`memory_supersede` / `memory_archive` 落地时确定的三条难逆/反直觉决策。实现见
`memory_agent/memory/writer.py`（frontmatter 就地改写助手在 `authoring.py`），
单测 `tests/unit/test_memory_writer.py`。

## 决策

**D1 确认机制 = 显式 `confirm` 参数 + skill 约定（C 方案），不用 MCP elicitation。**
`confirm=False`（默认）只做完整校验并返回 `{status:"confirmation_required", preview}`，
不落盘；调用方把 `preview` 给用户看、得到明确同意后，再以 `confirm=True` 重试。

理由：opencode 客户端当前**没有声明 MCP elicitation 能力**（`CLIENT_OPTIONS.capabilities`
里该项被注释，挂着 issue #23066），所以服务端 `ctx.elicit` 发不出去（详见 ADR-0009 同轮
调研，结论已记入全局 KB）。C 方案把"问不问人"的责任放在 skill 约定，而不是协议层。

> 代价（已知）：C 的真实检验不在 #12 的单测（单测只能验"默认不写"），而在 **#17 dogfood**——
> agent 有没有不问人就传 `confirm=true`。反证条件：dogfood 观察到不问就写 → 升级到
> 两阶段（预览/草稿）或等 opencode 开放 elicitation。

**D2 supersede 的标注 = 双向 + 就地改 frontmatter 行，一次 commit 两个文件。**
新条目 frontmatter 加 `supersedes: <old_id>`；旧条目加 `superseded_by: <new_id>`、
`status: superseded`、`updated: <today>`。两处都用 `authoring.update_frontmatter_fields`
**逐行**改写（已有的换值、没有的插到块末）：正文、未知字段、字段顺序、行尾风格原样保留。

理由：(a) 「双向指认」让"旧条目被谁取代"和"新条目取代了谁"都可从真相源直接读出，不依赖
外部映射；(b) 生命周期工具**不得原地编辑记忆内容**，只改元数据，逐行改写是最小侵入面——
避免 YAML round-trip 丢字段/重排。commit 范围仍是 ADR-0009 的路径级提交：`git add --`
两个文件 + `git commit -- <new> <old>`，生成物与别人的在制品不进来。

**D3 archive 只标记，`reason` 落 frontmatter 的 `archive_reason` 字段。**
置 `status: archived` + `archive_reason: <reason>` + `updated: <today>`；`reason` 必填。
任何路径都不删除文件（`os.remove` 只出现在 kb 校验失败时回滚**刚写的新文件**）。

理由：归档是"不再相关、但保留历史"，正文照旧；`archive_reason` 与 `superseded_by` 同层，
机器可查、`kb.py check` 不报错（它只校验必需键与取值域，未知键放行）。

## 附带约束

- **可替代状态**：旧条目必须是 `current` / `draft` 才能被 supersede；已 `archived` 的条目
  再 archive 会被拒（幂等守卫）。只读语料（`writable=false`）一律拒写。
- **目标路径冲突**：supersede 的新条目若与已存在文件同路径，直接报错，不覆盖。
- **失败要响、不留半成品**：任一文件过不了 `kb.py check` 时，回滚本次全部改动
  （新文件删除、旧文件恢复原文），且不产生 commit。
- **返回形状**（供 #14 skill 与调用方消费）：
  - 预览：`{status:"confirmation_required", written:false, action, preview, hint}`
  - supersede 成功：`{status:"written", written:true, action:"supersede", old_id, new_id, id, path, commit, preview, warnings}`
  - archive 成功：`{status:"written", written:true, action:"archive", id, path, commit, preview}`

## Consequences

- **已知债务**：写入后不刷新派生索引（增量 reindex 属 #13）。supersede 后旧条目在索引里
  仍是 `current`、新条目不可见，直到重建；`memory_search` 会暂时返回退役条目。
- **#14 skill 必须写进**：两个工具默认只预览；只有用户明确同意后才 `confirm=true`；
  `archive_reason` 落在 frontmatter。（D1 的验收在 #17，不在 #12。）
