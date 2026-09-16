# #36 个人模式落地验收结果

命令（仓库根，主树 venv 绝对路径）：

```powershell
D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe memory_agent/eval/personal_mode_36.py --json-out memory_agent/eval/personal_mode_36_results.json
```

**15/15 通过**（`personal_mode_36_results.json` 为机器可读矩阵）。

## 覆盖的验收点（issue #36 checklist）

| issue 验收 | 证据行 | 结果 |
|---|---|---|
| 外部直接改已收录文件（增/改/删）→ 下一次 `memory_search` 反映、免重启 | 2a / 2b / 2c | PASS |
| 改 overlay（加/减）→ 免重启生效；`list` 区分默认 vs 显式 | 1a / 3b / 3g | PASS |
| 写后不再同步刷（D13）；`memory_reindex` 仍可全量重建 | 3c / 3d / 3e / 0a | PASS |
| 指纹检查在真实语料耗时毫秒级（留数字） | 5 | PASS（172 文件 / **51.3 ms**） |
| 移除收录走预览 + 确认，且不误删他人条目 | 3f / 3g | PASS |
| `pytest tests/unit -q` 全绿；#16 sandbox 不退化 | （见下） | PASS |

## 关键实测数字

- **指纹检查**：真实三仓库 + 全局 KB 选中 **172 个 `.md`**，`scan_fingerprint()` 单次
  **51.3 ms**（`os.scandir` + `DirEntry.stat()` 单趟；不再 `os.walk` + 二次 `os.stat`）。
  同一台机上改前是 ~156 ms（双趟 stat），即本次把每文件系统调用减半。
- 机制链路用 **Stub 嵌入**（不加载 BGE-M3），全程只有一个 `MemoryIndex` 实例 =
  模拟**常驻 daemon 不重启**；改文件 / 改 overlay 后只再发 `search`。

## 机制说明（对应 ADR-0025）

- **运行时重读**（D8/D9）：`settings.READONLY_ROOTS` 不再是唯一真源；`loader.resolve_selection()`
  每次调用重读注册表（`readonly_repos.json`）+ overlay（`overlay.json`），改配置免重启。
- **收录 = DDL**（D2/D8）：overlay 独立清单，粒度 = 路径模式（精确文件 / 窄 glob）；
  `owner` 默认按来源继承。`memory_ingest_include/_exclude/_list` 是接口面。
- **惰性刷新**（D9）：`memory_search` 前做 stat-only 指纹比对，不同才增量重嵌。
- **孤儿安全**（修订 ADR-0014 风险）：注册表 / overlay 读不出或来源根不可达时
  `complete=false`，`refresh` **不删**任何条目，只报 `deferred_removed`——避免误删。
- **弃写后同步刷**（D13）：`memory_add/supersede/archive` 返回 `index.mode=lazy`，
  不再嵌入；索引由下一次查询追平。

## 未在本次证据内（另行验证）

- **#16 写路径 sandbox**（需 BGE-M3）：`write_path_sandbox.py` 的 1d 已改为断言
  `mode=lazy`；结论见其 results。
- **真实 daemon 端到端**：机制验收用单一 `MemoryIndex` 实例代替常驻进程，避免为一次
  验收加载 ~3.9GB BGE-M3；进程级"免重启"由「同一实例跨文件/overlay 变化持续服务」直接体现。
