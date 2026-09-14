# 给 coding agent 造一个安全的长期记忆：一次 MVP 的决策、踩坑与证据

> 项目：Agent-Knowledge-Base（hero = agent 记忆能力包，MCP + skill）
> 时间：2026-09 · 单机 Windows / Python 3.12 · Qdrant local mode + BGE-M3
> 结论先行：把 Markdown 当真相源、向量索引当派生物、所有写入过一个带生命周期语义的网关。功能全部落地，单测 163、写路径确定性套件 25/25、跨会话盲测 recall 通过。

## 0. 问题：agent 的"记忆"到底缺什么

我有一个跨项目使用的个人 Markdown 知识库（21 篇、5 个域、git 版本化），加上三个项目仓库。问题是：coding agent 只能靠 grep 去翻它。

这带来两个真实的痛：

1. **检索是字面的**。我记的是"决策质量与竞争性假设（ACH）"，问的是"怎么避免拍脑袋定方案"——grep 命中不了。agent 于是把已经记录过的结论**重新推导一遍**，浪费上下文、还可能推歪。
2. **写入是不安全的**。让 agent 直接往 Markdown 里写，它可能：写成重复条目、把一条记忆拆成五条、原地覆盖掉旧结论、或者干脆删文件。**知识库最怕的不是搜不到，是悄悄坏掉。**

所以这个 MVP 的目标不是"再做一个 RAG"，而是：**让 agent 能可靠地回忆，并且有一个值得信任的写入口。**

## 1. 形状：三个模块的单仓

```
ragcore/       可复用核心（零 FastAPI 依赖）
legal_web/     适配层实例 / 回归锚点（政策法规 RAG web）
memory_agent/  hero：记忆能力包（MCP + skill）
```

这不是一开始就有的。起点是大学 NLP 课程作业（政策法规问答 web）。把 hero 切成记忆能力包之后，原来的 web 退成"适配层实例 + 回归锚点"：它仍然必须能启动、能评测，用来证明核心没被改坏。

**为什么坚持单仓而不是拆库**：`memory_agent` 直接复用 `ragcore` 的 BGE-M3 与 Qdrant 封装（in-process），拆库会立刻引入"两个包版本怎么对齐"的运维问题，收益为零。跨模块用 sys.path 垫片连接，包名保持 `services/`、`config/`、`strategies/` 不变。

## 2. 核心决策

### 2.1 Markdown 是真相源，向量索引是派生物

```
可写记忆 = 全局 KB 的 Markdown 条目（带 frontmatter: id/type/tags/status/sources/supersedes…）
只读语料 = 项目仓库的 Markdown 文档（writable:false）
向量索引 = 从上面两者重建出来的、可随时丢弃的加速结构
```

**为什么**：记忆的语义是"可版本化、可回退、可人读"。数据库或向量库做主存，都做不到 `git log` 一眼看变更、`git revert` 一键回退。索引坏了？重建即可——它是派生物，不承载记忆本体。

这个决定后面反复救了场：出过"索引 0 点但 manifest 还写着 60 条"的静默故障，但因为真相源没丢，重建就完事。

### 2.2 写入网关：不覆盖、不删除、不原地编辑

对外只暴露 MCP 工具，**不暴露任何裸文件写**。写入必须过一次网关：

```
search → dedup → validate(frontmatter) → write → git commit → incremental refresh
```

生命周期语义只有两种，都不是"删除"：

| 操作 | 语义 | 落盘效果 |
|---|---|---|
| `supersede` | 有替代的更新 | 新建条目（`supersedes=old`）+ 旧条目置 `superseded`、`superseded_by=new`，同一 commit |
| `archive` | 无替代的退役 | 置 `status: archived` + `archive_reason`，**文件永久保留** |

两者都是破坏性操作，默认 `confirm=false` **只返回预览**，用户明确同意后才 `confirm=true` 落盘。

**为什么这么重**：agent 写记忆是"无人监督的写"。如果工具能原地改或删，一次幻觉就可能抹掉半年的知识。把"删除"从工具面里彻底拿掉，是把安全性做成**结构约束**而不是提示词约束——agent 想做坏事也做不到。

还有一条工程细节：每次写入 = **只包含本次触及文件**的一个 git commit。这在多会话并发时很关键——如果 `git add -A`，就会把别的会话正在编辑的脏文件一起提交进去。

### 2.3 索引一致性：代目录 + 指针原子切换

重建索引是长活（CPU 上 ~6 分钟/60 条），中间会被超时、kill、或崩溃打断。朴素做法（清空集合再重建）如果中途死掉，就留下"**空索引 + 陈旧 manifest**"——`memory_get` 照常、`memory_search` 返回空，**不核对根本发现不了**。这个坑真实发生过一次。

现在的做法：

- 每次全量重建写在**新代目录** `vector_db/gen-N/{qdrant,manifest.json}`；
- 全部建好并通过**自洽核对**（manifest 条数 == 集合点数）后，`os.replace` **原子切换** `CURRENT` 指针；
- 中断只留下一个没被接管的代目录，旧代继续服务；
- 检索前核对自洽性，不一致就**显式报错**，绝不静默返回空。

增量刷新按内容 hash 跳过未变条目，删除/改名留下的孤儿点按稳定 `uuid5(entry_id)` 定点删除。

### 2.4 一个常驻 daemon + 每会话一个瘦代理

这是整个项目里最"物理"的一个决策，因为它由一次 OOM 逼出来的。

最初每个 opencode 会话各拉起一份 stdio MCP，各加载一份 BGE-M3。BGE-M3 权重 2.17GB + torch 运行时 + 加载峰值 ≈ **每进程 3.9GB 私有内存**。三条并行会话 = ~12GB，直接把系统 commit 打满，结果是 `Out of memory`、卡死、`uv_spawn` 失败。

改成：

```
一个常驻 HTTP daemon（持有唯一一份 BGE-M3，127.0.0.1:8765）
  ↑ 每会话一个几十 MB 的 stdio 代理，透明转发 stdio ↔ HTTP
```

N 会话内存从 `N × 3.9GB` 降到 `1 × 3.9GB + N × 几十MB`。

配套的坑：
- 代理必须**幂等拉起** daemon。N 个会话同时冷启动会各自 spawn 一个 daemon（又回到 N 份模型），所以用**启动权文件锁**：只有一个进程负责 spawn，其余只等健康检查。
- Qdrant local mode 在**同一进程内也不能并发开 client**（锁是"按目录 + 全进程"的），所以 `VectorStoreService._session` 用模块级 `RLock` 把并发串行化。
- 默认**不预热**模型（要低延迟设 `MEMORY_WARMUP=1`）：启动私有内存从 3953MB 降到 54MB。

### 2.5 只读语料：带标签的多仓库文档

最后一块拼图是把三个项目仓库的文档也纳入检索。做了几个取舍：

- **只索引 `.md`，不索引代码**。三个仓库的代码对"回忆决策"没有价值，进语料只会稀释检索。
- **仓库清单不硬编码**。三个仓库是机器本地绝对路径，写进可发布代码就是"换机器即坏 + 泄露目录结构"。改成 gitignored 的 `readonly_repos.json`（`[{label, path}]`）+ 提交一份 `.example` 模板；环境变量 `MEMORY_READONLY_ROOTS` 仍是最高优先级（测试/沙箱用）。
- **加 `<label>/` 前缀消歧义**。三个仓库都有 `README.md`、`CONTEXT.md`，裸相对路径会让只读条目 id 互撞 `repo:README.md`，去重时**静默丢条**。现在 `source = "agent-infra/docs/adr/0007....md"`，id = `repo:agent-infra/...`，标签重复再加 `-2`。
- **噪声排除**：`outputs/`、`dataset/`、`.scratch/`、`.playwright-cli/` 等生成物和工具缓存一律不进——其中一个仓库光 `outputs/` 就有 1339 个 markdown 抽取产物，全进索引就是一场灾难。

改配置有个必须记住的约束：**`READONLY_ROOTS` 在 import 时求值，改完必须重启 daemon**。否则 daemon 拿着旧的只读根做增量刷新，会把"不在我语料里"的条目当孤儿删掉。

## 3. 踩过的坑（都可复用）

1. **stdio MCP：stdout 就是协议通道。** `ragcore` 的 logger 默认把 root handler 配到 `sys.stdout`，一行日志就能腐蚀 JSON-RPC。必须在 import `ragcore` **之前**把 root logger 抢配到 stderr。
2. **模块名会遮蔽包。** `memory_agent/config.py` 在 `python memory_agent/x.py` 下会遮蔽 ragcore 的顶层 `config` 包，报 `No module named 'config.config'`。改名 `settings.py` + 内部一律 `memory_agent.` 前缀绝对导入。
3. **Qdrant local mode 的两个反直觉语义**：(a) 独占锁在 **client 构造期**持有、`close()` 释放，所以不要长持 client，按操作开/关（实测 ~19ms/次）；(b) `delete_collection` 后再用同名 `create_collection`，磁盘上的旧点会**复活**——清空集合要用空 filter 删光点，而不是删集合。
4. **"命令返回了" ≠ 成功。** 重建进程被 kill，命令看起来"结束"了，但状态是坏的。长活必须后台启动 + 子进程内重定向 + 独立轮询进度 + 完成后自洽核对。
5. **客户端超时 ≠ 写入失败。** MCP 客户端 20s 超时，但服务端可能已经 commit 成功。超时后要核对真相源（KB 的 `git log` / 索引状态），**不能直接重试**（重试会命中去重，只返回 duplicate）。
6. **去重阈值要校准，不要拍脑袋。** 最初拍 0.92，在真实 KB 上量了一下：不同条目的最近邻 ≤0.792，精确重加最低 0.949，于是改成 0.88 落在间隔中部，多留出同义重加的捕获余量。

## 4. 证据，而不是感觉

"AI 写的代码"最大的风险是**看起来很对**。所以整个过程尽量把结论变成机器可验的锚点：

| 能力 | 证据 |
|---|---|
| 核心服务 | `pytest tests/unit` → **163 passed** |
| 写路径确定性 | 真实 KB 克隆 + 隔离索引的 sandbox 套件 → **25/25**，两次运行一致，真实 KB 前后逐字不变 |
| 索引一致性 | `memory_index_status` → `entries == points`、`consistent=true` |
| 跨会话 recall | 独立进程 + 独立子代理两路盲测，同一改写查询命中同一条，score 逐位相同 `0.636646…`；该查询关键词全库 grep **0 命中**，证明是语义召回 |
| 只读语料 | 三仓库 132 条 = 26 可写 KB + 106 只读文档；`supersede`/`archive` 对只读条目直接拒绝 |
| 回归锚点 | `legal_web` 启动冒烟：`/api/status` ready、`/` 与 `script.js` 200、KB 列表与文档数正确、退出后端口与锁释放 |

几个刻意的测试口径：

- **只断言外部行为**。sandbox 只检查 MCP 工具返回值、落盘文件、`git log/status`、`kb.py check` 输出，**不碰内部 Qdrant 调用**。这样重构内部实现不会把测试改成"测实现"。
- **真实 KB 只读**。sandbox 克隆真实 KB 的已提交态到临时目录，跑完用 `git status`/`rev-parse` 证明真实 KB 逐字未变。
- **锚点纪律**。结构怎么重构，基准 A（单测 + 导入冒烟 + 启动冒烟）必须每天可复现。

## 5. 方法论：让 AI 开发可验证

这个项目的执行者是 AI（我提供思路和裁决，AI 写代码）。跑下来最有价值的不是某段代码，而是几条把"AI 生产力"变成"可验证结果"的纪律：

- **请求先进路由**：缺陷走根因流程、新功能走决策闸门、实验只进 `experiments/<name>/`（先写记录再跑，无结论不算完成）。
- **一个单元 = 一个 commit**：能一句话说清 + 能独立验证的增量就提交，不攒大提交。
- **硬决策必须留 ADR**：难逆转 / 反直觉 / 真权衡的，写进 `docs/adr/`。这个项目留了 14 篇，覆盖每一次"为什么这么选"。
- **同一目标失败三次就停手调研**，不要原地重试。
- **收工过健康闸门**：git 干净、文档与目录树一致、无垃圾文件、实验有结论、决策有落点。

## 6. 下一步

- **检索质量**：这套读路径用的是 BGE-M3 + 向量检索；下一步做 BEIR 子集 nDCG@10，把"检索有多好"从感觉变成可比数字。
- **混合检索融合**：向量 + 关键词 + 锚点的加权融合是否最优。
- **北向**：企业级 agent 知识管理（多租户、缓存、降级、内容生命周期）——明确暂不在范围内。

## 附：一句话总结

**把不可逆的操作从工具面拿掉，把真相源和派生索引分开，把每个结论都变成可复现的锚点。** 这样即便写代码的是 AI，"记忆"这件事仍然是可靠的。
