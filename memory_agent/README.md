# memory_agent — 记忆能力包

Agent-Knowledge-Base 的第一个能力：让 coding agent 通过 MCP 语义检索、读取、并**安全写入**长期记忆。

- 产品形状与架构：`docs/adr/0005`（产品升级）、`docs/adr/0006`（Markdown 真相源 + 派生索引 + 写入网关）、`docs/adr/0007`（布局与更名）、`docs/adr/0008`（读路径接缝：MCP 选型 / 语料范围 / stdio 铁律）、`docs/adr/0009`（写路径：commit 归属范围 + 去重命中语义）、`docs/adr/0011`（索引一致性：代目录 + 指针切换）、`docs/adr/0013`（拓扑：共享单实例 daemon + 代理）、`docs/adr/0014`（只读语料：带标签的多仓库文档）、`docs/adr/0024`（独立包化：`ragcore` 真包 + 本包可安装）。
- 需求全貌（user stories / 决策 / 测试口径）：GitHub issue #7；实现票据 #10–#17。
- 第二消费者（#41）与共享可读写（#44 / `docs/adr/0025` D19）：见下方「第二个消费者」。

## 约定（不变量）

- **真相源是 Markdown**（条目 + frontmatter），向量索引是派生、可丢弃的。
- **只暴露 MCP 工具，不暴露裸文件写**；破坏性写（supersede / archive）需确认，永不原地改写或删除。
- 传输（#19 / ADR-0013）：**一个常驻 HTTP daemon 持有唯一一份引擎，每会话一个 stdio 代理转发**（N 会话共享，内存 `1×3.9GB` 而非 `N×`）；另保留纯 stdio 模式供单会话/手动。索引走**独立** Qdrant 路径，client 按操作开/关——可与 `legal_web` 并存（见 ADR-0008 D5）；单 daemon 的进程内串行化见 ADR-0013 D3。
- **stdout 是协议通道**：日志必须走 stderr。`memory_agent` 在 import ragcore 之前抢配 root logger（见 `_bootstrap.py`）。

## 现状（#10 读路径 + #11 写入网关 + #12 生命周期 + #13 索引一致性 + #19 共享单实例）

```
memory_agent/
├── mcp_server.py          # MCP 服务：默认 stdio；--transport http 起共享 daemon（/health + OpenAI 兼容 /v1/embeddings）(#10-#13,#19,#43)
├── proxy.py               # 每会话瘦代理：幂等确保 daemon 在跑 + stdio<->HTTP 转发          (#19)
├── connect.py             # 一步安装第二个消费者（DeepTutor）：部署级 mcp.json + 核验 + skill 落位；共享可读写 (#41,#44)
├── runtime.py             # 先立 stderr 日志，再装配索引 / 写入网关单例
├── _bootstrap.py          # stdio 安全日志（import ragcore 前抢配 root logger 到 stderr）
├── settings.py            # 真相源 / 索引根 / 来源注册表 / overlay / 指针名 / 集合名 / 去重阈值 / daemon 端点
├── readonly_repos.json    # 本地只读来源注册表（gitignored；模板见 .example.json）(#17,#36)
├── overlay.json           # 显式收录清单 include/exclude（gitignored；模板见 overlay.example.json）(#36)
├── corpus/loader.py       # 运行时收录解析（注册表默认 ∪ overlay）+ KB/只读文档发现 + 指纹 (#10,#17,#36)
├── memory/entries.py      # frontmatter 解析 -> Entry；稳定点 id（uuid5）    (#10,#13)
├── memory/index.py        # 当前代视图：检索 + 增量 refresh（hash 跳过/孤儿清理）(#10,#13)
├── memory/retrieval.py    # 检索接缝：策略召回（向量+关键词，走 ragcore）+ 可选 rerank   (#24)
├── memory/layout.py       # 代目录 + CURRENT 指针 + 原子切换                 (#13)
├── memory/reindex.py      # 分块全量重建（新代建好再切指针 + 自洽核对）       (#13)
├── memory/errors.py       # IndexNotBuiltError / IndexConsistencyError       (#13)
├── memory/locks.py        # 进程内 INDEX/WRITE 锁（单 daemon 并发安全）        (#19)
├── memory/authoring.py    # 渲染 frontmatter + 镜像 kb.py check 的校验        (#11)
├── memory/writer.py       # 写入网关：搜索→去重→校验→落盘→commit（不刷索引，D13）(#11,#12,#36)
├── memory/admission.py    # 收录接口面：include/exclude/list（DDL；移除走预览+确认）(#36)
├── gateway/               # 检索网关：/mcp 边界 authn + 工具层 authz 强制注入 (#32)
│   ├── identity.py        #   身份形状 + token/进程配置解析（绝不回显凭证）
│   ├── authz.py           #   effective filter（白名单，只可收窄；tenant + ABAC）
│   ├── middleware.py      #   /mcp 边界中间件：解析身份 → 请求上下文 → 审计
│   ├── context.py         #   请求期身份 ContextVar（工具层读取）
│   └── audit.py           #   调用审计（JSONL；配额留钩子）
├── build_index.py         # CLI：从 Markdown 全量重建（新代 + 切指针）        (#10,#13)
├── eval/                  # 运行时证据：baseline_A.md（锚点）、issue19_acceptance.md（#19）、write_path_sandbox.py + _results.md（#16）、readonly_corpus_17.py（#17 三仓库只读）、dogfood_17.md、retrieval_eval.py + metrics.py + retrieval_eval_set.json + retrieval_baseline.md（#24 确定性检索评测）、mcp_install_smoke_26.py + _results.md（#26 安装 + MCP 集成冒烟）、shared_service_41.py + _results.md（#41 第二消费者共享服务）、bge_m3_embeddings_43.py + _results.md（#43 BGE-M3 embeddings 端点）
├── pyproject.toml         # 本包（可安装，依赖 ragcore）                       (#26)
└── (skill)                # 见 #14：指导 agent 何时 search/read/add 及破坏性确认规则
```

写入**只落真相源**（不再同步刷索引，D13）；索引由下一次 `memory_search` 前的**廉价指纹检查**
（stat `mtime`+`size`）驱动增量追平——刚写入的条目在下一次检索即被搜到。索引不自洽
（manifest 条数 ≠ 集合点数）时检索会**显式报错**，用 `memory_reindex` 分块全量重建。

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

# 4) 第二个消费者（如 DeepTutor）一步接入：共享同一 daemon（#41 / #43 / ADR-0025 D17-D18）
venv\Scripts\python.exe -m memory_agent.connect --deeptutor-home <DeepTutor 运行根>
#    它作为 MCP streamableHttp 客户端连 http://127.0.0.1:8765/mcp——不新建服务端、不复制索引，
#    共享服务即共享基表 + 派生索引。安装器只做配置 + 核验：
#      - 写 DeepTutor **部署级** <home>/data/user/settings/mcp.json（保留其它条目、原子写、幂等）；
#        home 缺省 = $DEEPTUTOR_HOME，否则 CWD（与 DeepTutor 自身 get_runtime_home 一致）。
#      - 写 <home>/data/user/settings/model_catalog.json 的 embedding profile
#        （binding=vllm → http://127.0.0.1:8765/v1/embeddings、dimension=1024、免 key；
#         保留 LLM profile 与其 key）。用 --no-embedding 可跳过。
#      - 落位 skill 到 ~/.config/opencode/skills/memory-agent/。
#      - 核验 opencode 注册（缺则打印 patch，**不擅自改用户配置**）。
#      - 幂等确保共享 daemon 在跑；--dry-run 只预览；--no-daemon 跳过。
#    共享边界（#44 / ADR-0025 D19 修订 D17）：全局知识库是**所有 agent 可读可写**
#    的共享域——安装器在 DeepTutor 侧用 enabled_tools 放开 search / get / add /
#    supersede / archive / index_status / ingest_list；写经本产品写入网关 + 单 daemon
#    `WRITE_LOCK` 串行化，审计对每次 tools/call 记 agent 身份（可归属）。
#    维护 / 收录 DDL（reindex、ingest_include/exclude）不进白名单。
#    （console script：memory-agent-connect）

# 5) daemon 的 OpenAI 兼容 embeddings 端点（#43）：与索引共用同一份 BGE-M3
#    POST http://127.0.0.1:8765/v1/embeddings   {"input": "..." | [...], "model": "BAAI/bge-m3"}
#    → {"object":"list","data":[{"embedding":[1024 floats], ...}], "model": ...}
#    authn 与 /mcp 同源（未配 token = 零配置直连）；仅 float 向量。
```

只读来源（#17 → #36）：**注册表默认 ∪ 显式 overlay**，两者都在运行时重读，**改完免重启**。

- 注册表 `memory_agent/readonly_repos.json`（gitignored；复制 `readonly_repos.example.json` 改）：
  只索引 `.md`、不索引代码；`label` 跨仓库消歧义；可选 `owner`（缺省 = label）。

```json
[
  {"label": "agent-knowledge-base", "path": ".", "owner": "me"},
  {"label": "agent-infra", "path": "D:/python_work/work2026-8/Agent-infra"}
]
```

- overlay `memory_agent/overlay.json`（gitignored；复制 `overlay.example.json` 改）：显式追加
  `include`（精确文件 / 窄 glob）、收窄 `exclude`；粒度 = 路径模式。也可直接用 MCP 工具管理：
  `memory_ingest_list` / `memory_ingest_include` / `memory_ingest_exclude`（移除走预览 + 确认）。
  见 `docs/adr/0025` D8。改完下一次 `memory_search` 即生效（指纹变化 → 增量刷新）。

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
- `memory_search(query, k=5, writable_only=False)` → 条目级命中（`id/title/source/writable/type/tags/status/owner/score/snippet`；`owner` = 域所有者，只读条目为来源 label，读侧可见 #45）；`score` 越大越相关（向量 + 关键词混合召回：关键词命中批次 >1，其余为余弦相似度）。
- `memory_get(entry_id)` → 该条目的**真实 Markdown** 内容 + 元数据。
- `memory_add(title, body, section, type, tags, slug, sources, status, allow_duplicate)` → 唯一写入口；`section`（`topics | decisions | projects/<slug>`）是全局知识库内的分区（旧名 `domain` 为弃用别名，#45）；写前去重，命中近似条目则**不写**并返回候选（`allow_duplicate=true` 是误报出口）；写入 = frontmatter 过校验 + 一个**只含本条目文件**的 git commit（**不同步刷索引**，D13：索引由下一次 `memory_search` 的指纹检查追平）。
- `memory_supersede(old_id, ..., confirm=False)` / `memory_archive(entry_id, reason, confirm=False)` → 破坏性；默认只预览，`confirm=true` 才落盘（见 `docs/adr/0010`）。
- `memory_reindex(cursor=None, batch=16)` → 分块全量重建（`cursor=None` 开始，拿 `cursor` 续调到 `done=true`，此时指针已切）。
- `memory_index_status()` → `{built,gen,entries,points,consistent,built_at,path}`。

导入 `ragcore` 的方式与 `legal_web` 一致：**`ragcore` 是真包**（ADR-0024），一律 `from ragcore.services... import ...`，不再有 sys.path 垫片或裸 `services/`、`config/` 顶层名。本包以 `memory_agent.` 前缀绝对导入，并依赖已安装的 `ragcore`。
