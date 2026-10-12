# MemOS 本地实现源码研究：个人知识库与 agent 经验记忆的接缝

研究日期：2026-10-11（Asia/Shanghai）。研究快照：`MemTensor/MemOS@a7367d07e55db61099f7b4e2c1108bc5831a24f3`。本地只读目录：`D:\python_work\work2026-4\MemOS`。

本报告研究 `apps/memos-local-plugin`、`apps/memos-local-openclaw` 及 `packages/memos-core`，不替代 Python `src/memos` 的专项报告。结论来自固定源码、测试内容和一个内存 SQLite 探针；没有安装依赖、调用模型、启动上游产品、修改上游仓库或执行 AOCI。测试文件被阅读，**未运行其 Vitest/Python 测试套件**。

## 1. 对我们的架构最有影响的结论

1. **MemOS Local 新版主要管理 agent 工作经历与从经历学习的行为知识。** 它有 session/episode、trace、policy、world model、skill、feedback 的实际模型。它与保存人的文章、视频、笔记原文的通用知识库存在交集，但两者的基本对象与生命周期不同。[实际 schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L30-L244)
2. **完整自进化链路有代码，默认却是轻量模式。** `lightweightMemory.enabled=true`；轻量路径按 turn 合并、摘要、只计算 summary 向量，并不挂 reward/L2/L3/skill/feedback 的演化订阅者。不能把 README 中完整算法当成默认运行行为。[默认值](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/config/defaults.ts#L103-L108)、[订阅者分支](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/deps.ts#L244-L267)、[实际轻量 capture](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/capture/capture.ts#L250-L324)
3. **“三个宿主共享 core”指新版 app 内的 TypeScript core，不能推导它与 Python MemOS 共用运行时。** DSH/OpenClaw 直接 import 该 TS core，Hermes 的 Python provider 经 JSON-RPC 调用 Node bridge。新版包自身编译 core、contract、adapter、bridge；旧 OpenClaw app 又编译自己的 `src/`。没有发现这些 TS app 运行时导入 Python `src/memos`。[新版编译范围](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/tsconfig.build.json#L1-L21)、[DSH 的直接依赖](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/adapters/deepseek-harness/index.ts#L15-L24)、[Hermes bridge 入口](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/bridge.cts#L1-L14)、[旧版编译范围](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/tsconfig.json#L1-L25)
4. **最有价值的联动是“统一内容基座 + 可替换的经验处理模块”。** 原文先进入我们的基座；Local 的捕获、经验抽取、反馈和上下文装配可作上层消费者，自己的 SQLite 保存处理状态与派生结果。这是本报告的架构建议，尚未实现或验证这个适配器。
5. **不要按文件系统术语直接移植整个 Local 存储层。** 数据库保存了 agent 事件、算法状态和很多可变的派生实体；所谓存储接口仍暴露 SQLite/SQL/原生 handle。把它换成 OpenViking 并不是实现几个 read/write 方法就能完成。[StorageDb](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/types.ts#L9-L48)、[实际 bootstrap](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L224-L271)

## 2. 本仓存在的本地代码形态

| 形态 | 固定快照中的实际组织 | 不能混用的结论 |
|---|---|---|
| `apps/memos-local-plugin`，`2.0.16-beta.1` | app 内 `core/`、`agent-contract/`、OpenClaw/DSH/Hermes adapters、HTTP viewer、Node bridge | 这是本报告推荐继续观察的本地实现；完整演化不是默认开启 |
| `apps/memos-local-openclaw`，`1.0.9-beta.1` | `index.ts` 自己初始化 SqliteStore、Embedder、IngestWorker、RecallEngine、任务/skill处理、viewer/hub，直接挂 OpenClaw hooks | 其 chunk/task/dedup/recency 结论不能直接套到新版 trace/policy/world 模型 |
| `packages/memos-core/src` | 另一组 chunk/task 时代 TS 文件和 `initPlugin` 门面 | 上述两个 app 的编译与 import 不指向这里；不能以目录名认定它是当前三个宿主使用的共同实现 |
| `src/memos` | Python 主库及 API；由另一专项报告研究 | 同一个 Git 仓库与同一个品牌不等于相同 schema 或同一进程运行时 |

证据：[新版 manifest](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/package.json#L1-L59)、[旧版 manifest](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/package.json#L1-L58)、[旧版实际入口依赖](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/index.ts#L9-L32)、[packages 中的独立门面](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/packages/memos-core/src/index.ts#L1-L69)。还做了静态 import 搜索：两个 app 没有指向 `packages/memos-core` 的 runtime import；没有发现调用 Python主库的 import。此负向结论限于该快照与已搜索代码文件，不排除产品级的协议互联。

## 3. 新版数据层次：哪些是证据，哪些是推断

| 数据 | 实际字段与关系 | 对我们意味着什么 |
|---|---|---|
| Session / Episode | 宿主 agent、owner kind/profile/workspace、起止时间、open/closed、trace IDs、任务评分、meta | 记录工作事件的边界，不是文章目录结构 |
| L1 Trace | user/agent 文本、tool calls、summary、reflection、native thinking、value/alpha/priority、tags、error signatures、summary/action vectors、turn ID | 有原始交流内容的成分，但文本会被规范化和截断；应当视为工作轨迹表示 |
| L2 Policy / Experience | title、trigger、procedure、verification、boundary、support/gain、candidate/active/archived；typed experience、confidence/salience、source episode/feedback/trace IDs、decision guidance | 接近“遇到何种任务应该如何做”和“用户希望/不希望如何做”，并非一般文章事实图谱 |
| L3 World Model | body、policy IDs、environment/inference/constraints 结构、domain tags、confidence、source episodes、vector、version migration | 是从多条经验抽象的环境/约束认识；不要自动提升为原始事实 |
| Skill | invocation guide、procedure JSON、来源 policy/world IDs、evidence anchors、eta/support/gain、trial attempted/passed、version、状态 | 可调用程序知识的草稿及生命周期，需要实际使用反馈 |
| Feedback / Decision repair | polarity/magnitude、上下文 hash、偏好、反模式和证据 | 反馈本身也应保留证据来源；偏好修订不等于改写过去的交流原文 |

这些字段见 [session/episode/trace schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L30-L109)、[policy schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L111-L174)、[world/skill schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L180-L244)、[feedback/repair schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L252-L289)。

本地实现的“关系”主要用 SQLite 表与 JSON ID 集合表达，不是从 Python graph backend 直接获得的 Neo4j/图数据库运行时。证据关联值得借鉴，但这些 ID 通常指向内部 trace/episode/policy；上述 schema 没有通用的外部原文 URI + 内容版本/hash 契约。因此，接我们的文章/视频库时应额外建立明确来源引用，不能只保留一个内部 trace ID。

### 3.1 它并非完整会话原文归档

`EpisodeManager.addTurn` 主要存内存态 turn；episode 元数据保留 pending/initial user text等恢复线索，持久 turn 内容主要经 trace capture。`onTurnEnd` 先组装工具与 assistant turns，再执行 lite/lightweight capture；capture 再抽取、规范化、摘要、嵌入、写 trace。默认 user/agent 上限 4000 字符、每个 tool output 上限 2000，保留头尾并插入 truncated 标记；非字符串工具输出在 `onTurnEnd` 还有 2000 字符的 JSON 转换上限。[EpisodeManager 持久性说明与 addTurn](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/session/episode-manager.ts#L1-L14)、[onTurnEnd](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/orchestrator.ts#L1468-L1544)、[规范化](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/capture/normalizer.ts#L23-L104)、[默认预算](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/config/defaults.ts#L103-L126)。

这是一项架构取舍，不等于该插件不能保存有用文本。我们的“原样保存用户批准的文章/视频总结、可以导出且跨宿主读取”已经是独立能力；不要交给上述 capture 路径作为唯一归档步骤。

## 4. 写入与学习的真实调用链

### 4.1 轻量默认模式

```text
host 接受用户输入 → prompt-time search / 注入
host 的 terminal turn → prepare/open episode + onTurnEnd
  → extractSteps → 按 turn 合并 → normalizeSteps
  → summarize → summary-only embedding → persist traces
  → 文本/向量/模式通道检索 → 排序/可选 LLM relevance filter → context packet
```

默认不运行 reward/L2/L3/skill induction。显式反馈会先落 feedback row，但轻量路径直接返回，不继续 reward/decision repair 学习。[轻量反馈分支](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L2480-L2535)

### 4.2 完整模式

```text
onTurnEnd → runLite → 新 traces 立即可见（reflection=null / alpha=0）
topic end / idle / session end / shutdown
  → episode.finalized → runReflect → capture.done
  → reward: R_human、value backprop、priority
  → reward.updated → L2 associate / candidate bucket / induction / gain
  → l2.policy.induced → L3 abstraction 与 skill crystallization
用户反馈/工具失败 → feedback / repair → guidance 或 typed experience
```

真正的 topic end 不是“每次 assistant 回复都完成任务”。下一条用户输入会分类为 revision/follow_up/new_task，episode 可以继续打开、关闭再重开。代码文件早期注释仍有“每 turn finalizes”的旧描述，应以 `onTurnEnd` 实际保持 episode OPEN 和 relation-routing 分支为准。[实际生命周期](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/orchestrator.ts#L1521-L1587)、[正常模式订阅连接](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/deps.ts#L270-L353)。

L2 只接收 value 超过阈值且已有向量的 traces，按签名把没有匹配既有 policy 的证据放 candidate pool；至少若干个不同 episode 才调用归纳 LLM。它需要任务反馈、执行轨迹和多个可比任务，单篇文章/视频总结并不天然具备这些信号。[runL2 接口/筛选](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/memory/l2/l2.ts#L50-L70)、[候选池与归纳](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/memory/l2/l2.ts#L120-L183)。

值得借鉴的取舍：负 value trace 的 priority 为 0，但仍保留在磁盘上，可用于失败修复；不能因为检索价值低就删除原始失败证据。[backprop](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/reward/backprop.ts#L15-L27)

## 5. 检索、上下文与共享范围

### 5.1 不只是向量检索

新版有 skill、trace/episode/typed experience、world model 三层候选；通道包括 summary/action/vector、FTS、短词 LIKE pattern、exact identifier、结构化错误签名。排序用最佳通道分数为主，RRF 作为多通道一致性奖励，再加 trace value/time decay 或 skill eta；然后 threshold、dedup、MMR、可选 LLM relevance filter。缺嵌入时仍可走关键词通道，LLM filter不可用时保留机械排序。[候选类型与通道](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/types.ts#L43-L191)、[排序实现说明与代码](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/ranker.ts#L1-L45)、[后置过滤与降级](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/retrieve.ts#L409-L440)。

SQLite 保存 Float32 BLOB，vector search 为 JS cosine 暴力 top-K；FTS 为 trigram并由 SQL triggers 跟随 insert/update/delete。这里只确认实现，不接受注释中的吞吐数字作为实测性能。[向量实现](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/vector.ts#L1-L26)、[FTS实际触发器](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/001-initial.sql#L336-L387)。

`InjectionPacket` 将候选变为宿主可注入文本，单普通 snippet有640字符上限；skill默认只给短描述与 `memos_skill_get` 调用提示；历史记忆包裹为 UNTRUSTED DATA。[injector边界](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/injector.ts#L33-L88)。`scheduleInjection` 按 chitchat/meta/memory_probe/new_task/follow_up 决定是否检索及使用哪层，个人事实查询可只走 Tier2。[注入调度](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/injection/scheduler.ts#L33-L101)。

这些模块比“再换一个向量数据库”更能补齐我们的 agent 使用层，但技能、经验、原文的分数不可直接当成同一种事实可信度。

### 5.2 `public` 不等于跨所有宿主的全局知识库

实际可见性谓词如下：

| 行的 owner / scope | 当前 namespace 是否可见 |
|---|---|
| 同 agentKind + profile | 可见 |
| private，其他 profile | 不可见 |
| public/local，同 agentKind，其他 profile | 可见 |
| public/local，其他 agentKind | 不可见 |
| hub | 可见于不同 agentKind/profile |
| owner 缺失或 unknown | 为兼容旧数据，直接可见 |

workspaceId被记录，但不参与上述 ownership/visibility 谓词。`ownerWhere` 也按 kind/profile。[SQL谓词与内存谓词](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/runtime/namespace.ts#L99-L172)

过滤位置并不全相同：trace 的 vector/FTS/pattern 搜索在 repo wrapper 注入 WHERE，批量读再过滤；skills/world/policies 的召回先取候选，随后 wrapper 的 `getById`/`list` 检查 `isVisibleTo`。所以除了最终可见范围，还要关注不可见候选是否占用有限召回名额；此报告未做候选饥饿的实测。[traces查询过滤](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/retrieval-repos.ts#L15-L103)、[world/policy读过滤](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/retrieval-repos.ts#L106-L165)、[skill候选到读回](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/tier1-skill.ts#L100-L181)。

这是本地工作流范围规则。HTTP server可配置 API key/本地viewer登录，它不能直接替代我们已经做好的服务端 host-reader/writer 身份网关；更不能把该 kind/profile命名空间解释为人的统一全局主体。[HTTP认证边界](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/server/http.ts#L237-L250)

## 6. 持久性、修改与迁移：有工程能力，也有边界

### 6.1 不是单纯内存队列，也不是整个学习流程的 durable task engine

SQLite以 WAL、foreign keys、busy_timeout、默认 `synchronous=NORMAL` 打开；迁移逐版本事务化。[connection](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/connection.ts#L25-L63)、[迁移过程](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrator.ts#L93-L140)

嵌入失败有持久补偿表：target kind/id/field、source text、状态、尝试次数、next-attempt、error，另有 worker claim 与 lease。retry worker 在持有 claim 的事务内更新 vector并标记成功，过期worker的成功/失败回写被忽略。这是可具体借用的恢复实现。[队列 schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/002-embedding-retry-queue.sql#L1-L24)、[lease migration](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/migrations/003-embedding-retry-lease.sql#L1-L4)、[claim与提交](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/embedding/retry-worker.ts#L40-L125)

session event bus为进程内 Map/Set pubsub；L2 的 inflight/pendingByEpisode为内存 Promise/Map并按episode串行，drain用于正常退出。启动扫描 stale/dirty episodes后异步恢复反思/评分，不是为所有L2/L3/skill边界统一定义任务outbox和exactly-once流程。[event bus](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/session/events.ts#L1-L25)、[L2订阅者](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/memory/l2/subscriber.ts#L36-L134)、[启动恢复](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L1099-L1127)。

建议借它的补偿/lease思想，同时在我们要异步运行的跨库任务上保留 source URI + hash + processor version + 幂等键和状态，而不是直接复用进程内订阅链作为跨服务事务。

### 6.2 正文编辑不自动代表向量与派生经验已追平

`MemoryCore.updateTrace` 调 `traces.updateBody`；repo只更新 body/summary/tags，不清除向量、不enqueue重嵌入。`updatePolicy`/`updateWorldModel` 编辑路径同样修改正文字段与edited_at；向量更新是独立方法。没有在这些调用链中发现由原文修订统一传播到关联policy/world/skill的失效机制。[trace edit](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L3184-L3201)、[trace repo update](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/repos/traces.ts#L759-L801)、[policy repo update](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/repos/policies.ts#L345-L382)、[world repo update](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/storage/repos/world_model.ts#L326-L351)。

这是明确的集成约束，不宣称已运行产品界面复现bug。下文 SQL探针进一步确认 FTS trigger 与向量BLOB更新行为有差异。若我们采用可变原文，必须自己补全派生失效/重处理规则；若采用不可变版本，则必须给新旧版本的关系与检索选择定义规则。

### 6.3 Skill verifier 的“验证”不等于执行成功

`verifyDraft` 比较 draft工具集合与证据工具集合，并检查文本词元共现比例；它不执行生成的程序。注释说工具集合包含，但实际接受阈值为coverage≥0.5；resonance同样是启发式指标。`applyFeedback` 才按实际trial/pass/fail、thumbs、reward调eta和状态。因此即便可复用生成器，也应保留candidate与真实试用反馈的分离。[verifier实现](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/skill/verifier.ts#L46-L100)、[lifecycle](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/skill/lifecycle.ts#L34-L88)

### 6.4 JSON导出是实体迁移，不是完整灾备格式

`exportBundle` 导出 traces/policies/worldModels/skills，固定version=1，并设读取上限（代码分别100000/5000/2000/5000，repo可能进一步钳制）；不含feedback、全部session/episode状态、队列、运行配置、所有历史。import按已有ID跳过冲突，为trace合成缺失session/episode，向量置空，需要重新嵌入。[export/import](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L4338-L4425)、[导入向量](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L4440-L4475)。

这很好地支持表示迁移，但不能因此称“可恢复一整套记忆运行系统”。我们应独立定义内容导出、处理数据库备份、配置与credentials恢复，避免混成一个承诺。

## 7. 旧 OpenClaw 实现提供的另一组经验

旧版 schema为 chunks（session/turn/role/content/summary）、独立embeddings、tasks、skills/skill_versions、task-skill link、hub共享表。`captureMessages` 会跳过system、自己的memory工具结果、boot/review提示，去掉宿主metadata及thinking/evidence wrappers；主要目的是防止系统注入与召回文本再次被采集成记忆。[原始schema](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/storage/sqlite.ts#L21-L123)、[capture过滤](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/capture/index.ts#L55-L130)

旧版 IngestWorker 的 queue是内存数组；主要路径不是调用chunker，而是将每条captured message以paragraph送 `storeChunk`。先摘要/embedding，再在同owner下检查content hash；完全重复时旧chunk降为duplicate、保留新chunk为active，并删除旧embedding。近似Top5候选由LLM判DUPLICATE/UPDATE；UPDATE生成mergedSummary，旧记录降为merged，保存merge history。[worker路径与exact去重](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/ingest/worker.ts#L10-L49)、[一条消息与语义合并](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/ingest/worker.ts#L129-L239)。

旧版 task processor 用session变化、时间间隔和LLM topic判断task边界；先持久chunk再让task processor分配与总结。其检索为FTS/vector/pattern→RRF→MMR→recency→relative threshold，与新版不应混为一套“固定算法”。[task processor](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/ingest/task-processor.ts#L17-L43)、[旧版recall](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/recall/engine.ts#L55-L95)、[融合与时间衰减](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-openclaw/src/recall/engine.ts#L138-L179)。

可以借用“避免记忆自我污染”和“保留历史/合并理由”。不建议把“最新重复覆盖旧记录”的检索活跃策略直接用于原文权威库：相同内容来自不同来源、不同时间，其出处可能都有价值；相似度不等于事实纠正或用户授权修订。

## 8. 可借用的代码接缝，以及必须一起承担的依赖

| 接缝 | 真实输入/输出与触发 | 借源码时必须承担什么 | 推荐方式 |
|---|---|---|---|
| `agent-contract` / `MemoryCore` | DTO、openSession/episode、onTurnStart/End、feedback、search/get、context packet | session/episode/turn IDs、宿主namespace、工具时间与输出、错误码；门面也混有viewer管理/导出/维护方法 | 借DTO思想，给我们定义较小的host事件协议；避免直接复制巨大门面 |
| capture worker | `EpisodeSnapshot`→normalized steps→summary/vector→traces；full mode另有reflection | `TracesRepo`当前是具体factory ReturnType，episodes repo、retry repo、LLM/embedder、bus、CaptureConfig、logger/id/type模块；需要明确截断与dedup语义 | 离线侧车/worker最合适，接已保存原文引用；不当原文写入事务 |
| `runL2` policy生成 | reward.updated后的high-value traces→candidate pool/policies/trace-policy links | 必须有value/reward、summary/action向量、episode多样性、signature、SQLite事务、5类repos、LLM提示、gain/support/status；不是一个输入文章输出facts的函数 | 暂缓全链迁移；先用明确任务反复发生的小领域试验 |
| reward/backprop / skill lifecycle | 有terminal reward与alpha的trace序列，或skill trial事件→分值和状态 | 纯计算函数仍import本地类型/logger；分值语义和反馈采集要统一；不能把任务成功分值当原文可靠性 | 最容易小范围借用；先保留真实trial数据和调参记录 |
| retrieval `RetrievalRepos` | 各种typed候选检索/读回→融合排序 | 并非纯content search；需要skills/traces/worldModel及可选policy字段、向量/FTS/pattern，namespace、特定阈值/boost；同一种查询的各层分数语义 | 接口比StorageDb深，适合实现我们自己的映射；先只接原文/摘要层，其他层按需加入 |
| ranker / injector / scheduler | candidates→ranked→InjectionPacket；intent/relation→RetrievePlan | candidate.type/tier/refId、渠道排名、向量MMR、value/eta/time、来源说明；注入API、内容上限、召回budget需宿主负责 | 上下文装配最有近期价值，优先借策略与测试，保留来源可读回 |
| storage facade | SQLite handle/prepare/tx→repo操作 | better-sqlite3 native ABI、SQL迁移、FTS5、Float32BLOB、版本升级及备份；`raw`明确是BetterSqlite3.Database | 用于worker状态和派生数据；不作为替换OpenViking的通用文件API |
| embedding retry queue |持久failed slot→claimed job→vector补偿 | target ID/field、source text、lease、重试退避、同DB事务；我们应补source hash/processor version | 借工程设计，和当前外部串行embedding服务对接；先验证维度/缓存/请求并发 |
| DSH/OpenClaw/Hermes hooks |宿主消息/模型route/工具事件→合同DTO；packet→宿主context |各SDK生命周期差异、准确模型route、取消/timeout、异步capture串行、shutdown；这些不属于内容存储 |继续让host adapters薄；不用为了读取库把整套学习器放进每个宿主 |

接缝源码：[CaptureDeps](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/capture/capture.ts#L20-L69)、[RunL2Deps](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/memory/l2/l2.ts#L50-L59)、[RetrievalDeps与embedder端口](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/retrieval/types.ts#L680-L702)、[PipelineDeps](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/types.ts#L129-L169)、[MemoryCore合同](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/agent-contract/memory-core.ts#L169-L280)。

### 8.1 独立于宿主运行有可行性，但不是零成本

core中的运行时imports没有发现直接import OpenClaw/DSH SDK；`bootstrapMemoryCore`/`createPipeline`可接配置、home、provider和测试注入依赖，适合独立调用。Hermes已经证明“非TS宿主+Node sidecar”的实现形态存在，Python provider不是Python主库。[bootstrap输入](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/pipeline/memory-core.ts#L151-L226)。

然而新版 npm包声明Node≥20、better-sqlite3、transformers、TypeBox、tsx、yaml，且DSH peers限定在rc版本区间；还包含viewer、server、hub、telemetry与配置迁移。直接取整个app会新增一套部署/数据库/模型配置面；抽小模块则需要我们维护import、types、prompts和测试边界。[依赖与宿主约束](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/package.json#L87-L110)。默认telemetry开启、LLM日志prompt/completion未默认redact，这些属于整包引入时必须主动检查的配置，而非该算法必需能力。[默认配置](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/core/config/defaults.ts#L330-L358)

### 8.2 DSH 不是只注册几个工具

adapter默认可以借宿主LLM，但完整自动恢复如果只有turn-scoped host route就关闭，因为后台没有owner turn可还原准确route。DSH foreground使用 `searchMemory`，注入source-labelled context；prepare/capture推迟并按session排队，避免memory处理拖住用户输入。它显式依赖Cordis、DSH agent/session/LLM/systemPrompt/tools服务；模型route、取消、失败开放、shutdown恢复是实际耦合。[DSH bootstrap与恢复开关](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/adapters/deepseek-harness/index.ts#L116-L144)、[启动](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/adapters/deepseek-harness/index.ts#L247-L282)、[foreground检索与注入](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/adapters/deepseek-harness/bridge.ts#L149-L273)、[后置处理](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/adapters/deepseek-harness/bridge.ts#L534-L605)。

因此，现有MCP/API继续用于跨宿主原文共享；仅当我们要跟踪DSH整个执行轨迹与反馈，才需要这类native hook adapter。

## 9. 推荐的最小联动设计（建议，尚未实现）

```text
Bubble / Codex / DSH / 其他宿主
  ├─ 原样内容写入/读取 → 当前 memory_base API → OpenViking 内容与索引
  └─ 宿主事件（可选） → 我们的事件合同 → 经验处理 worker
                                      ├─ SQLite：job、episode、feedback、候选、索引状态
                                      ├─ Local可借用：capture / guidance / policy或skill算法
                                      └─ 派生结果：来源 URI + 内容 hash + 生成器版本 + scope
检索装配层 → 原文候选 + 按任务选择的经验候选 → 控制预算的上下文 → 读回具体出处
```

建议先实现或试验的最小单位：

1. 同一篇批准保存的内容可以经URI/hash读回；经验模块只读这份证据，不重新截断覆盖原文。
2. 给worker少量明确任务事件与用户反馈，生成一种typed经验（例如“用户偏好引用原出处”），保留证据、candidate状态和来源范围。
3. 宿主检索时只比较有经验装配和仅原文检索的差异，观察是否减少纠错、是否引入错误推断及上下文成本。
4. 再决定是否引入L2/L3/skill完整链；不要先开启所有反思、分类、过滤LLM调用。

关键合同至少包含 `source_uri`、`source_hash`、`source_kind`、`producer/version`、`created_at`、`owner/scope`、`derived_from`、处理状态/错误以及稳定幂等键。任务success/value、事实confidence和用户批准状态分别记录，避免共用一个“重要性”字段。

SQLite可作为派生运行状态数据库，与OpenViking内容库并存；这不要求同一份原文有两个写入权威。若用户正式确认某条推断，那一条确认应成为独立、有出处的内容记录，再由索引/图/agent经验消费。

## 10. 验证记录与证据边界

### 10.1 阅读过的测试（未运行）

| 测试 | 能说明的代码设计 | 不能据此宣称 |
|---|---|---|
| `tests/e2e/v7-full-chain.e2e.test.ts` | fake LLM/fake embedder驱动capture→reward→L2/L3/skill/repair；可选真实LLM smoke被环境开关控制 | 默认模型已能正确归纳所有任务；该环境E2E已通过 |
| `tests/unit/pipeline/lightweight-mode.test.ts` | 检查轻量模式不挂演化订阅、包括恢复event | 开箱自进化开启 |
| `tests/unit/embedding/retry-queue.test.ts` | 两worker lease、到期抢占、stale完成/失败被拒绝 | 全流程具有跨库exactly-once |
| `tests/unit/pipeline/auto-recovery-role.test.ts` | 恢复owner与正常capture分离 | 每个adapter的恢复设置相同 |
| DSH runtime/bridge tests | 借host模型、恢复开关、按准确model route、foreground fail-open/defer capture、dispose drain | 当前用户机器DSH已安装并活跃使用该MemOS插件 |

测试原始证据：[离线full-chain依赖](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/tests/e2e/v7-full-chain.e2e.test.ts#L26-L58)、[轻量模式验证目标](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/tests/unit/pipeline/lightweight-mode.test.ts#L86-L119)、[lease tests](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/tests/unit/embedding/retry-queue.test.ts#L57-L128)、[DSH defaults tests](https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/apps/memos-local-plugin/tests/unit/adapters/deepseek-harness-runtime.test.ts#L19-L82)。

### 10.2 已执行的hermetic SQL探针

环境：项目根已激活venv；Python标准库 `sqlite3` 返回 SQLite **3.53.1**。仅在 `:memory:` 执行固定源码 `001-initial.sql`、`002-embedding-retry-queue.sql`、`003-embedding-retry-lease.sql`，不接真实数据。不使用 better-sqlite3/TypeScript runtime，未执行后续全迁移。

探针源码，可从项目根激活venv后通过 `@'…'@ | python -`执行（路径对应本机只读源码）：

```python
import sqlite3
from pathlib import Path

root = Path(r'D:\python_work\work2026-4\MemOS')
migrations = root / 'apps/memos-local-plugin/core/storage/migrations'
db = sqlite3.connect(':memory:')
db.execute('PRAGMA foreign_keys=ON')
for name in ['001-initial.sql', '002-embedding-retry-queue.sql',
             '003-embedding-retry-lease.sql']:
    db.executescript((migrations / name).read_text(encoding='utf-8'))
db.execute("INSERT INTO sessions(id,agent,started_at,last_seen_at) "
           "VALUES ('s','dsh',1,1)")
db.execute("INSERT INTO episodes(id,session_id,started_at) VALUES ('e','s',1)")
vec = b'\x00\x00\x80\x3f'
db.execute("INSERT INTO traces(id,episode_id,session_id,ts,user_text,"
           "agent_text,summary,vec_summary,turn_id) "
           "VALUES('t','e','s',1,'用户原话','旧正文','旧摘要',?,1)", (vec,))
old = db.execute("SELECT hex(vec_summary) FROM traces WHERE id='t'").fetchone()[0]
# 与已追踪repo.updateBody的body/summary UPDATE等价。
db.execute("UPDATE traces SET agent_text='新正文',summary='新摘要' WHERE id='t'")
new = db.execute("SELECT hex(vec_summary) FROM traces WHERE id='t'").fetchone()[0]
fts = db.execute("SELECT summary FROM traces_fts WHERE trace_id='t'").fetchone()[0]
print(sqlite3.sqlite_version)
print({'fts_tracks_summary_update': fts == '新摘要',
       'body_update_preserves_prior_vector_blob': old == new})
db.close()
```

实际结果：

```json
{
  "sqlite_version": "3.53.1",
  "source_commit": "a7367d07e55db61099f7b4e2c1108bc5831a24f3",
  "fts_tracks_summary_update": true,
  "body_update_preserves_prior_vector_blob": true
}
```

这证明初始SQL触发器行为与已读UPDATE路径的一致性边界，不证明产品全部编辑入口或后台动作已在实际运行中复现。迁移001-003的队列列也实际包含 `source_text/status/attempts/next_attempt_at/claimed_by/lease_until`。研究前后上游 `git status --short`为空，HEAD保持固定。

## 11. 读取范围、未验证项与维护建议

已建立全局目录/manifest图；深入追踪新版 `agent-contract`、config defaults、pipeline bootstrap/deps/orchestrator关键分支、session持久性、capture/normalizer/dedup、reward backprop、L2入口/订阅、feedback接口、retrieval三tier与ranker/injector/scheduler、namespace/查询wrapper、storage schema/repos/migrator/vector/retry queue、导出迁移路径，以及DSH与Hermes bridge边界。旧版重点读入口、capture、worker/dedup、task processor、recall engine、SQLite schema；packages读取其独立入口/import边界。

很多大文件只读取关键段落，**不声称已逐行理解整仓**。目录计数提供规模参照：新版core有228个 `.ts/.py/.sql`文件、约56749行，agent-contract 7文件约1863行、adapters 15文件约10569行（此计数只是静态文件行数，不是复杂度或完成度指标）。

未充分研究/未验证：hub跨机器同步与权限完整性、viewer所有管理入口、遥测发送的完整网络行为、实际LLM输出质量与成本、千万条级性能、所有升级/旧版数据库兼容路径、完整L3合并和长期skill学习效果、真实DSH/OpenClaw/Hermes宿主运行。包声明的许可证需在具体复制文件与分发范围确认，本报告不作法律判断。

长期裁剪建议：先保存固定commit与原测试作为参照；优先重用低宿主耦合的纯策略函数、合同DTO和恢复机制，并以我们的source/hash合同包装。只有当真实使用证明“任务经验能减少重复失败”时，才接完整学习worker。若要持续跟踪新版beta变动，保留少量上游API适配层与迁移测试；不宜同时维护旧OpenClaw、新Local与packages副本三条逻辑。最终产品化时，原文库、工作流状态、派生知识和宿主context适配各有清楚职责，比把它们命名为一个“记忆OS”更能约束实现。
