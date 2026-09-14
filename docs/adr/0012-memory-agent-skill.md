# 0012 memory-agent skill（#14）：位置、确认规则归属与边界

Status: accepted

`memory-agent` skill 把 MCP 工具（`memory_search` / `memory_get` / `memory_add` /
`memory_supersede` / `memory_archive`）的使用规则交给 agent。源文件
`memory_agent/skill/SKILL.md` 随包发布，安装到
`C:\Users\Tan\.config\opencode\skills\memory-agent\SKILL.md`。三条难逆/易漂的决策如下。

## 决策

**D1 位置 = 仓库内为源 + 安装到全局。**
源在仓库（可版本化、随包发布、可评审）；全局 skills 目录是**派生副本**，opencode 从这里加载。
理由：skill 必须能被 opencode 发现（#17 dogfood 的前提），又必须进版本控制；副本不在版本控制内，
故以仓库为单一事实源——改 skill 先改仓库、再同步安装（复制文件）。

**D2 确认规则由 skill 承载，协议层不承载。**
`memory_supersede` / `memory_archive` 默认 `confirm=false`，只返回 `confirmation_required`
预览；skill 硬性要求 agent 把 preview 讲给用户、拿到明确同意后才以 `confirm=true` 重试。
理由：opencode 客户端未声明 MCP elicitation（ADR-0010 D1），协议层"问不了人"，所以"问不问"
的责任落在 skill 约定。**代价（已知）**：真正的验收不在 #14 自身，而在 **#17 dogfood**——
观察 agent 有没有不问人就传 `confirm=true`。反证条件：观察到不问就写 → 升级到两阶段
（草稿/预览态）或等 opencode 开放 elicitation。

**D3 skill 只管"怎么用工具"，不复述领域约定。**
skill 覆盖读/写/生命周期三类调用、字段域（domain↔type↔tags）、索引新鲜度警告（写入后不刷新
索引属 #13）、以及破坏性确认规则；KB 的组织约定仍以 `knowledge/AGENTS.md` + `agent-kb`
skill 为准，skill 里只放指针。理由：避免同一约定两处维护而漂移；有 MCP 工具时优先走工具，
CLI（`kb.py`）退为兜底。

## Consequences

- 全局副本与仓库源可能漂移：改 skill 后需重新安装（复制文件）；仓库源是 canonical。
- #13 落地 `memory_reindex` / `memory_index_status` 后，skill 对应小节从"未暴露时的兜底"
  转为常规路径。
- #14 自证只能到"skill 存在且与冻结契约一致"；行为正确性由 #17 dogfood 验收。
