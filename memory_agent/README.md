# memory_agent — 记忆能力包

Agent-Knowledge-Base 的第一个能力：让 coding agent 通过 MCP 语义检索、读取、并**安全写入**长期记忆。

- 产品形状与架构：`docs/adr/0005`（产品升级）、`docs/adr/0006`（Markdown 真相源 + 派生索引 + 写入网关）、`docs/adr/0007`（布局与更名）、`docs/adr/0008`（读路径接缝：MCP 选型 / 语料范围 / stdio 铁律）、`docs/adr/0009`（写路径：commit 归属范围 + 去重命中语义）。
- 需求全貌（user stories / 决策 / 测试口径）：GitHub issue #7；实现票据 #10–#17。

## 约定（不变量）

- **真相源是 Markdown**（条目 + frontmatter），向量索引是派生、可丢弃的。
- **只暴露 MCP 工具，不暴露裸文件写**；破坏性写（supersede / archive）需确认，永不原地改写或删除。
- 传输：**stdio**；引擎**进程内**调用 `ragcore`（模型每进程一份内存）；索引走**独立** Qdrant 路径，且 client 按操作开/关——可与 `legal_web` 并存（见 ADR-0008 D5）。
- **stdout 是协议通道**：日志必须走 stderr。`memory_agent` 在 import ragcore 之前抢配 root logger（见 `_bootstrap.py`）。

## 现状（#10 读路径 + #11 写入网关 + #12 生命周期 + #13 索引一致性）

```
memory_agent/
├── mcp_server.py          # stdio MCP：search/get/add/supersede/archive/reindex/status
├── runtime.py             # 先立 stderr 日志，再装配索引 / 写入网关单例
├── _bootstrap.py          # stdio 安全日志 + ragcore sys.path 垫片
├── settings.py            # 真相源 / 索引根 / 指针名 / 集合名 / 去重阈值（勿命名 config.py，会遮蔽 ragcore 的 config 包）
├── corpus/loader.py       # KB 条目 + 只读语料 的发现与噪声排除              (#10)
├── memory/entries.py      # frontmatter 解析 -> Entry；稳定点 id（uuid5）    (#10,#13)
├── memory/index.py        # 当前代视图：检索 + 增量 refresh（hash 跳过/孤儿清理）(#10,#13)
├── memory/layout.py       # 代目录 + CURRENT 指针 + 原子切换                 (#13)
├── memory/reindex.py      # 分块全量重建（新代建好再切指针 + 自洽核对）       (#13)
├── memory/errors.py       # IndexNotBuiltError / IndexConsistencyError       (#13)
├── memory/authoring.py    # 渲染 frontmatter + 镜像 kb.py check 的校验        (#11)
├── memory/writer.py       # 写入网关：搜索→去重→校验→落盘→commit→增量刷新    (#11,#12,#13)
├── build_index.py         # CLI：从 Markdown 全量重建（新代 + 切指针）        (#10,#13)
├── eval/                  # 运行时证据：baseline_A.md（锚点）、write_path_sandbox.py + _results.md（#16）、BEIR (#15)
└── (skill)                # 见 #14：指导 agent 何时 search/read/add 及破坏性确认规则
```

写入后自动**增量刷新**索引（只重嵌受影响条目）；刚写入的条目立即可被 `memory_search` 搜到。
索引不自洽（manifest 条数 ≠ 集合点数）时检索会**显式报错**，用 `memory_reindex` 分块全量重建。

## 用法

```powershell
# 1) 建索引（会加载 BGE-M3，CPU 上 ~6 min/60 条）
venv\Scripts\python.exe memory_agent/build_index.py

# 2) 手动冒烟（任意 MCP 客户端同理）
#    opencode 接入（~/.config/opencode/opencode.json）：
#    "memory-agent": {
#      "type": "local",
#      "command": ["<repo>/venv/Scripts/python.exe", "<repo>/memory_agent/mcp_server.py"],
#      "enabled": true
#    }
```

写路径确定性 sandbox 套件（#16，需 BGE-M3；真实 KB 只读、不污染）：

```powershell
venv\Scripts\python.exe memory_agent/eval/write_path_sandbox.py
```

工具：
- `memory_search(query, k=5, writable_only=False)` → 条目级命中（`id/title/source/writable/type/tags/status/score/snippet`）；`score` 为余弦相似度（越大越相关）。
- `memory_get(entry_id)` → 该条目的**真实 Markdown** 内容 + 元数据。
- `memory_add(title, body, domain, type, tags, slug, sources, status, allow_duplicate)` → 唯一写入口；写前去重，命中近似条目则**不写**并返回候选（`allow_duplicate=true` 是误报出口）；写入 = frontmatter 过校验 + 一个**只含本条目文件**的 git commit + 增量刷新索引。
- `memory_supersede(old_id, ..., confirm=False)` / `memory_archive(entry_id, reason, confirm=False)` → 破坏性；默认只预览，`confirm=true` 才落盘（见 `docs/adr/0010`）。
- `memory_reindex(cursor=None, batch=16)` → 分块全量重建（`cursor=None` 开始，拿 `cursor` 续调到 `done=true`，此时指针已切）。
- `memory_index_status()` → `{built,gen,entries,points,consistent,built_at,path}`。

导入 `ragcore` 的方式与 `legal_web` 一致：启动时把 `ragcore/` 加入 `sys.path`，沿用 `services/`、`config/`、`strategies/` 原包名。
