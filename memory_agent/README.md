# memory_agent — 记忆能力包

Agent-Knowledge-Base 的第一个能力：让 coding agent 通过 MCP 语义检索、读取、并**安全写入**长期记忆。

- 产品形状与架构：`docs/adr/0005`（产品升级）、`docs/adr/0006`（Markdown 真相源 + 派生索引 + 写入网关）、`docs/adr/0007`（布局与更名）、`docs/adr/0008`（读路径接缝：MCP 选型 / 语料范围 / stdio 铁律）、`docs/adr/0009`（写路径：commit 归属范围 + 去重命中语义）、`docs/adr/0011`（索引一致性：代目录 + 指针切换）、`docs/adr/0013`（拓扑：共享单实例 daemon + 代理）、`docs/adr/0014`（只读语料：带标签的多仓库文档）、`docs/adr/0024`（独立包化：`ragcore` 真包 + 本包可安装）。
- 需求全貌（user stories / 决策 / 测试口径）：GitHub issue #7；实现票据 #10–#17。

## 约定（不变量）

- **真相源是 Markdown**（条目 + frontmatter），向量索引是派生、可丢弃的。
- **只暴露 MCP 工具，不暴露裸文件写**；破坏性写（supersede / archive）需确认，永不原地改写或删除。
- 传输（#19 / ADR-0013）：**一个常驻 HTTP daemon 持有唯一一份引擎，每会话一个 stdio 代理转发**（N 会话共享，内存 `1×3.9GB` 而非 `N×`）；另保留纯 stdio 模式供单会话/手动。索引走**独立** Qdrant 路径，client 按操作开/关——可与 `legal_web` 并存（见 ADR-0008 D5）；单 daemon 的进程内串行化见 ADR-0013 D3。
- **stdout 是协议通道**：日志必须走 stderr。`memory_agent` 在 import ragcore 之前抢配 root logger（见 `_bootstrap.py`）。

## 现状（#10 读路径 + #11 写入网关 + #12 生命周期 + #13 索引一致性 + #19 共享单实例）

```
memory_agent/
├── mcp_server.py          # MCP 服务：默认 stdio；--transport http 起共享 daemon （+ 可选 /health）(#10-#13,#19)
├── proxy.py               # 每会话瘦代理：幂等确保 daemon 在跑 + stdio<->HTTP 转发          (#19)
├── runtime.py             # 先立 stderr 日志，再装配索引 / 写入网关单例
├── _bootstrap.py          # stdio 安全日志（import ragcore 前抢配 root logger 到 stderr）
├── settings.py            # 真相源 / 索引根 / 只读仓库清单 / 指针名 / 集合名 / 去重阈值 / daemon 端点
├── readonly_repos.json    # 本地只读仓库清单（gitignored；模板见 .example.json）(#17)
├── corpus/loader.py       # KB 条目 + 多仓库只读文档 的发现、标签消歧与噪声排除 (#10,#17)
├── memory/entries.py      # frontmatter 解析 -> Entry；稳定点 id（uuid5）    (#10,#13)
├── memory/index.py        # 当前代视图：检索 + 增量 refresh（hash 跳过/孤儿清理）(#10,#13)
├── memory/retrieval.py    # 检索接缝：策略召回（向量+关键词，走 ragcore）+ 可选 rerank   (#24)
├── memory/layout.py       # 代目录 + CURRENT 指针 + 原子切换                 (#13)
├── memory/reindex.py      # 分块全量重建（新代建好再切指针 + 自洽核对）       (#13)
├── memory/errors.py       # IndexNotBuiltError / IndexConsistencyError       (#13)
├── memory/locks.py        # 进程内 INDEX/WRITE 锁（单 daemon 并发安全）        (#19)
├── memory/authoring.py    # 渲染 frontmatter + 镜像 kb.py check 的校验        (#11)
├── memory/writer.py       # 写入网关：搜索→去重→校验→落盘→commit→增量刷新    (#11,#12,#13)
├── gateway/               # 检索网关：/mcp 边界 authn + 工具层 authz 强制注入 (#32)
│   ├── identity.py        #   身份形状 + token/进程配置解析（绝不回显凭证）
│   ├── authz.py           #   effective filter（白名单，只可收窄；tenant + ABAC）
│   ├── middleware.py      #   /mcp 边界中间件：解析身份 → 请求上下文 → 审计
│   ├── context.py         #   请求期身份 ContextVar（工具层读取）
│   └── audit.py           #   调用审计（JSONL；配额留钩子）
├── build_index.py         # CLI：从 Markdown 全量重建（新代 + 切指针）        (#10,#13)
├── eval/                  # 运行时证据：baseline_A.md（锚点）、issue19_acceptance.md（#19）、write_path_sandbox.py + _results.md（#16）、readonly_corpus_17.py（#17 三仓库只读）、dogfood_17.md、retrieval_eval.py + metrics.py + retrieval_eval_set.json + retrieval_baseline.md（#24 确定性检索评测）、mcp_install_smoke_26.py + _results.md（#26 安装 + MCP 集成冒烟）
├── pyproject.toml         # 本包（可安装，依赖 ragcore）                       (#26)
└── (skill)                # 见 #14：指导 agent 何时 search/read/add 及破坏性确认规则
```

写入后自动**增量刷新**索引（只重嵌受影响条目）；刚写入的条目立即可被 `memory_search` 搜到。
索引不自洽（manifest 条数 ≠ 集合点数）时检索会**显式报错**，用 `memory_reindex` 分块全量重建。

## 用法

```powershell
# 0) 安装两个包（editable；#26 / ADR-0024）
venv\Scripts\python.exe -m pip install -e ragcore -e memory_agent

# 1) 建索引（会加载 BGE-M3，CPU 上 ~6 min/60 条）
venv\Scripts\python.exe memory_agent/build_index.py

# 2) 启动共享 daemon（常驻单实例，持有唯一一份引擎；默认 eager 预热）
venv\Scripts\python.exe memory_agent/mcp_server.py --transport http
#    就绪探测：Get-Item/Invoke-RestMethod http://127.0.0.1:8765/health

# 3) opencode 接入（~/.config/opencode/opencode.json）——指向**代理**，每会话一个瘦进程：
#    "memory-agent": {
#      "type": "local",
#      "command": ["<repo>/venv/Scripts/python.exe", "<repo>/memory_agent/proxy.py"],
#      "enabled": true,
#      "timeout": 20000
#    }
#    代理会在会话启动时幂等确保 daemon 在跑；运维：proxy.py --status / --ensure / --stop
#    --stop 手动停掉 daemon（回收 ~3.9GB）；不做自动空闲卸载（见 ADR-0013 D2）
#    单会话/手动仍可直接跑 mcp_server.py（默认 stdio，不共享）。
```

只读仓库清单（用户故事 #17）：把要检索的**项目仓库文档**写进 `memory_agent/readonly_repos.json`
（gitignored；复制 `readonly_repos.example.json` 改）。只索引 `.md`，不索引代码；`label` 用于
跨仓库消歧义。**改完必须重启 daemon**（见 `docs/adr/0014`）。

```json
[
  {"label": "agent-knowledge-base", "path": "."},
  {"label": "agent-infra", "path": "D:/python_work/work2026-8/Agent-infra"}
]
```

写路径确定性 sandbox 套件（#16，需 BGE-M3；真实 KB 只读、不污染）：

```powershell
venv\Scripts\python.exe memory_agent/eval/write_path_sandbox.py
```

记忆检索确定性评测（#24，需 BGE-M3 [+ reranker]，不调 LLM）：`recall@k / nDCG@10 / MRR`，
目标链路 = 向量 + 关键词 + rerank。详见 `memory_agent/eval/README.md` 与基线
`memory_agent/eval/retrieval_baseline.md`。

```powershell
venv\Scripts\python.exe memory_agent/eval/retrieval_eval.py --mode hybrid-rerank
```

工具：
- `memory_search(query, k=5, writable_only=False)` → 条目级命中（`id/title/source/writable/type/tags/status/score/snippet`）；`score` 越大越相关（向量 + 关键词混合召回：关键词命中批次 >1，其余为余弦相似度）。
- `memory_get(entry_id)` → 该条目的**真实 Markdown** 内容 + 元数据。
- `memory_add(title, body, domain, type, tags, slug, sources, status, allow_duplicate)` → 唯一写入口；写前去重，命中近似条目则**不写**并返回候选（`allow_duplicate=true` 是误报出口）；写入 = frontmatter 过校验 + 一个**只含本条目文件**的 git commit + 增量刷新索引。
- `memory_supersede(old_id, ..., confirm=False)` / `memory_archive(entry_id, reason, confirm=False)` → 破坏性；默认只预览，`confirm=true` 才落盘（见 `docs/adr/0010`）。
- `memory_reindex(cursor=None, batch=16)` → 分块全量重建（`cursor=None` 开始，拿 `cursor` 续调到 `done=true`，此时指针已切）。
- `memory_index_status()` → `{built,gen,entries,points,consistent,built_at,path}`。

导入 `ragcore` 的方式与 `legal_web` 一致：**`ragcore` 是真包**（ADR-0024），一律 `from ragcore.services... import ...`，不再有 sys.path 垫片或裸 `services/`、`config/` 顶层名。本包以 `memory_agent.` 前缀绝对导入，并依赖已安装的 `ragcore`。
