# MemOS Python 主实现源码研究：权威原文与语义记忆的边界

研究日期：2026-10-11。对象：MemTensor/MemOS 的 Python 主实现 src/memos，固定 commit **a7367d07e55db61099f7b4e2c1108bc5831a24f3**（本地 HEAD 已核对，包版本 2.0.33）。本文不覆盖 apps/memos-local 的独立 TypeScript 产品，也不把云服务能力、宣传榜单或路线图视为本地 Python 包已经交付的能力。

目标需求以本仓 docs/handoff-memory-base.md 的 S1–S3 为准：多种 agent 共享一个人的记忆；项目文件映射与虚拟条目同一寻址空间；Bubble/pi 已产出的总结原样保存，LLM 派生结论另存；家用 PC、千级数据；部署/真实宿主 capture 链路已经验收，下一步明确来源、检索范围和恢复边界。本文只给源码层架构判断，未执行真实模型、数据库或完整 SDK 集成测试。

## 1. 结论

**MemOS Python 的核心长处是把输入加工成可整理、可检索、可反馈更新的语义记忆；它不天然提供我们的“不可改写原文权威层”。** Tree 路线把记忆正文、来源、向量、生命周期状态都放入图存储，再用 reader / reorganizer / feedback 改变内容及其可见状态。它的图不是只存关系的只读附属索引。[数据模型][item]、[图写入][neo-write]、[合并与归档][merge]

但“MemOS 所有 add 都必须经过 LLM”也不成立。NaiveTextMemory 与 GeneralTextMemory 可以直接接收 TextualMemoryItem；General 的 add 只做 embedding 和向量 payload 写入，get 返回该 payload 的正文，extract 是单独的方法。MOSCore 对非 tree_text 的 messages / memory_content 也直接构造条目。**调用时不使用提炼 LLM，与导入/初始化不依赖 LLM SDK，是两个不同问题。** 后者在当前包里没有自然分离：MOSCore、reader 及 General 构造器仍创建 LLM 对象，包顶层导入 MOS/Cube/Scheduler。[General 直接存取][general]、[MOSCore 写入分支][core-add]、[顶层导入][init]、[核心初始化][core-init]

**建议先保留本项目的原文持久层与三工具边界，把 MemOS 当作后续语义派生层的参考或受控 worker。** 第一批可选能力是来源字段、提炼提示词/结果协议、反馈变更提案、检索策略；不建议为 Bubble 原样存取移植 MOS + Tree + Scheduler 全栈。以后可以让图是原文库的派生视图，但需要重定义删除、更正、版本和重建边界，不能仅把 graph provider 换成文件系统。[产品 API 装配][api-init]、[原始 fast 条目清理][fast-delete]

## 2. 研究方法与范围

先读上游 AGENTS.md；本轮是只读研究，没有安装依赖、format、修改上游、创建索引或执行 AOCI。使用 rg、带行号的源码读取，追踪以下实际调用链；另做一个只依赖 Python 标准库的 AST 方法探针。上游仓库研究结束时 git status --short 为空。

已深入阅读/交叉定位：

- mem_os/core.py、main.py 的构造、注册、add/search/get/update/dump/load 路径；mem_cube/general.py 的初始化与导入导出。
- memories/factory.py；textual/item.py、naive.py、general.py、tree.py、simple_tree.py、preference.py、simple_preference.py；preference adder 的 LLM 更新判断。
- mem_reader/simple_struct.py、multi_modal_struct.py 的构造、fast/fine、来源、版本 hook、RawFileMemory 路径。
- tree_text_memory/organize 的 manager、reorganizer、handler；retrieve 的 searcher、advanced_searcher 及 provider 入口。检索算法未逐个 backend 做端到端测试。
- graph_dbs/factory.py、neo4j.py、neo4j_community.py、postgres.py；vec_dbs/factory.py、qdrant.py；embedders/factory.py、universal_api.py。PolarDB / Milvus 只定位注册和接口，未深入其全部实现。
- api/server_api.py、routers/server_router.py、handlers/component_init.py / add_handler.py、config.py / product_models.py；multi_mem_cube/single_cube.py。
- mem_scheduler 的 base/general scheduler、queue 选择、local/redis queue、mem_read_handler；反馈 feedback.py；plugins/hooks.py / hook_defs.py 和注册搜索。
- activation/kv.py、parametric/lora.py；相关单元测试与 docs/en/open_source 的架构/overview/parametric 文档。没有采信性能排名或 benchmark 作为本项目选型依据。

这是有边界的代码研究，不是“全仓所有路径已经运行验证”。下文“实现”指在固定 commit 看到实际代码，“推断/建议”指依据这些代码对我们需求作出的判断。

## 3. 真正的模块关系：两个入口，不是一条统一链

### 3.1 SDK：MOS / MOSCore → GeneralMemCube

MOS 在 MOSCore 上增加 chat / PRO 等编排。MOSCore 默认创建聊天 LLM、MemReader、SQLite UserManager，scheduler 是可选功能；GeneralMemCube 再按配置通过 MemoryFactory 创建 text / activation / parametric / preference 容器。Cube 是聚合与封装边界，不是一个通用文件系统或单一可移植数据库。[MOSCore 初始化][core-init]、[Cube 初始化][cube]、[memory factory][mem-factory]

SDK 的主要写入分叉：

~~~text
MOSCore.add(messages 或 memory_content)
  ├─ backend != tree_text → TextualMemoryItem(memory=输入正文) → text_mem.add
  └─ backend == tree_text → MemReader.get_memory(fast/fine) → text_mem.add
       └─ async + scheduler → MEM_READ 后台细化
MOSCore.add(doc_path) → 文档枚举 → MemReader → text_mem.add
~~~

非 tree_text 的直接写入是现有代码能力；doc_path 路线仍经过 reader。项目文件枚举不等同于我们已有只读来源注册表、外部文件变更指纹、孤儿保护和派生索引追平机制，相关语义需要我们自己保留。[SDK add][core-add]、[文档枚举][documents]

### 3.2 Product REST API：独立装配的 SimpleTree 路线

实际服务文件是 api/server_api.py，router 前缀为 /product。router 模块导入时执行 init_server；这个工厂直接创建 graph DB、多个 LLM、embedder、reader、reranker、manager、searcher、feedback、scheduler，随后构造 **NaiveMemCube + SimpleTreeTextMemory**，没有走 MOSCore / GeneralMemCube 的同一装配流程。关闭聊天 API 或 scheduler.start，不代表这些对象都不初始化。[服务入口][api-server]、[router 初始化][api-router]、[产品装配][api-init]

因此不能从 SDK GeneralTextMemory 支持 raw add，直接推出 Product /product/add 有“完全透明、无框架依赖的原文库模式”。实际 Product 写入经过 SingleCubeView 与 reader：async 默认先 fast，sync 可以选择 fast，否则 fine。SimpleTreeTextMemory 的名字里的 simple 指接收预构造对象，并未自动消除图、检索器、管理器和 reader 的依赖。[API 请求默认值][api-request]、[SingleCube 写入][single-add]、[SimpleTree 构造][simple-tree]

## 4. 不同 text backend 的权威与成本

| backend | 主存储与读回 | 直接 add 是否提炼 | 持久性 / 生命周期 | 对我们用途的含义 |
|---|---|---|---|---|
| naive_text | 进程内列表；get/search 从列表读 | 否；extract 单列 | 显式 JSON dump/load；add 不自动落盘；update 覆盖 | 可理解协议或做 fake，不适合直接当可部署持久基座 |
| general_text | 向量库 payload 就是完整 memory 条目 | 否，但写入/搜索需要 embedding | add/update 直接写 provider；显式导出含向量；无自动版本历史 | 可做最小 raw+vector 路线；必须补权威原文/来源刷新/幂等边界 |
| tree_text | graph 存正文、metadata、来源和状态；可集成外部向量库 | Tree.add 自身不提炼；调用方 reader 通常提炼 | manager/reorganizer 改结构、归档、清理；dump 默认不含 embedding | 是可演化的语义记忆状态库，不是透明文件层 |
| simple_tree_text | 继承 Tree，构造器接收已初始化依赖 | 取决于调用方；Product 经过 reader | 继承 Tree 语义 | 是产品装配接缝，不是一个独立轻量后端 |
| pref_text / simple_pref_text | 专用 extractor/adder/retriever 与向量 collections | add 的内部 upsert / 去重判定可调用 LLM | activated 过滤、偏好更新；若干通用 get/update 接口未实现 | 专用偏好模型，可选后续能力；不应承担所有原文存储 |

依据：[Naive][naive]、[General][general]、[Tree 构造/管理器][tree-init]、[Tree get][tree-update]、[Tree export][tree-export]、[Preference][pref]、[Preference adder][pref-add]。

MemoryFactory 会以单一 config 参数实例化注册 backend；SimpleTree 的构造器却需要多项注入对象。这表明“注册了 backend 名称”不等于“所有 backend 都能以相同方式独立创建”。Product 工厂直接注入对象，是实际使用的接缝。[factory][mem-factory]、[SimpleTree 参数][simple-tree]

### 4.1 最小方法探针：raw 路径是存在的

在本项目 venv 激活后运行一次标准库 AST 探针：从上游文件复制 GeneralTextMemory 的 add/get/search/_embed_one_sentence 方法，以及 SimpleStructMemReader 的 _iter_chat_windows；移除构造/继承/第三方导入，以 fake model、fake embedder、fake store 执行。正文包含中文、换行、缩进、emoji、尾部空格；fake LLM 的 generate 一旦调用就失败。

结果：

~~~json
{
  "general_add_get_search_content_exact": true,
  "general_llm_generate_calls": 0,
  "embedded_inputs": 2,
  "reader_window_equals_input": false,
  "reader_source_content_exact": true,
  "reader_window_prefix": "user: "
}
~~~

第一次探针因 harness 将 _count_tokens 命名为 _token_count 而失败；更正 fake 属性后通过。这个结果验证了被复制方法的正文传递行为，**没有验证实际 Pydantic 校验、包导入、构造、数据库事务、进程重启和 API**。它支持“可绕过 extract”，不支持“主 SDK 无需 LLM SDK/配置就能初始化”。[General 方法][general]、[reader 加前缀][chat-window]

## 5. 原始输入与提炼条目：谁是权威？

数据模型的主体只有 memory 字符串和 metadata。来源 SourceMessage 能记录 role / content / message_id / doc_path / file_info，Tree 扩展还有 sources、embedding、memory_type、background、usage、version/history 等。它能承载溯源信息，但本身没有把“外部原文的不可变 content hash + 稳定地址”定义成必须遵守的权威协议。[item 模型][item]

SimpleStruct 的 chat fast 路径给正文添加 role / time 前缀和换行，再按 token 窗口合并；fine 则调用 LLM，把返回 value 保存为 memory。MultiModal 同样先 parser.parse(fast)，聚合/切窗，再返回 fast 项或进入 fine。文档的 RawFileMemory 保存 mem_str 这个解析/分块结果，并连接 summaries；它不是保存原始文件字节的 blob store。[chat fast/fine][chat-reader]、[multimodal fast/fine][multi-reader]、[RawFileMemory][raw-file]、[summary/source 边][raw-edges]

Product 默认 async 路线尤其关键：

~~~text
APIADDRequest(async 默认)
 → reader fast → graph 写入 fast 条目 → 提交 MEM_READ
 → 后台读取这些条目 → fine 提炼 → graph 写入新条目
 → 可另加 RawFileMemory 与来源边
 → version off 时删除原 fast/working IDs
~~~

后台删除在 enhanced 处理之后的通用清理段；该段并没有仅以“生成了非空 enhanced memories”包围所有清理逻辑，因此不能凭 API 成功响应承诺原始输入永远可读。版本开关默认 off；version on 改为 soft_delete，但细化和版本实现另有 plugin 依赖。这是静态路径判断，未注入故障运行。[fast 写入与提交][single-add]、[后台读取/细化][mem-read]、[清理][fast-delete]、[默认版本开关][default-config]

**对我们的 S3 的推断：** Bubble 已总结好的正文若再当 chat/doc 输入走 fine，会被二次加工；走 Product fast 也可能改变 memory 的排版/聚合和生命周期。我们要的契约应是“add 原文 → 返回稳定地址 → get 返回同样正文”，然后派生条目单独关联这个地址。General direct add 或 Tree low-level add 可以作为实现素材，但不能继承 Product 默认 pipeline 就宣称契约已满足。

## 6. Graph 能否作为我们的派生视图？

**可以作为目标设计，但当前 Tree 子系统要改边界。** Neo4j provider 写入 memory 与 metadata；Neo4jCommunity 还先写外部 vector DB，再写 graph，以 vector_sync 标记状态，图和向量没有一个跨 provider 的原子事务。Postgres provider 用 memories / edges / pgvector 表存同类对象，而不是操作系统文件。[Neo4j 节点][neo-write]、[Community 双写][community-write]、[Postgres schema][pg]

organize/handler 会检索近似节点，再用 LLM 判断冲突/重复；merge 创建新正文、新 embedding，继承来源与关系，把旧节点 archived，并添加 MERGED_TO。另一路 hard_update 按时间删除旧节点。reorganizer 还聚类、生成父摘要、建立 PARENT。这里的语义是“维护当前可用记忆知识”，会改变可用正文和状态，不只是给原文加检索标记。[冲突判定][conflict]、[合并/删除][merge]、[后台树组织][reorganizer]、[父摘要与 PARENT 边][parent]

要变成我们的派生图，至少约定：

1. graph 节点必须引用 SourceRef（外部文件或虚拟原文的稳定 URI + revision/content hash）；原文地址由我们持久层分配。
2. archive/merge 只能改变派生节点和默认检索可见性，不能删改原文；反馈更正原文需要单独的显式命令。
3. 每个计算生成的派生视图记录输入 revision、模型/提示词版本、算法参数；输入变化后标 stale，可重建。图损坏时原文 get 仍然可用。人工确认、否决、更正和生效状态不是可自动重算的缓存，必须作为持久记录单独导出/恢复，并在重建视图时重新施加。
4. worker 输出 proposal 或新派生节点，由我们的仓库提交；只读项目来源没有写回权。
5. 查询结果区分 original / derived，并能沿来源边 get 原文；不能把 LLM 的合并结果标成原文。

这是我们应定义的接口，不是从源码发现已有完整实现。来源字段、SUMMARY/MATERIAL/MERGED_TO/PARENT 的想法值得借鉴；复制整个 graph DB 抽象会同时带入大量 tree 业务方法。Neo4j 与 Community 的 fulltext 路径目前返回空列表，provider 的 detect_conflicts/merge_memory 等方法也有 NotImplemented；相关语义实际位于 organizer，不能只移植 provider 就获得语义整理。[Community fulltext][community-fulltext]、[provider 未实现方法][neo-stubs]

## 7. 调度与后台持久性：是另一层能力

MOSCore 的 scheduler 可选，但 Product init_server 总会构造 scheduler，默认 API_SCHEDULER_ON=true 才启动。scheduler 接收已装配的 cube、reader、searcher、feedback、LLM 和数据库；MEM_READ handler 明确依赖 TreeTextMemory 与其 MemoryManager。这是框架内部记忆流水线调度器，不是一个现成的轻量通用任务系统。[产品 scheduler 初始化][api-init]、[调度器装配][scheduler]、[MEM_READ][mem-read]

默认 MEMSCHEDULER_USE_REDIS_QUEUE=false 时使用进程内 SchedulerLocalQueue；其 AutoDroppingQueue 在队列满时移除最旧项。worker 默认线程数 50、internal message queue size 200 都是上游 defaults，不代表家用 PC 必须或适合照用。GraphReorganizer 另有内存 PriorityQueue + 后台线程，不能以启用 Redis 推出所有整理任务都 durable。[queue 选择][queue]、[本地队列][local-queue]、[自动丢弃][dropping]、[默认值][scheduler-default]、[reorganizer 队列][reorganizer]

Redis 模式实有 stream xadd、ack、pending claim / xautoclaim 代码，也实有 maxlen 裁剪和可选 ack 后删除。能说“有重投递机制”，不能仅凭代码说 exactly-once 或所有任务永不丢失；还要验证 Redis 配置、任务幂等、失败传播和图/向量双写恢复。[Redis 写入/确认][redis-ack]、[claim][redis-claim]

**对本项目的建议：** 千级数据下先采用简单的 durable job 状态（例如 SQLite job/outbox 或现有持久队列），保留 enqueue/status/retry 的小接口；不在原文成功写入前等待提炼。家用 PC 的模型/embedding 资源由我们已有单服务策略控制。只有真实使用证明需要跨进程 worker、批量调度和巩固，才考虑 MemOS scheduler 的算法或独立服务。此处是架构建议，未做吞吐/内存性能测量。

## 8. 反馈、更正、版本与恢复不能混为一谈

### 8.1 反馈更新

feedback 不是普通原文 CRUD：它结合搜索、LLM 判断和标准化操作。version off 时 WorkingMemory 可以直接覆盖字段；其他 memory 可加新节点并把旧节点 archived。organizer 的 merge 与 feedback 也是不同的生命周期入口。[feedback update][feedback]

SDK MOSCore.update 对 tree_text 只记录“不支持 update”的 warning；Tree.update 本身未实现。非 tree_text 分派使用 memories= 参数，而 General.update 的参数是 new_memory，静态签名不一致。不能把顶层有 update 方法名解释成所有 backend 的统一更正协议已可用；本轮未执行此失败路径。[SDK update][core-update]、[General update][general]、[Tree update][tree-update]

### 8.2 版本接口存在，但独立开源完整性未证实

metadata 有 version/history/ArchivedTextualMemory 结构；MultiModalReader 开关打开后调用 memory_version.prepare_updates / apply_updates，feedback 调用 apply_feedback_update。这些是 **required single-provider plugin hooks**；无 callback 时 trigger_single_hook 抛错。[版本字段][item]、[reader 版本 hook][version-reader]、[required hook][hooks]

对 src、apps、tests、pyproject.toml 执行 rg（memory_version. / MEMORY_VERSION_），找到这些 hook 的定义与调用，未找到具体 callback 的注册/实现；这一搜索不覆盖外部安装插件或其他远端私有实现；当前 Python entry point 只注册 Dream 插件。不能排除安装了外部插件后有实现，**但不能把固定 checkout 默认主包说成已独立交付完整版本管理**。feedback fallback 会更新当前正文、sources 等字段，archived_item=None；没有在该 fallback 内创建完整历史节点或递增 version。即使插件返回 archived item，归档写入失败也可被 warning 后继续当前节点更新。[插件 entry point][deps]、[feedback 版本 fallback][feedback-version]

/product/recover_memory_by_record_id 恢复的是一次删除记录标识对应的节点；这不等于回滚正文版本或恢复图/向量一致性。[删除与恢复 API][record-recovery]

### 8.3 dump/load

Cube dump 将配置及各 memory backend 的导出文件写入目录，拒绝已存在的非空目录；load 再委托当前已初始化的 backend。General 导出 vector DB records；Tree 默认 include_embedding=false，graph export 还有生命周期筛选。因此“有 dump/load”与“完整可重建、可等价检索的备份恢复”不应等同。某些 load 路径捕获并记录异常，需要实际恢复验收，不能仅凭方法返回就判断成功。[Cube dump/load][cube-export]、[General export][general-export]、[Tree export][tree-export]

## 9. 实际 API 与接入边界

| 接口层 | 已看到的实际入口 | 需要保留的区别 |
|---|---|---|
| Python SDK | MOSCore.add/search/get/get_all/update/delete/dump/load | backend 分支的能力不同；方法存在不代表所有实现一致 |
| 本地 Product REST | /product/add、/search、/get_all、/get_memory、/get_memory/{id}、/get_memory_by_ids | 是 SingleCube/Product tree pipeline，不是 General raw backend 的 HTTP 包装 |
| 后台运维 | /product/scheduler/status、queue/status、wait、wait/stream 等 | 任务状态不替代原文提交和可读性契约 |
| 反馈/删除 | /product/feedback、delete_memory、delete_memory_by_record_id、recover_memory_by_record_id | 反馈可加工正文；删除记录恢复不等同版本回滚 |

依据：[router 路由][api-routes]、[record recovery][record-recovery]。api/client.py 的部分 async_mode 参数仍标成 bool，但当前 Product 请求模型使用字符串 async/sync；接入时必须以所接入服务与该 commit 的请求 schema 为准，不能只看历史示例。[client][api-client]、[模型][api-request]

用户/cube 概念能用于多 agent 共享同一人的分区：把 human/user 与 client/agent 身份区分开，统一访问同一 cube。SDK 的 UserManager 是 SQLAlchemy + 默认 SQLite 用户/cube 关系表，并非“用 MemOS 就必须上 MySQL”。不过 user_id / mem_cube_id 的业务字段不是 agent 授权凭据；本次只核对了基本 server_api 的 middleware/装配，不做完整安全审计，我们应继续使用自己的 HTTP/MCP 网关识别 caller、限制写权限、审计来源。[UserManager][users]、[基本服务入口][api-server]

## 10. 模块裁剪：按依赖边界取，不按目录名取

| 候选能力 | 可复用程度 | 最小外部接口 | 本轮建议 |
|---|---|---|---|
| 来源 / 元数据设计 | 高：数据结构可重写或定向抽取 | SourceRef、MemoryRecord、DerivedRecord | 借鉴字段；补强 URI/revision/hash，明确 original/derived |
| General raw 存取模式 | 高：方法逻辑简单；当前 constructor/import 非独立 | Embedder + RecordStore | 适合作为设计参考；现有原文库已在用则不必再引入一套库 |
| UniversalAPI embedding adapter | 中：可对接现有远程 embedding；需改配置/依赖装配 | embed(texts)→vectors | 可以单独适配，验证维度、批次与串行约束；不搬全部 factory |
| MemReader 提炼 | 中：有输入/coercion/chunker/prompt/schema/来源联动 | extract(source record)→derived proposals | 先取单一文本能力做离线 worker，Bubble 原样存储不经过它 |
| Graph organizer | 低到中：LLM、embedding、graph 全接口、node status/edges、线程队列耦合 | proposeRelations / proposeMerge | 先取算法/提示词；把变更落成派生视图，暂不整包移植 |
| Feedback | 低到中：搜索、LLM、reader、manager、graph、版本 hook | proposeCorrection(evidence, instruction) | 原文更正由我们提交；feedback 先输出提案 |
| Scheduler | 低：内部模块和 Tree-specific handler 较多 | enqueue/status/retry | 现阶段不用；以后有实际任务需求再评估独立服务 |
| Preference | 中：专用语义与 upsert，不是 raw store | PreferenceView + evidence | 延后，明确哪个用户声明生效、何时过期/冲突 |
| KV / LoRA | 与当前目标不适配 | 模型侧运行时/训练接口 | 不纳入当前产品基础层 |

embedding factory 有 universal_api 后端，它走 OpenAI 兼容 embedding endpoint，可参考我们当前外部串行嵌入服务。但适配一个 embed port 不代表 SDK 可同环境直接安装：根 pyproject 将 openai 限在 >=1.77,<2，而本项目 memory_base 当前固定 2.54；顶层 memos 导入还带入 MOS/Cube/Scheduler，MemoryFactory 也导入多种 memory 实现。**主 SDK 若作为实验服务，应独立 venv/进程；若裁剪代码，去除顶层 eager imports 和不必要 factory 是实际工作的一部分。**[依赖清单][deps]、[包初始化][init]、[UniversalAPI][embed]、[memory factory][mem-factory]

Graph provider 的替换成本也不能只看 factory map：接口包含节点、边、过滤、向量检索、导入导出、用户域、软删除等多种语义。Neo4j、Community、Postgres 与 PolarDB 是已注册项，不能把文档里的 Nebula 当成在这个 commit 已注册的 provider。[graph factory][graph-factory]

KVCacheMemory 有真实缓存提取/合并代码，但受 HuggingFace/vLLM 一类模型后端约束，不等于能接管外部 agent 的任意上下文窗口。LoRAMemory 文件明确是 placeholder，dump 写 Placeholder，不能计入“已可用参数记忆”。[KV 实现][kv]、[backend 限制][kv-config]、[LoRA placeholder][lora]

## 11. 文档与源码的几个差异

| 文档/名称暗示 | 固定源码的实际情况 | 研究使用原则 |
|---|---|---|
| AGENTS 的 API 是 memos.api.start_api:app | checkout 无 start_api.py；实际 server_api.py + server_router.py | 以实际路由/请求模型验证接入 |
| SimpleTree 名称像精简独立 backend | 注入多对象，继承 Tree；Product 手工装配 | 不能按 simple 一词估计安装成本 |
| 统一 update / version / rollback | Tree 不支持 SDK update；version hooks 缺内置实现证据；record recover 是删除恢复 | 不把顶层接口或字段当交付完整性 |
| RawFileMemory 像原始文件权威 | 保存解析分块的 mem_str，连接派生摘要 | 原始文件字节及我们的虚拟原文另存 |
| 文档列 Nebula / 参数记忆 | factory 未注册 Nebula；LoRA 是 placeholder | 不把路线图当已交付能力 |
| “持久调度”容易泛化到全部任务 | 默认本地内存队列；Redis 可选；organizer 另有内存队列 | 要逐条验证队列和重启路径 |

依据分别见上文源码链接；文档抽查对象包括 [architecture][docs-arch]、[overview][docs-overview] 与 [parametric][docs-para]。这些差异是确定模块边界所需的事实，不是对产品成熟度做全盘评测。

## 12. 对我们基本架构的落地指导

先定义两个稳定协议，而不是先选“数据库还是文件”：

~~~text
OriginalStore: put(original, source, request_id) → stable URI + revision
               get(URI[, revision]) → 原样正文 + 来源
DerivedMemory: build(source refs, policy) → derived records / edges
               search(query, filters) → original/derived 标识 + 可回溯 source refs
~~~

本项目 memory_base / OpenViking 运行包的 capture 链路已经跑通：本仓 f77c811 与 docs/memory-base-deployment.md:58 记录真实 Bubble pi 总结原样写入、跨会话读取、Codex 读取以及重启后 URI/哈希/搜索结果保留；DSH/opencode 的真实对话调用没有包含在这项成功声明中。下一步完善项目来源与检索范围、备份恢复、失败后重试语义，再择需探索提炼/巩固。继续采用当前基座，是保留已验收的原文提交能力；MemOS 的自动提炼/归并应在此之上独立演进。多 agent 可以共享一个提交入口，也可以由多个 writer 通过同一个一致性协议提交；**“有单一逻辑权威”不自动推出“必须一个业务进程写入所有东西”**。进程串行化是当前后端和部署约束的选择，不能升格为永久产品定律。

下一阶段只选一个真实缺陷：例如同一事实被重复总结、偏好过时、项目来源更改后派生结论失效。用固定原文数据集评估 MemOS 的一个 extractor 或 merge proposer，要求：原文 hash 不变、计算派生项可重建且人工反馈不丢失、结果能回溯来源、失败不会把“不可用”报告成“没有记忆”。上游 SingleCube search 捕获异常后返回空列表、manager 的 batch 写异常可能只记录日志，提示我们要把原文提交状态与提炼/检索状态显式区分。[search 错误路径][search-errors]、[batch 写入][manager-write]

最后再决定代码移植、独立服务或自实现。可移植的算法/协议并不难理解；真正的难点是保留跨阶段语义：来源完整性、幂等、版本更正、双写失败恢复、共享身份与生命周期。如果用自己的小接口包住上游能力，这些边界先由我们明确，后续剥离冗余才不会变成重新做一遍整个框架。

## 13. 验证局限与下一步最小验证

读到的 tests/memories/textual/test_general.py 覆盖 mocked factory、add/update/search/get；test_tree_manager.py 依赖 MagicMock store/LLM；test_feedback.py 多数测试工作流与操作归一化；reader fallback 测试验证不合法 LLM JSON 时仍生成条目。它们支持单元行为，不证明原文不可改写、后台任务重启恢复、独立版本插件或图/向量恢复等价。本轮没有运行这些上游测试，不引用 pass 数。[General tests][test-general]、[Manager tests][test-manager]、[Feedback tests][test-feedback]、[Reader fallback tests][test-reader]

尚未证实：实际 Product 的最小凭据/模型初始化是否能完全离线、不同 provider 的事务恢复、Redis 与 organizer 重启时任务保全、外部 version 插件交付形式、dump/load 后索引是否等价、端到端 user/cube 授权、真实家用 PC 延迟/内存、各检索模式是否适合我们的中文千级语料。也未深入 PolarDB/Milvus/skill 多模态链路。结论不依赖这些未知项来声称某路线已不可行。

推荐必要时只加三项隔离实验，而不是现在运行全系统：

1. General/direct Tree add 的真实 provider 环境：写入含格式正文，跨进程 get 对比 hash，明确 acknowledged commit。
2. 固定几条原文，对单一 reader / merge proposer 生成派生项，验证来源闭包和原文只读；测质量与成本。
3. 若要上后台整理，杀进程/重启、embedding 失败、graph/vector 分别失败、重复 request_id 的契约测试；版本回滚独立验收。

这些实验应在独立数据目录与进程中进行。本轮仅保存报告，不变更部署、索引、真实记忆和上游代码。

## 固定源码索引

下列链接均固定至本次 commit，行号经本地源码读取或行数校验，避免 main 后续变化污染证据。

[init]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/__init__.py#L1-L20
[deps]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/pyproject.toml#L7-L110
[core-init]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_os/core.py#L45-L80
[core-add]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_os/core.py#L734-L919
[documents]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_os/core.py#L233-L248
[core-update]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_os/core.py#L997-L1034
[cube]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_cube/general.py#L24-L48
[cube-export]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_cube/general.py#L50-L159
[mem-factory]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/factory.py#L1-L42
[item]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/item.py#L16-L360
[naive]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/naive.py#L59-L187
[general]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/general.py#L24-L144
[general-export]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/general.py#L175-L211
[tree-init]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree.py#L43-L120
[tree-update]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree.py#L355-L382
[tree-export]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree.py#L439-L476
[simple-tree]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/simple_tree.py#L25-L69
[pref]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/preference.py#L33-L190
[pref-add]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/prefer_text_memory/adder.py#L46-L148
[chat-window]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_reader/simple_struct.py#L315-L356
[chat-reader]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_reader/simple_struct.py#L359-L429
[multi-reader]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_reader/multi_modal_struct.py#L957-L1043
[raw-file]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_reader/multi_modal_struct.py#L772-L795
[raw-edges]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree.py#L524-L576
[version-reader]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_reader/multi_modal_struct.py#L664-L702
[graph-factory]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/factory.py#L1-L27
[neo-write]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/neo4j.py#L224-L295
[community-write]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/neo4j_community.py#L54-L117
[community-fulltext]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/neo4j_community.py#L468-L486
[neo-stubs]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/neo4j.py#L1201-L1234
[pg]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/graph_dbs/postgres.py#L43-L167
[conflict]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree_text_memory/organize/handler.py#L22-L130
[merge]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree_text_memory/organize/handler.py#L131-L205
[reorganizer]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree_text_memory/organize/reorganizer.py#L81-L238
[manager-write]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree_text_memory/organize/manager.py#L211-L248
[api-server]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/server_api.py#L15-L84
[api-router]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/routers/server_router.py#L68-L91
[api-routes]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/routers/server_router.py#L112-L422
[api-init]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/handlers/component_init.py#L135-L328
[api-request]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/product_models.py#L628-L659
[api-client]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/client.py#L145-L177
[default-config]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/config.py#L1031-L1075
[single-add]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/multi_mem_cube/single_cube.py#L662-L791
[search-errors]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/multi_mem_cube/single_cube.py#L190-L226
[scheduler]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/base_scheduler.py#L186-L262
[scheduler-default]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/schemas/general_schemas.py#L17-L35
[queue]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/task_queue.py#L28-L48
[local-queue]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/local_queue.py#L25-L90
[dropping]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/general_modules/misc.py#L198-L233
[redis-ack]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/redis_queue.py#L552-L610
[redis-claim]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/redis_queue.py#L782-L896
[mem-read]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/handlers/mem_read_handler.py#L90-L257
[fast-delete]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_scheduler/task_schedule_modules/handlers/mem_read_handler.py#L396-L441
[feedback]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_feedback/feedback.py#L269-L337
[feedback-version]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_feedback/feedback.py#L339-L409
[hooks]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/plugins/hooks.py#L77-L100
[record-recovery]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/api/routers/server_router.py#L465-L501
[users]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/mem_user/user_manager.py#L38-L125
[embed]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/embedders/universal_api.py#L33-L148
[kv]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/activation/kv.py#L1-L139
[kv-config]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/configs/memory.py#L43-L98
[lora]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/parametric/lora.py#L1-L41
[test-general]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/tests/memories/textual/test_general.py#L20-L268
[test-manager]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/tests/memories/textual/test_tree_manager.py#L10-L144
[test-feedback]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/tests/mem_feedback/test_feedback.py#L1-L141
[test-reader]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/tests/mem_reader/test_simple_struct_fallback.py#L1-L111
[docs-arch]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/docs/en/open_source/home/architecture.md#L1-L45
[docs-overview]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/docs/en/open_source/modules/memories/overview.md#L125-L217
[docs-para]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/docs/en/open_source/modules/memories/parametric_memory.md#L1-L37
[parent]: https://github.com/MemTensor/MemOS/blob/a7367d07e55db61099f7b4e2c1108bc5831a24f3/src/memos/memories/textual/tree_text_memory/organize/reorganizer.py#L550-L641

## 附录：可复跑的 AST 方法探针

从本项目根目录激活 venv 后，把下面代码交给 Python（例如 PowerShell 单引号 here-string 管道给 python -）。代码只读固定上游 checkout，不导入 memos，不写数据库/文件。fake Item 刻意绕过真实 schema，因此 UUID、Pydantic 校验和 metadata 序列化均不在该探针覆盖内。

~~~python
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

base = Path(r'D:\python_work\work2026-4\MemOS')


def extract(path, classname, names, resultname):
    tree = ast.parse((base / path).read_text(encoding='utf-8'))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == classname)
    methods = [copy.deepcopy(n) for n in cls.body
               if isinstance(n, ast.FunctionDef) and n.name in names]
    for method in methods:
        method.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module='__future__',
                       names=[ast.alias(name='annotations')], level=0),
        ast.ClassDef(name=resultname, bases=[], keywords=[],
                     body=methods, decorator_list=[]),
    ], type_ignores=[])
    return compile(ast.fix_missing_locations(module),
                   '<upstream-methods>', 'exec')


class Item:
    def __init__(self, **data):
        self.__dict__.update(data)

    def model_dump(self):
        return copy.deepcopy(self.__dict__)


class Embed:
    def __init__(self):
        self.inputs = []

    def embed(self, texts):
        self.inputs.extend(texts)
        return [[float(len(text)), 1.0] for text in texts]


class DB:
    def __init__(self):
        self.data = {}

    def add(self, items):
        self.data.update({item.id: item for item in items})

    def get_by_id(self, ident):
        return self.data.get(ident)

    def search(self, vector, top_k):
        return [SimpleNamespace(payload=item.payload, score=1.0)
                for item in list(self.data.values())[:top_k]]


class LLM:
    calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('LLM call forbidden')


ns = {'TextualMemoryItem': Item, 'VecDBItem': Item}
exec(extract('src/memos/memories/textual/general.py', 'GeneralTextMemory',
             {'add', 'get', 'search', '_embed_one_sentence'}, 'General'), ns)
memory = ns['General']()
memory.embedder = Embed()
memory.vector_db = DB()
memory.extractor_llm = LLM()
raw = 'Bubble 原样总结\n  保留缩进与 emoji 🫧\n\n尾部空格  \n'
memory.add([{'id': 'stable-probe-id', 'memory': raw,
             'metadata': {'source': 'Bubble'}}])
got = memory.get('stable-probe-id').memory
searched = memory.search('总结', 1)[0].memory
assert got == raw and searched == raw and memory.extractor_llm.calls == 0

ns2 = {}
exec(extract('src/memos/mem_reader/simple_struct.py', 'SimpleStructMemReader',
             {'_iter_chat_windows'}, 'Reader'), ns2)
reader = ns2['Reader']()
reader.chat_window_max_tokens = 10000
reader._count_tokens = len
window = list(reader._iter_chat_windows([{'role': 'user', 'content': raw}]))[0]
assert window['text'] != raw and window['sources'][0]['content'] == raw
print(json.dumps({
    'scope': 'AST methods with fakes; no imports/constructors/real persistence',
    'general_add_get_search_content_exact': got == raw and searched == raw,
    'general_llm_generate_calls': memory.extractor_llm.calls,
    'embedded_inputs': len(memory.embedder.inputs),
    'reader_window_equals_input': window['text'] == raw,
    'reader_source_content_exact': window['sources'][0]['content'] == raw,
    'reader_window_prefix': window['text'][:6],
}, ensure_ascii=False))
~~~