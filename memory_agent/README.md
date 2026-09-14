# memory_agent — 记忆能力包

Agent-Knowledge-Base 的第一个能力：让 coding agent 通过 MCP 语义检索、读取、并**安全写入**长期记忆。

- 产品形状与架构：`docs/adr/0005`（产品升级）、`docs/adr/0006`（Markdown 真相源 + 派生索引 + 写入网关）、`docs/adr/0007`（布局与更名）、`docs/adr/0008`（读路径接缝：MCP 选型 / 语料范围 / stdio 铁律）。
- 需求全貌（user stories / 决策 / 测试口径）：GitHub issue #7；实现票据 #10–#17。

## 约定（不变量）

- **真相源是 Markdown**（条目 + frontmatter），向量索引是派生、可丢弃的。
- **只暴露 MCP 工具，不暴露裸文件写**；破坏性写（supersede / archive）需确认，永不原地改写或删除。
- 传输：**stdio**；引擎**进程内**调用 `ragcore`（模型每进程一份内存）；索引走**独立** Qdrant 路径，且 client 按操作开/关——可与 `legal_web` 并存（见 ADR-0008 D5）。
- **stdout 是协议通道**：日志必须走 stderr。`memory_agent` 在 import ragcore 之前抢配 root logger（见 `_bootstrap.py`）。

## 现状（#10 读路径最小闭环）

```
memory_agent/
├── mcp_server.py     # stdio MCP：memory_search / memory_get            (#10)
├── runtime.py        # 先立 stderr 日志，再装配索引单例
├── _bootstrap.py     # stdio 安全日志 + ragcore sys.path 垫片
├── settings.py       # 真相源 / 索引路径 / 集合名（勿命名 config.py，会遮蔽 ragcore 的 config 包）
├── corpus/loader.py  # KB 条目 + 只读语料 的发现与噪声排除              (#10)
├── memory/entries.py # frontmatter 解析 -> Entry
├── memory/index.py   # 条目级派生索引（Qdrant）+ manifest                (#10)
├── build_index.py    # CLI：从 Markdown 全量重建                        (#10)
├── eval/             # 检索与写路径证据：baseline_A.md、BEIR nDCG@10、写路径套件 (#9,#15,#16)
└── (skill)           # 见 #14：指导 agent 何时 search/read/add 及破坏性确认规则
```

写入网关（`memory_add` / `memory_supersede` / `memory_archive`）与索引增量一致性（hash 跳过 / 孤儿清理）分别是 #11/#12 与 #13。

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

工具：
- `memory_search(query, k=5, writable_only=False)` → 条目级命中（`id/title/source/writable/type/tags/status/score/snippet`）；`score` 为余弦相似度（越大越相关）。
- `memory_get(entry_id)` → 该条目的**真实 Markdown** 内容 + 元数据。

导入 `ragcore` 的方式与 `legal_web` 一致：启动时把 `ragcore/` 加入 `sys.path`，沿用 `services/`、`config/`、`strategies/` 原包名。
