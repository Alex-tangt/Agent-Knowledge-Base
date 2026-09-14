# 0009 写入网关（#11）：commit 归属范围 + 去重命中语义

Status: accepted

`memory_add` 落地时确定的两条难逆/反直觉决策。实现见 `memory_agent/memory/writer.py`
（校验/渲染在 `authoring.py`），单测 `tests/unit/test_memory_writer.py`。

## 决策

**D1 一次写入的 commit 只拥有「本次新写入的条目文件」（路径级提交）。**
用 `git add -- <entry.md>` + `git commit -- <entry.md>`，生成物（`_index.md` / `tags.md`）
与其它未提交改动留在工作树。

理由：KB 工作树长期是脏的——另一会话在并发编辑（#13 约束 3 已实测）。若提交整棵树，
每笔 `memory_add` 会静默夹带别人的在制品；`_index.md` 又是由**所有**条目生成的聚合文件，
提交它就无法只保留「我们那一行」。而生成索引是派生物、可随时 `kb.py index` 重建
（ADR-0006），把它排除在 commit 之外不损失任何可恢复性。

> 代价（已知）：committed 状态里索引可能暂时滞后；新 tag 对 `tags.md` 的补充也不会进
> commit。二者都由「索引/标签是派生+可重建」这一前提兜住。

考虑过的替代：
- **提交条目 + 重生成的 `_index.md` / `tags.md`** —— committed 状态自洽，但必然夹带
  并发会话未提交的条目（无法只提交一行）。弃。
- **脏树即拒写（fail-closed）** —— 每笔 commit 完全可归属，但 KB 长期脏 → 工具经常不可用，
  且逼 agent 先去提交/stash 别人的在制品，风险更大。弃。

**D2 去重命中「只报告、不写」：`memory_add` 保持非破坏性。**
写入前用派生索引检索（`writable_only=True`，过滤下推到向量库），命中相似度 ≥ 阈值即返回
候选 id/score 并不落盘；替换走上层确认的生命周期工具 `memory_supersede`（#12）。
误报的出口是显式 `allow_duplicate=true`（调用方承担判断）。

理由：PRD 把破坏性/变更语义（supersede、archive）交给需确认的工具；`memory_add` 是「auto」
的非破坏路径。若 add 直接改写已有条目，就在没有确认语义的情况下重新引入了原地编辑。

**D3 去重阈值 `DEDUP_THRESHOLD=0.92` 是暂定 knob。**
取值偏保守（宁漏报、不误报——误报会白挡一次合法写入）。真实 KB 上的校准留给 #16。

## Consequences

- 校验规则**镜像**全局 KB 的 `tools/kb.py check`（单一事实源在 KB 侧），写入时还会在 KB 里
  存在 `tools/kb.py` 时跑一次真实 `check` 作为外部闸门（只对本文件相关的 ERROR 负责）。
  额外强制 `domain ↔ type` 对应（topics↔topic、decisions↔decision/research、
  projects/*↔project-knowledge），这是 KB 目录约定、kb.py 本身不查。
- **已知债务**：写入后不刷新派生索引（增量 reindex / hash 跳过属 #13），所以新条目在被
  重建前对 `memory_search` 与后续去重检索不可见。
- 任何路径都不覆盖/删除/原地编辑已有记忆；写出后若 `kb.py check` 对本文件报错，会删掉刚写的
  文件并放弃提交（不留半成品）。
