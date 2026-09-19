# 个人模式验收用例（人的小抽查）

> 基座由 `seed/` 提供（setup.sh 铺到隔离环境）。逐条做，把「实测」列填上。
> 判据看两处：**产物**（返回/落盘状态）+ **命中**（`memory_search` 命中的 id，可见于 opencode 的工具结果）。
> 说明里给的是**建议问法**；只要触发到目标工具、判据满足即可。

| # | 你问（自然语言） | 应触发 | 期望 / 判据 | 实测 | 结论 |
|---|---|---|---|---|---|
| A | 演示项目的代号是什么？负责人是谁？用 memory_search。 | `memory_search` | 命中 `projects/blue-whale/overview`（`writable=true`），答「蓝鲸 / Alice」 | | |
| B | 外部域单次报销上限是多少？用 memory_search。 | `memory_search` | 命中只读条目（`writable=false`，source 以 `corpus/` 开头），答「800 元」 | | |
| C | 记住一条：演示项目新增成员 Carol，角色是测试。用 memory_add，section=projects/blue-whale，tags 从 tags.md 取（project-index）。 | `memory_add` | `status=written`（返回含 commit）；若 `duplicate` 则确认后 `allow_duplicate=true` 重试 | | |
| D | **新开会话**：演示项目里 Carol 的角色是什么？用 memory_search。 | `memory_search` | 命中 C 写入的新条目，答「测试」 | | |
| E | 用 memory_supersede 把 `projects/blue-whale/overview` 替代为「负责人 Bob」，section=projects/blue-whale，slug=owner-bob，type=project-knowledge。 | `memory_supersede` | **先** `confirmation_required` 预览且**不落盘**；回复同意后重试 → 新条 `supersedes=projects/blue-whale/overview`，旧条 `status=superseded`（文件仍在） | | |
| F | 演示项目负责人是谁？用 memory_search 且 **exclude_retired=true**。 | `memory_search` | 只出现新条（Bob），**不出现** Alice 旧条 | | |
| G | 调 memory_index_status 看看索引。 | `memory_index_status` | `built=true`、`consistent=true`、`entries==points` | | |
| H（可选，需解析环境） | 上传一个 PDF/DOCX 并收录，然后召回其内容。 | `ingest_*` | 物化 `.md` → overlay 收录 → 下一次 `memory_search` 召回（免重启） | | |

## 判据之外的观察（记一笔）
- 启动时 opencode 是否显示 `memory-agent` 已连接（`opencode mcp list` → connected）。
- 写/替代后索引为 `mode=lazy`（写入不刷索引，下次 `memory_search` 追平）——正常，不是失败。
- 任一工具报 `memory-agent daemon 不可用` → 多半是端口串台（见 runbook 的「故障」节）。
