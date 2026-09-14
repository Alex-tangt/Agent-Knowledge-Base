# memory_agent — 记忆能力包

Agent-Knowledge-Base 的第一个能力：让 coding agent 通过 MCP 语义检索、读取、并**安全写入**长期记忆。

- 产品形状与架构：`docs/adr/0005`（产品升级）、`docs/adr/0006`（Markdown 真相源 + 派生索引 + 写入网关）、`docs/adr/0007`（布局与更名）。
- 需求全貌（user stories / 决策 / 测试口径）：GitHub issue #7；实现票据 #10–#17。

## 约定（不变量）

- **真相源是 Markdown**（条目 + frontmatter），向量索引是派生、可丢弃的。
- **只暴露 MCP 工具，不暴露裸文件写**；破坏性写（supersede / archive）需确认，永不原地改写或删除。
- 传输：**stdio**；引擎**进程内**调用 `ragcore`（因 Qdrant 本地模式独占锁 + 模型重）；**不与 `legal_web` 并发**运行。

## 目标结构（票据逐步填充）

```
memory_agent/
├── mcp_server.py    # stdio MCP：memory_search / memory_get / memory_add / memory_supersede / memory_archive   (#10,#11,#12)
├── memory/          # 写入网关、条目解析、frontmatter 校验、生命周期语义                                  (#10,#11,#12)
├── corpus/          # 真相源（KB 条目，可写）与只读语料（项目仓库文本）的装载/排除规则                    (#10)
├── eval/            # 检索与写路径证据：baseline_A.md（锚点）、BEIR nDCG@10、写路径确定性套件             (#9,#15,#16)
└── (skill)          # 见 #14：指导 agent 何时 search/read/add 及破坏性确认规则
```

导入 `ragcore` 的方式与 `legal_web` 一致：启动时把 `ragcore/` 加入 `sys.path`，沿用 `services/`、`config/`、`strategies/` 原包名。
