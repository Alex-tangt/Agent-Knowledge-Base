# 0011 索引一致性（#13）：代目录 + 指针切换、增量刷新、失败要响

Status: accepted

索引一致性落地时确定的三条难逆/反直觉决策。实现见 `memory_agent/memory/layout.py`
（代目录 + 指针）、`memory_agent/memory/index.py`（当前代视图 + 增量 `refresh`）、
`memory_agent/memory/reindex.py`（分块全量重建），单测 `tests/unit/test_memory_reindex.py`
与 `test_memory_index.py`/`test_memory_index_qdrant.py`。

## 决策

**D1 全量重建 = 代目录 + 原子切换指针（D2，见 issue 冻结项）。**
布局 `INDEX_DIR/<gen>/{qdrant/,manifest.json}` + 指针文件 `INDEX_DIR/CURRENT`（内容为代名）。
新代在**独立目录**里完整建好，最后 `os.replace(指针)` 原子切换。搜到只认指针。

理由：(a) Qdrant local mode 的锁按**目录**持有（ADR-0008 D5），不同代 = 不同锁，重建期间
旧代照常服务、搜索不被打断；(b) 旧实现「清空集合 → 逐条嵌入 → 写 manifest」不是原子的，
进程崩在中途会留下 **0 点 + 陈旧 manifest**：`memory_get` 照常（读文件）、`memory_search`
静默返回空（实测：`c10.dll` 访问违例 `0xc0000005`）。代目录把「正在构建的」与「在服务的」
物理隔离，中断只留一个未接管的代目录。保留最近 2 代（`prune(keep=2)`），给可能仍在旧代上
检索的进程留缓冲。

**D2 失败要响：完成前核对「manifest 条数 == 集合点数」，不等则报错且不切指针。**
`search` 同样在检索前做该核对（`_ensure_consistent`），不一致直接抛 `IndexConsistencyError`，
**绝不静默返回空**。空语料重建直接拒绝（否则会切出一个空索引）。

理由：`memory_get` 读文件、`memory_search` 读索引，两者以真相源为准但**失败模式不同**；
「表面正常、实际搜不到」正是最难发现的不自洽。宁可显式报错并保留旧代。

**D3 增量刷新按条目：hash 未变跳过、变更重嵌、消失的条目删点。**
写入成功后 `MemoryIndex.refresh()`（writer 的写后钩子）按 `entries.content_hash` 与 manifest
比对：只有新增/变更条目走嵌入，孤儿点按**稳定点 id**（`uuid5(entry_id)`，见 `entries.point_id_for`）
删除。全量重建同样用稳定点 id，重复写入即覆盖。

理由：条目级索引（CONTEXT）要求以条目为增量单位；点 id 若随机，重建会写出重复点，
数量核对与孤儿清理都会失真。刷新失败**不回滚已提交的写入**（真相源已落盘），只在返回值的
`index` 字段如实报 `ok:false`——派生索引下次刷新即可追平。

## 接口（供 skill / 后续会话对齐）

- `memory_reindex(cursor=None, batch=16) -> {done,total,processed,skipped,cursor,gen}`：
  分块全量重建；`cursor=None` 开新一轮，拿 `cursor` 续调到 `done=true`（此时指针已切换）。
  分块是为了迁就 MCP 客户端几十秒的调用超时（D3），零后台线程、零 job 表。
- `memory_index_status() -> {built,gen,entries,points,consistent,built_at,path}`。
- `MemoryIndex.refresh(entries=None) -> {entries,added,updated,skipped,removed,embedded}`。

## Consequences

- **不再有「写入后不刷新」的债务**：writer 的三个写路径（add/supersede/archive）提交后自动
  增量刷新；`memory_search` 立即可见。（ADR-0009 / 0010 的对应债务解除。）
- `build_index.py` 现在建新代并原子切指针；旧布局（`INDEX_DIR/qdrant` + `manifest.json`）不再
  被读取——首次用新代码需重建一次（已完成：gen-1，68 条 = 68 点）。
- 刷新是**同步**的：首次真正检索/写入仍要现加载 BGE-M3（~30–40s，见 #19 惰性加载），
  可能贴近 MCP 调用超时；需要低延迟可临时 `MEMORY_WARMUP=1`。
- 旧代目录会保留最多 2 代；更旧的 best-effort 删除。
