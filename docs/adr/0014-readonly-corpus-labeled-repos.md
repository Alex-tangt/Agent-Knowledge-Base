# 0014 只读语料：带标签的多仓库文档（用户故事 #17）

Status: accepted

issue #7（记忆能力包 MVP）收尾时落地：把「三个项目仓库只读语料」从占位符变成真实能力
（ADR-0008 D2 留的扩展点）。本 ADR 只记录**难逆或真权衡**的决策。

## 决策

**D1 只读语料 = 项目仓库的 Markdown 文档，不索引代码。** 业主明确取舍："只把文档放进去
就行了，代码就没必要了"。装载器本就只收 `.md`，故"文档"与"代码"的边界天然清晰；索引里
只出现 `.md`（README / CONTEXT / AGENTS / docs / ADR / 评测记录等），不出现源码。

**D2 配置 = gitignored 的 `readonly_repos.json`（`[{label, path}]`）+ 提交 `readonly_repos.example.json`。**
理由：三个仓库是**机器本地绝对路径**，硬编码进可发布包即"换机器就坏、还泄露目录结构"。
优先级（`settings._readonly_roots`）：

1. `MEMORY_READONLY_ROOTS` 环境变量（`os.pathsep` 分隔；**空串 = 无只读语料**）——sandbox/评测
   套件用它把索引限制在可写 KB 内（#16），必须保留。
2. 否则读 `readonly_repos.json`（相对路径按仓库根解析）。
3. 都没有 → 默认仅本仓库（`DEFAULT_READONLY_LABEL` = 目录名），保持包自足、开箱可用。

**D3 `source` / `id` 加 `<label>/` 前缀以消歧义。** 不同仓库都有 `README.md` / `CONTEXT.md`，
裸相对路径会让只读条目 id 互撞（`repo:README.md`），`load_corpus` 去重后**静默丢条**。
故只读 `source = "<label>/<rel>"`，条目 id = `repo:<label>/<rel>`；标签重复时加 `-2`、`-3`
后缀（`_uniquify`），路径重复则去重。`memory_get` 仍按 manifest 里的绝对 `path` 读文件。

**D4 噪声排除扩到多仓库常见的生成物 / 工具缓存 / 数据目录。** 在原有集合（VCS、venv、
`__pycache__`、IDE/cache、`vector_db`、`build`…）上补 `outputs`、`dataset`、`.scratch`、
`.playwright-cli`、`.pi-agent`、`.pi`、`.claude`、`.codex`、`.uv`、`.cache`、`models`。
依据：这些目录在三个仓库里分别装着抽取产物 / 数据集 / 临时脚本 / 工具会话（如
kg-triplet-sft 的 `outputs/` 本身就在它自己的 `.gitignore` 里），属于 issue #7 说的
"generated data dumps and raw corpora"。

## Consequences

- 语料规模（本机）：可写 KB 26 条 + 只读 106 条（agent-knowledge-base 51 / kg-triplet-sft 33 /
  agent-infra 22）= **132 条**；CPU 全量重建约十几分钟，与既有"~6 min/60 条"一致。
- **改配置必须重启 daemon**：`READONLY_ROOTS` 在 import 时求值。若 daemon 持有的只读根与
  索引不一致，写入触发的增量 `refresh()` 会把"不在 daemon 语料里"的条目当孤儿**删掉**。
  这是实现约束，不是 bug——`proxy.py --stop` 后重建/重启即可。
- 未引入 per-repo 的 `.gitignore` 感知；噪声排除仍是全局目录名集合。需要时按仓库补
  `exclude` 字段（当前不需要）。
- `memory_search` 结果里 `writable=false` 即只读语料，`memory_add` / `supersede` / `archive`
  一律拒写（#11/#12 既有守卫，未改动）。
