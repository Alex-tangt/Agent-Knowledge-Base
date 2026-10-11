# OpenViking 0.4.23 源码研究：原文、索引、记忆加工与可复用边界

研究日期：2026-10-11。只读源码基线：`v0.4.23`，提交 `df32bf6e50a40843438f9491a26069ca4bd08f1f`，本地目录 `D:\python_work\work2026-4\OpenViking`。全文上游链接固定到该提交。问题背景来自 [handoff-memory-base.md](../../docs/handoff-memory-base.md) 与 [ADR-0032](../../docs/adr/0032-personal-memory-base-adopt-then-productize.md)：一个人、家用 PC、多个 agent 共享；提交内容不得二次改写；项目仓库只读、版本留在原仓库，基座需知道读到哪个版本。

本轮阅读核心实现、配置、若干测试及本仓薄接入，**没有启动服务、运行上游测试、调用模型、安装依赖或建立 AOCI 索引**。测试以下均指“阅读测试的预期与替身断言”。既有 HTTP 上传、正式 Windows 部署与 Bubble/Codex 验收仍是此前实测证据，不能被这次源码推断替代。主研究者已核对已安装 0.4.23 与固定 clone：704 个共有 Python 文件的归一化文本和 AST 全部一致，安装包仅多构建生成的 `_version.py`；未比较原生二进制。见 [provenance.json](provenance.json)。

## 1. 直接影响架构的结论

1. **OpenViking 已经同时包含存储/索引、会话记忆抽取、记忆编辑和上下文调度。** “OpenViking 是文件系统、MemOS 是学习层”不足以作为拆模块的依据。它的 session commit 有持久归档与异步抽取两阶段，V3 抽取器会产出、合并与编辑记忆；context assembler 还有查询扩展、召回去重、正文预取和 token 预算。[会话两阶段][session-commit]、[V3 抽取][compressor]、[上下文组装][assembler]
2. **原文语义取决于 namespace 和入口。** 普通 resource 的 `content/write + vectors_only` 是可保留输入 UTF-8 文本的窄入口；memory namespace 会经过 `MemoryFileUtils`、资源 URI 链接化及 schema overview 更新，甚至显式忽略 `processing_mode`。因此把 app 原文放在 `resources/...` 是实质性的边界选择，不只是命名习惯。[普通与 memory 写入][write-by-namespace]、[memory 刷新][write-memory]
3. **文件树是内容权威，但整个向量数据库并非可随便丢弃的派生缓存。** ACL 直接与继承权限保存在 context collection；身份、账号、组、运行配置和 watch 也是独立控制状态。只备份正文再重建全部数据库，不能据此保证权限恢复。[R/N/F/V][rnfv]、[ACL][acl]、[身份路径][identity-paths]
4. **当前 `find` 是 QUICK 模式的全局候选检索。** 名为 `HierarchicalRetriever` 的类在此提交实际只做一次受 namespace/路径/过滤条件限制的候选查询，再 URI 去重、可选重排；不是根据 L0 摘要逐层钻目录的检索循环。`find` 固定 QUICK，当前本仓又固定 L2，因此目前不能把目录摘要理解为已生效的导航层。[find][find]、[实际 retrieve][retrieve]、[测试中的一次查询][retrieve-test]
5. **native watch 不等于 HTTP 上传 watch，更不等于外部文件实时映射。** HTTP 明确拒绝宿主文件路径，temp upload 明确拒绝 watch；native ResourceService 和 WatchScheduler 保留本地来源路径的代码路径，但调度动作仍是定时重新导入到内部目标。当前 HTTP 快照新鲜度缺陷仍成立；native 本地 watch 的完整运行行为本轮未验证。[HTTP 限制][http-local]、[上传 watch][http-watch]、[native refresh][native-refresh]、[watch 调度][watch-scheduler]
6. **单独复制 `VikingFS` 不会得到一个独立的纯 Python 虚拟文件系统。** 它是混入权限、语义、向量与快照能力的门面，下方需要 Rust RAGFS binding，旁边依赖请求身份、context store、运行配置、队列和 provider。最小可复用边界应先按能力契约与数据所有权划定。[VikingFS 构造][vikingfs]、[初始化][core-init]、[必须的 binding][binding]

这些事实支持继续利用当前已验证的原文入口，同时先解决来源和权限边界；它们**尚不足以直接决定最终产品底座、是否裁剪原生模块或是否接入 MemOS**。具体选择仍需下面的小探针回答。

## 2. 实际模块图与数据模型

```text
HTTP / SDK / embedded service
          │ RequestContext: account / user / role
          ▼
OpenVikingService / Core
 ├─ FsService ─ ContentWriteCoordinator ─ VikingFS
 ├─ ResourceService ─ ResourceProcessor ─ Parser / TreeBuilder / 更新计划
 ├─ SessionService ─ session 归档 ─ CompressorV3 / ExtractLoop / MemoryUpdater
 ├─ ContextAssembler ─ find / session / RecallLedger / token planner
 ├─ WatchScheduler ─ ResourceService.refresh_resource
 └─ QueueManager
       ├─ AddResource / ExternalParse / Reindex / SessionCommit / ExternalTask
       ├─ SemanticProcessor ─ VLM ─ .abstract.md / .overview.md
       └─ TextEmbeddingHandler ─ account embedding provider ─ context collection

VikingFS
 ├─ AsyncAGFSClient ─ PyO3 ragfs_python ─ Rust RAGFS
 │                                    └─ mount / localfs / queuefs / pathlock / Git
 ├─ VikingVectorIndexBackend / AclManager ─ context collection
 └─ namespace / URI / identity / retrieval / runtime config / provider
```

这是源码调用关系，不是部署进程图。Core 初始化 context collection、运行配置、VLM resolver、VikingFS 和 ResourceProcessor，再注册持久队列消费者；watch scheduler 有单独配置开关。Semantic 与 embedding 队列消费者由 QueueManager 配置。[Core 初始化][core-init]、[队列与 watch 启动][core-queues]、[语义/嵌入消费者][queue-handlers]

RAGFS 的 Rust builder 组合 mount、path lock、统计层，并注册 localfs、memfs、queuefs、kvfs、sqlfs 等插件；Python AsyncAGFSClient 则把同步 binding 调用放到线程中执行。它不是 OS 内核文件系统，而是应用可组合的文件访问/锁/快照层。[Rust builder][ragfs-builder]、[AsyncAGFSClient][async-agfs]

| 对象 | 当前实现与身份 | 架构含义 |
|---|---|---|
| URI | `viking://...` 根据 `account_id` 映射到 `/local/<account>/...`；namespace 决定 resource / memory / skill | URI 是账户中的逻辑地址，不能单凭字符串认定跨账户同一对象。[URI 映射][uri-map]、[namespace][namespace] |
| 正式内容 F | 更新规划明确以内部 formal file tree 判断内容是否存在 | 对**已导入内容**，内部树是正文权威；这不自动使外部项目仓库成为实时映射。[R/N/F/V][rnfv] |
| L0/L1 | 目录 `.abstract.md` / `.overview.md`，包含派生语义与相关结构信息 | 派生文件与原件可以分开，但其更新会调用 VLM；不是原文。[语义生成][overview-generation] |
| L2 | 文件/已解析块的 Context；向量记录包含 URI、level、abstract、md5 等 | 查到记录不等于读到正文，应再按 URI 读取；没有自动为每个长原文建立无限数量的块。[Context][context]、[文件向量化][vectorize-file] |
| record id | `md5(account_id + ':' + seed_uri)`；L0/L1 以 sidecar URI 为 seed，L2 以文件 URI 为 seed | 是地址派生键，**不是内容版本号**；同地址改写可更新同一记录。[vector id][vector-id] |
| md5 | 指向最终写入的 bytes，供增量更新判断 | 是已存内容指纹；不是外部仓库 commit 或未来最新性证明。[R/N/F/V][rnfv]、[向量化 md5][vectorize-file] |
| ACL | context collection 内的 `acl_direct` / `acl_inherited` / mode | 控制权威与检索记录共存，恢复时不能整体当缓存丢弃。[ACL][acl] |
| 身份与调度状态 | `_system/accounts.json`、各 account `_system/users.json/groups.json`；watch 状态另存 | 需要独立备份/迁移契约；正文包不是整个服务的状态。[身份路径][identity-paths]、[watch 存储][watch-storage] |

本地配置的 `/local` mount 通常指向数据目录下的 `viking` 树；LocalFileSystem 将 virtual path 解析到其 base path。本文未穷尽多后端、缓存层、加密层的行为。[mount 配置][mount-config]、[localfs][localfs]

## 3. 原文写入、导入与索引链路

### 3.1 原文直接写入：当前基座使用的接缝

```text
POST content/write (resource, mode=create/replace/append, vectors_only)
 → FsService.write
 → ContentWriteCoordinator
 → URI / 可写性 / 内部文件名校验
 → exact path lock
 → 普通 resource: UTF-8 写入，append 为直接拼接
 → vectorize_file(final md5, summary='')
 → embedding queue → embedding provider → context collection
 → 等待本请求派生工作（若 wait=true）→ 返回 semantic/vector 状态

GET content/read(raw=true)
 → FsService.read → VikingFS.read_file
 → URI / read ACL / 文件 stat 校验 → 读取存储正文
```

resource 的普通文本 write/append 不经 MemoryFileUtils。`vectors_only` 分支只请求文件向量化，跳过 SemanticProcessor；其测试明确断言没有调用语义刷新、summary 为空、md5 是最终输入字节指纹、semantic status 为 skipped。[写入 coordinator][write-direct]、[普通拼接][write-by-namespace]、[processing_mode 测试][write-test]、[正文读写][raw-io]

但是一次写入并不是“正文、ACL、向量、全部 sidecar 同事务提交”：正文先写，后续 work 入队/等待；源码回滚仅覆盖某些派生处理尚未启动的失败。正文已存在而向量未就绪是必须表达的状态。[写入和失败处理][write-direct]

`vectorize_file` 读取文本/摘要并按 embedding token 预算截断后，形成一个文件 Context 和 embedding message；本轮看到的函数没有把整个长原文再自动展开为多个 L2 records。因此“全文可读回”与“全文都进入 embedding”是两个判据。资源导入可以先解析成块，但当前 raw capture 入口没有因此自动获得解析分块。[文件向量化][vectorize-file]

### 3.2 资源导入：解析产物，不应默认等同原件

```text
ResourceService.add_resource / refresh_resource
 → 持久 ingestion task / AddResource queue
 → ResourceProcessor._process
 → 根据来源/类型调用 parser → 临时 artifact
 → TreeBuilder.finalize_from_temp：确定目标 URI、layout、移动/合并产物
 → R: 请求意图、N: 新 artifact、F: 正式内容树、V: 向量记录
 → ResourceDiff / ContextUpdatePlan
 → 更新正式文件与清理无效索引
 → 按 processing_mode 请求 Semantic / Embedding 工作
```

解析结果进入内部树，而不是挂接一个“每次 read 都指回源 repo”的引用。R/N/F/V 把内容变化与索引健康分成两个轴；不完整遍历会阻止按缺失路径删除，这是可以学习的更新安全机制。[ingestion finalize][ingestion]、[R/N/F/V][rnfv]

`no_split` 的契约只是控制 parsed Markdown 是否拆成多个文件。Markdown parser 会计算 layout，按配置重写链接、导入本地图片；其他类型会转换为可检索文本。不能用“no_split + vectors_only”推导所有资源类型都原样保留输入 bytes。需要逐类型验证；已经生成的 app 总结优先走上一节的普通文本入口。[parse_mode][parse-mode]、[Markdown parser][markdown-parser]

### 3.3 语义派生、更新与删除

SemanticProcessor 从文件摘要和子目录摘要生成目录 overview，调用 VLM，再产生 sidecars 和目录向量。小目录 overview 的异常分支返回“overview 未生成”的占位正文；队列处理完成与产物语义成功不是同一件事，不能只凭 completed 标志通过验收。[overview 生成与异常][overview-generation] 本仓此前故障实测见 [m4_failure.md](../memory-framework-spike/mvp/evidence/m4_failure.md)，本轮未重跑。

父目录 freshness policy 累积子项变化次数，对大目录可标记 pending，达到比例阈值才重新聚合。这是**内部摘要刷新策略**，不能解决 repo 外部更新的来源新鲜度。[freshness policy][freshness]

普通 replace/append 可更新同 URI 的正文与记录；重新导入则走 R/N/F/V 更新计划。删除通过 `VikingFS.rm` 获得 exact/tree lock、收集影响 URI、**先删向量记录再删文件**；FsService 之后同步 watch 状态、刷新父目录并清理 memory-resource links。源码顺序显示是多个有状态步骤，本轮没有验证它们在掉电或部分异常时的整体恢复。[底层删除][rm]、[上层删除后续][rm-service]

`check_index_consistency` 检查当前文件树预期的 URI/level 是否有向量记录；所读实现本身没有比较外部 repo HEAD/mtime/content hash。因此存在性一致不能代替来源新鲜度验收。[index consistency][index-consistency]

### 3.4 队列持久性与重启补偿的具体范围

OpenViking 构造 queuefs 插件时默认选择 SQLite，数据库通常位于数据根 `_system/queue/queue.db`；Rust QueueFS 本身还支持 memory/cache 等 backend，不能脱离实际配置笼统承诺持久性。SQLite 后端存 pending/processing 状态，启动时把符合 `recover_stale_sec` 的 processing 消息重置为 pending。[OpenViking queue 配置][queue-config]、[SQLite 持久与恢复][queue-sqlite]

NamedQueue 采用 at-least-once 流程：先 dequeue 标记 processing，handler 返回后 ack 删除。handler 抛异常则不 ack，重启可重投；**handler 返回已处理错误也会 ack**，不是所有业务失败都会无限自动重试。Core 在消费者启动前从 queue snapshots 重建 task work，并恢复 tracked tasks。[dequeue/ack][queue-delivery]、[task tracking 重建][queue-tracking]

因此现成队列可复用，但仍需按具体任务验证幂等、返回错误的终态、重投副作用、故障中间态和配置。SQLite 当前使用 WAL 与 `synchronous=NORMAL`，本轮没有掉电持久性测试，也没有证明已写正文/权限/全部队列能作为整体恢复。snapshot 和 OVPack 同样不是这份 queue DB 的完整备份合同。[SQLite 设置][queue-sqlite]

## 4. 检索、读回与上下文组装

```text
find(query, target_uri, level, filter)
 → _SemanticMixin.find → TypedQuery → RetrieverMode.QUICK
 → query embedding 一次
 → tenant + namespace / target scope / level / filter 受限的全局查询一次
 → URI 去重 → 阈值 / 结果排序 → resources / memories / skills 分组

search(query, session)
 → 可选会话意图分析 / LLM 查询计划
 → 一个或多个 TypedQuery → 同一 Retriever
 → 根据配置可重排 → 合并结果

context assembly
 → 可选 session 查询扩展
 → find 查询（embedding cache）/ 候选 gather
 → RecallLedger 控制短期重复召回
 → 正文 prefetch → token budget plan → render
 → 可选 digest rewrite
```

关键事实是 `_SemanticMixin.find` 显式传 QUICK；`HierarchicalRetriever.retrieve` 根据 mode 决定重排，查询 vector store 一次，重排时送的是候选 `abstract`。官方替身测试也断言只查询一次，且候选 abstract 被送往 rerank。类名与概念文档不能代替当前调用链。[find][find]、[retrieve][retrieve]、[retrieve 测试][retrieve-test] `search` 的会话意图分析路径及 context assembly 另外持有 VLM 依赖。[search 意图][search-intent]、[assembler][assembler]

L0/L1 的 URI 在返回时会还原 sidecar 后缀；L2 的普通 Markdown frontmatter 特意保持不动。`abstract` 是结果预览字段，`get/read` 才是正文读取契约。[结果转换][result-conversion]

对本仓的实际含义：目前选择 `find + level=2 + capture tag`，避免引入意图生成和目录摘要成本；得到的是 raw capture 的受限检索，不是 OpenViking 所有 memory/context 能力。这一选择与“先存储和检索”的 MVP 契约一致，但需另测长文本召回、关键词查找、时间/类型范围和新 agent 首次加载。

## 5. OpenViking 自己的记忆抽取与生命周期

`session.commit_async` 首先在 path lock 下把会话分为归档与保留段，持久化恢复意图和 raw archive，再持久入队 Phase 2；第二阶段才做 memory extraction。它既保存会话证据，又维护抽取任务及恢复状态，不能直接当成一个“给原文打 embedding”的函数。[session commit][session-commit]

V3 `extract_long_term_memories` 从消息提取用户 memories，按配置筛选 agent evolution memory types，处理 cases、训练/轨迹或 session skills，再写最终 memory diff。ExtractLoop 依赖 memory schemas、可用工具、预取上下文、VLM、VikingFS 与身份隔离处理；MemoryUpdater 把 operations 应用到存储。它已有生成、patch/编辑、删除、引用维护和派生刷新等职责。[V3 抽取][compressor]、[ExtractLoop][extract-loop]、[MemoryUpdater][memory-updater]

memory 原文保存不是普通 resource 的原样写：写入会重新序列化 memory fields trailer，并把裸资源 URI 链接化；memory write 的 `processing_mode` 被忽略，随后固定刷新 schema overview 与 memory embedding。即使没有 LLM 调用，也可能改变原始输入的存储字节。[namespace 写入][write-by-namespace]、[memory serializer][memory-serializer]、[资源引用][resource-refs]、[memory 刷新][write-memory]

因此可采用的分工是：**已确认的用户/应用提交作为证据 resource；画像、偏好、合并事实等存入单独派生空间，并保留证据来源关系。** 这是契约建议，不是说现有 memory namespace 自动满足本仓全部派生授权与版本要求。资源 URI 引用提供连接点，但不是强制的不可变版本引用；需要附 `content hash / source revision / extraction version` 的产品契约。

可供拆分研究的真实接缝是 `ExtractContextProvider`：它规定指令、prefetch、tools、memory schemas，能分别服务 session extraction 与 consolidation。但现有 ExtractLoop/Updater 仍穿透到 VikingFS、身份与 schema registry，单拷接口不会完成后端解耦。若日后提取，宜先引入“只读证据访问”和“受限派生写入”端口，保留同一组外部行为测试。[context provider][extract-provider]、[ExtractLoop][extract-loop]、[MemoryUpdater][memory-updater]

生命周期范围要谨慎：例如 `memory_lifecycle.py` 中可见 active count 与时间衰减的 hotness 计算，并用于相关统计；它不是证明自动遗忘/清理全部记忆的完整机制。未系统审计全量 consolidation、agent evolution、冲突推理与遗忘策略，不能据此宣称这些能力成熟或适合我们的数据语义。[hotness][memory-lifecycle]

## 6. 来源更新、权限与恢复

### 6.1 watch 能补哪些，不能替代哪些

| 入口/来源 | 固定源码事实 | 对本仓的解释 |
|---|---|---|
| HTTP 直接本地 path | guard 拒绝宿主文件路径，资源路由关闭 local path resolution | 此前 HTTP 本地路径失败仍有效；不是缺少一个传参开关。[HTTP guard][http-local]、[HTTP resource route][http-watch] |
| HTTP temp upload | `watch_interval>0` 明确报 static snapshot 错误 | 上传已冻结；定时处理同一上传不会看到 repo 更新。[HTTP upload watch][http-watch] |
| native ResourceService | `add/refresh_resource` 本地路径解析默认开启；watch 创建要求稳定 to URI | 代码存在本地来源方案，但本轮未执行 SDK/embedded 本地 watch E2E。[native refresh][native-refresh]、[watch 创建][watch-create] |
| WatchScheduler | 对非远程路径使用 `Path.exists()`；定时调用 refresh_resource(original path, stable target) | 是“来源重新导入”，不是 read-through 映射；需要观察 refresh 失败与停止期间的 stale 状态。[watch scheduler][watch-scheduler]、[本地存在性检查][watch-local] |
| 直接 Rust mount | LocalFileSystem 能映射 base directory，并有普通写/删/rename 能力 | 不能据此承诺只读来源；本轮未发现该 LocalFileSystem 构造中的独立 read-only 开关，未完成全栈只读策略审计。[localfs][localfs] |

ADR-0032 D7 要求项目文件不复制、不提交，版本留在各 repo。native watch 的“定时导入内部副本”至多解决某部分快照追平，**没有因此满足 D7**。要坚持该契约，需要来源 registry/resolver：检索记录保存 source URI 与读到的指纹；get 从被授权的只读来源取正文，并对索引版本与当前正文版本的差异作显式处理。若决定允许内部派生副本，需要用户明确改变契约，而不是把副本叫映射。

### 6.2 权限不是完整 POSIX 模型，也不能当可重建缓存

ACL 的显式作用范围为 `viking://resources`；user/agent 等空间另有 namespace/owner 规则。缺少 account ACL setting 时 `AclManager.is_enabled` 为 False；resource admin 有隐式 manage，不能拿 admin 客户端的成功读写作为受限 agent 的隔离验证。[ACL][acl]、[访问检查][access]

ACL 记录直接在 context collection 中；身份注册表在 `_system` 文件路径，root key 等部署配置也有自己的管理面。OVPack 可携带的 scalar 白名单只有 URI/type/context_type/level/name/description/tags/abstract/md5，未包含 ACL。由此得出的限定结论是：**OVPack 不是“正文 + 全部安全状态”的一键恢复包**，不能按它定义全量服务备份。[身份路径][identity-paths]、[OVPack scalar][ovpack-scalars]

当前 memory_base 的宿主身份与 writer/reader 限制由 gateway 执行，upstream 连接另有自己的服务身份。现有 gateway 只暴露有限 capture 工具，这是一道实际边界；将来扩大映射范围或引入派生 worker 时，必须继续明确哪些请求使用哪个身份以及写入哪些空间，不能直接把全权上游 API 交给每个 agent。[当前 gateway](../../memory_base/gateway.py#L164)

### 6.3 快照、导出和恢复的不同契约

Snapshot API 调用 Rust Git 层，以账户内部树做提交；commit 是显式操作，本轮所读 content write 路径没有逐次自动调用它。恢复会把指定 subtree 的差异写回 VFS，并创建以当前 HEAD 为 parent 的新 commit；之后后台进行向量维护，以 task id 追踪。它不是“正文/ACL/向量/服务身份全部同时切换”的事务恢复。[snapshot commit][snapshot-commit]、[restore][snapshot-restore]

OVPack 的 backup 只枚举 public scopes `resources`、`user`，过滤 `.relations.json` 和 watch task control 文件；可按配置带向量并校验预期 records，也可在 import 时重建/恢复向量。账号/组/凭据、运行配置、watch 控制状态和 Git 历史没有因此变成完整迁移包。[OVPack scopes][ovpack-policy]、[backup 枚举][ovpack-backup]、[OVPack import/export][ovpack-operations]

本仓此前 [m5_backup.md](../memory-framework-spike/mvp/evidence/m5_backup.md)、[RECOVERY.md](../memory-framework-spike/mvp/RECOVERY.md) 的明确恢复范围应继续保留。下一轮完整备份合同至少区分：① 虚拟提交原件和请求追溯，② 外部 repo 的来源登记/已观察版本（repo 本身仍独立管理），③ 身份/授权与配置，④ 可重建的 embedding/语义结果，⑤ 尚未完成且需要恢复的任务。④ 可重算不代表②③⑤可丢。

## 7. 当前 memory_base 已做了什么，缺什么

| 能力 | 本仓当前实际入口 | 状态与可复用方向 |
|---|---|---|
| app 总结原样写入和读回 | `resources` capture，create + vectors_only；read raw=true | 已有窄合同与真实宿主验收，应保留。[upstream](../../memory_base/upstream.py#L84)、[既有实测](../memory-base-live/README.md) |
| 追加为主与幂等 | principal + request_id 得 capture id；先保留完整请求 metadata，再 create 正文；请求冲突返回 409 | 已有；其 mutex 明确建立在单 gateway process 部署上，不是多进程写入协议。[gateway](../../memory_base/gateway.py#L164) |
| 派生失败仍保住原件 | read-back 确认已写正文；返回 index pending；重试触发 reindex | 已有，不要改成 embedding 超时就把已保存原文当失败丢弃。[gateway](../../memory_base/gateway.py#L202) |
| 当前检索与读取 | find，L2，capture tag，固定 capture scope；get 返回 raw | 已有；empty abstract 回退取正文前 512 字，属于展示摘取，非 LLM 二次总结。[upstream](../../memory_base/upstream.py#L110)、[gateway](../../memory_base/gateway.py#L231) |
| 多 agent 共享 | HTTP/MCP gateway，同一 OpenViking 与外部串行嵌入服务 | Windows Bubble/pi 写、Codex 读、完整重启保持 URI/正文 hash 已通过；DSH/opencode 实际会话尚未验证。[验收](../memory-base-live/README.md) |
| 项目只读映射与新鲜度 | 当前 capture service 没有 source resolver/registry；上游 HTTP 上传是快照 | 首要缺口；native watch 可作为隔离比较对象，但不替代 D7 的严格映射契约。[ADR-0032](../../docs/adr/0032-personal-memory-base-adopt-then-productize.md#L40) |
| 范围权限 | gateway writer/reader；固定单一 capture scope | 已有基本角色门槛；未等同于跨项目/来源 scope policy、派生 worker 权限或上游 ACL 完整恢复 |
| 日期/来源类型路径、启动画像 | 当前 hash capture path；所读三工具入口没有对应范围/首次加载合同 | 后续产品接入层需补，避免把它们误报为现成 URI 即可满足的需求。[需求](../../docs/handoff-memory-base.md#L49) |
| 语义记忆/上下文调度 | 当前没有开启上游 session extraction/context assembly | 上游已有候选能力，需验证后择取；不必先因“缺学习层”接入第二个全量产品 |
| 完整备份与迁移 | 既有 spike 明确部分恢复；正式重启不是异盘恢复 | 下一轮需按权威状态分类做恢复探针；不能以 OVPack 有 export/import 宣称全部完成 |

本轮源代码和此前验收证明了“当前窄入口可用”，没有证明阶段 1 的所有完成条件已满足。尤其来源新鲜度、范围授权与完整恢复仍应单列。

## 8. 模块提取的工程可行性

| 边界 | 能复用的内容 | 实际依赖/代价 | 本轮建议 |
|---|---|---|---|
| 固定服务 + HTTP adapter | 原文 resource create/read、find/reindex、现成存储与锁 | OpenViking 作为部署依赖；升级与数据格式契约需固定 | 最小的安全复用候选，延续已验证的 memory_base 接缝 |
| Rust RAGFS | filesystem trait、mount、localfs、queuefs、pathlock、Git 基础能力 | Rust native build、PyO3/异步桥接、插件配置与快照语义；向量与权限不自动获得 | 适合后续专门研究底层存储，不宜当作拷贝几份 Python 文件的裁剪 |
| VikingFS 单独提取 | read/ls/write/search/snapshot 门面 | 7 mixins、native binding、identity/namespace、ACL/context backend、retrieval、config/provider 与单例耦合 | **不能只复制 VikingFS 就独立使用**；先列依赖与新接口合同 |
| parser/更新计划 | parse artifact、layout、R/N/F/V diff、安全删除与增量更新思路 | 原文转换/重写规则、临时 artifact backend、URI 移动与内部树 | 可按文档类型独立验证；原件保留须另外建立，不直接承诺 no_split 等于 raw |
| 纯策略/格式 helper | vector id、scope 表达式、部分 freshness/token 规划、格式读写 | 小模块仍可能引用全局配置/CLI 类型；格式将成为迁移责任 | 提取可行性相对高，但先明确格式兼容与许可范围，不能仅按文件小判断 |
| ExtractLoop / MemoryUpdater | schemas、tool-driven extraction、patch/edit、引用维护、diff | VLM、VikingFS/单例、RequestContext、registry、隔离 handler、派生刷新、队列 | 先试只读证据输入与受限派生输出；避免给抽取器原件编辑权限 |
| ContextAssembler | expansion、recall ledger、prefetch、token budget、render | session 状态、VLM 可选路径、find contract、正文加载与历史冷却文件 | 可先关闭扩展/改写，用固定 query 与预算比较；无需先整套启用 |

Python binding 当前在 `create_agfs_client` 中不可用时直接抛 ImportError；不是自动换用一个纯 Python 实现。`ragfs-python` manifest 包含 Rust 1.91.1、PyO3 0.27/abi3-py310、Tokio，默认还有 s3 feature；本地向量引擎亦有原生 backend 装载器。裁剪需承担 native 打包、跨 OS 验证和上游升级责任，而不只是降低 Python 包数量。[binding][binding]、[Rust binding manifest][ragfs-python]、[vector native loader][vector-engine]

许可仅记元数据事实：root `pyproject.toml` 标为 AGPL-3.0，许多 Python 文件头也是 AGPL-3.0；`crates/ragfs/Cargo.toml` 标为 Apache-2.0。这不构成“整个 VikingFS 或所有依赖都可按 Apache 单独取走”的结论；提取范围与第三方文件需单独核对。本文不重做 ADR-0032 D8 的法律判断。[root metadata][license-root]、[RAGFS metadata][license-ragfs]

## 9. 最小安全复用/改造边界与下一步探针

下面是针对本仓的**待验证建议**，不是最终架构决策：

- 保留 `resource create/read + vectors_only + find L2` 的当前契约，以及外部串行 embedding。主对话模型/派生 worker 可以变化，原件与请求追溯先保持稳定。
- 在本仓掌握统一 URI/证据合同：virtual capture 的正文 hash；mapped source 的 allowlist、root/path、观察时间、内容指纹与 repo revision；search/get 明确返回所见版本及 stale/missing/unavailable。当前 URI 稳定不等于来源新鲜。
- 以 source resolver 为下一项关键探索：先在 hermetic repo 比较“HTTP snapshot + 显式刷新”“native local watch”“严格只读 read-through + 可重建索引”三种有限路径。后两者都未在本轮运行证明。重点判据是 D7、删除/重命名/离线、索引与 get 版本不一致时的行为。
- 派生功能先通过受限端口试用：读取证据，写入独立派生对象；每次输出携带 evidence URI + hash/revision、模型/策略版本和状态。OpenViking 自身 extraction/context assembly 与 MemOS 的候选模块应以同一证据集比较，再决定复用哪一部分。
- 独立列出权限/恢复合同：同一正式实例服务多个 agent 并不要求只有一个 agent 能写；当前单 gateway 与文件 path lock 是具体并发策略。需要保证访问策略与请求追溯统一，不能从“单一真相”直接推出永远只有单个写客户端或一个写线程。

建议优先执行的小探针：

| 探针 | 隔离条件 | 能回答的问题 / 最小通过标准 |
|---|---|---|
| 来源变更 | 临时 git repo、假 embedding、独立实例数据目录 | 写后外部改/删/rename；搜索与 get 返回所见 hash/version，离线不得误删或伪报最新；是否保持来源 bytes 与 git 状态不变 |
| 原文语义 | 同一含 YAML、裸 Viking URI、尾部换行、长文本的 fixture | resource raw write、memory write、no_split ingest 分别比较 bytes；揭示入口差异；长文本尾段召回单列，不以 get 成功代替 |
| 受限身份 | 隔离账号/ACL、独立 worker 凭据 | direct read、search、snapshot、导出均不能跨范围；派生 worker 不可 replace/delete 证据；权限关闭/缺失的行为显式 |
| 部分失败与恢复 | 假 provider 失败、临时恢复目标 | 正文持久但 index pending 可识别并重试；恢复后先核权限和正文，再核派生与 pending task，不把“重启成功”当恢复成功 |
| 预算调度 | 固定证据集、先无 LLM 扩展/改写 | 比较原 find+get 与 ContextAssembler 的覆盖率、重复证据、token 用量；性能只在实际本机同条件测量 |

这些探针可以使用源码和少量依赖查询逐步推进；当前已定位主要边界，不需要把全库 AOCI 处理作为开始实施的前置。后续进入具体模块裁剪时再用 codegraph/AOCI 帮助补依赖闭包，仍需源码和行为验证。

## 10. 阅读范围与未验证点

主要跟踪的实现组：

- Core/服务初始化；VikingFS `_access/_ops/_semantic/_snapshot`；content write coordinator；resource processor / TreeBuilder / Markdown parser / R/N/F/V / index consistency。
- QueueManager/NamedQueue、Rust queuefs SQLite 状态与恢复、semantic processor 与 embedding utils；Context/namespace/vector id；HierarchicalRetriever、intent 路径、context assembler pipeline/ledger。
- Session commit、CompressorV3、ExtractLoop、MemoryUpdater、ExtractContextProvider、memory serializer/resource refs/hotness。
- ResourceService/watch scheduler/manager/storage、HTTP local input guard；ACL；OVPack policy/index/operations；身份注册表存储路径。
- Rust filesystem trait/builder/localfs 与 binding manifests；Python binding/client 与 native vector loader；本仓 upstream/gateway/ADR/交接与既有验收。

阅读相关测试包括 `test_content_write_processing_mode.py`、content write service/API tests、resource service watch tests、hierarchical retriever rerank tests；只追相关断言与替身，不宣称全量覆盖或执行通过。

未做/未系统读完：所有 parser 的 bytes 保真、所有 connector/OAuth、完整 agent evolution/consolidation、memory graph/冲突与遗忘机制、加密、多租户 LDAP/OIDC、所有后端 plugin 和向量引擎内部算法、掉电一致性、native SDK 本地 watch E2E、跨进程完整并发与恢复、打包许可闭包、POSIX 部署性能。原生二进制与固定 Rust/C++ 源码的构建来源未核对。本轮没有性能测量，也没有验证任何新接缝可运行。

## 固定源码证据索引

[session-commit]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/session.py#L1233-L1285
[compressor]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/compressor_v3.py#L419-L525
[assembler]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/retrieve/context_assembler/pipeline.py#L54-L176
[write-by-namespace]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/content_write.py#L1238-L1275
[write-memory]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/content_write.py#L1355-L1415
[rnfv]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/resource_diff.py#L3-L109
[acl]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/acl.py#L256-L322
[identity-paths]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/server/api_keys/legacy.py#L44-L46
[find]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_semantic.py#L285-L319
[retrieve]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/retrieve/hierarchical_retriever.py#L100-L235
[retrieve-test]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/tests/retrieve/test_hierarchical_retriever_rerank.py#L147-L163
[http-local]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/server/local_input_guard.py#L58-L75
[http-watch]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/server/routers/resources.py#L267-L279
[native-refresh]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/service/resource_service.py#L1570-L1613
[watch-scheduler]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/resource/watch_scheduler.py#L415-L434
[vikingfs]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/__init__.py#L110-L164
[core-init]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/service/core.py#L473-L525
[binding]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/utils/agfs_utils.py#L554-L599
[core-queues]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/service/core.py#L602-L649
[queue-handlers]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/queuefs/queue_manager.py#L176-L217
[ragfs-builder]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/crates/ragfs/src/core/builder.rs#L1-L150
[async-agfs]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/pyagfs/async_client.py#L83-L110
[uri-map]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_access.py#L687-L702
[namespace]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/core/namespace.py#L14-L20
[overview-generation]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/queuefs/semantic_processor.py#L1645-L1682
[context]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/core/context.py#L27-L140
[vectorize-file]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/utils/embedding_utils.py#L583-L769
[vector-id]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/vector_ids.py#L3-L60
[watch-storage]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/resource/watch_storage.py#L1-L30
[mount-config]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/utils/agfs_utils.py#L278-L334
[localfs]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/crates/ragfs/src/plugins/localfs/mod.rs#L89-L145
[write-direct]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/content_write.py#L846-L1001
[write-test]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/tests/storage/test_content_write_processing_mode.py#L94-L123
[raw-io]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_ops.py#L1962-L2052
[ingestion]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/utils/resource_processor.py#L828-L925
[parse-mode]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/parse/mode.py#L11-L26
[markdown-parser]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/parse/parsers/markdown.py#L298-L351
[freshness]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/queuefs/semantic_ops/freshness_policy.py#L28-L62
[rm]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_ops.py#L215-L348
[rm-service]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/service/fs_service.py#L453-L525
[index-consistency]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/index_consistency.py#L205-L228
[search-intent]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_semantic.py#L471-L556
[result-conversion]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/retrieve/hierarchical_retriever.py#L320-L374
[extract-loop]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/memory/extract_loop.py#L117-L290
[memory-updater]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/memory/memory_updater.py#L763-L919
[memory-serializer]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/memory/utils/memory_file_utils.py#L74-L129
[resource-refs]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/memory/utils/resource_refs.py#L38-L66
[extract-provider]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/session/memory/core.py#L17-L75
[memory-lifecycle]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/retrieve/memory_lifecycle.py#L1-L63
[watch-create]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/service/resource_service.py#L397-L432
[watch-local]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/resource/watch_scheduler.py#L613-L631
[access]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_access.py#L169-L270
[ovpack-scalars]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/ovpack/index.py#L22-L40
[snapshot-commit]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_snapshot.py#L220-L272
[snapshot-restore]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/viking_fs/_snapshot.py#L336-L383
[ovpack-policy]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/ovpack/policy.py#L21-L28
[ovpack-backup]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/ovpack/operations.py#L382-L415
[ovpack-operations]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/ovpack/operations.py#L262-L377
[ragfs-python]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/crates/ragfs-python/Cargo.toml#L1-L32
[vector-engine]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/vectordb/engine/__init__.py#L19-L210
[license-root]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/pyproject.toml#L11-L26
[license-ragfs]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/crates/ragfs/Cargo.toml#L1-L9
[queue-config]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/utils/agfs_utils.py#L248-L274
[queue-sqlite]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/crates/ragfs/src/plugins/queuefs/backend.rs#L404-L516
[queue-delivery]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/queuefs/named_queue.py#L174-L248
[queue-tracking]: https://github.com/volcengine/OpenViking/blob/df32bf6e50a40843438f9491a26069ca4bd08f1f/openviking/storage/queuefs/queue_manager.py#L169-L174
