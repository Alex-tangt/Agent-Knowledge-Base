# memory-framework-spike（候选记忆框架可回滚 spike）

> **2026-10-10 阅读指引**：owner 已确定“先采用开源产品跑通 MVP，再按实际使用完善并独立产品化”的方向，见 [ADR-0032 草案](../../docs/adr/0032-personal-memory-base-adopt-then-productize.md)。**本页以下的 J1–J5 是 2026-10-09 原计划的历史预注册判据，保留原文，不作为当前 MVP 的开工/迁移闸门。** 特别是 J3“必须显著优于旧检索才值得换”不适用于本轮目标。当前验收与首个开发单元见 [MVP 验证方案](mvp-protocol.md)；这里没有新增实测结果。

日期：**2026-10-09 开** · 状态：**进行中（判据已预注册，数据/结论待填）**

> **目的**：回答"要不要用现成记忆框架替换我们自维护的引擎那一半"。
> **不是**架构决策；结论只覆盖**引擎可替换性**，**不覆盖**愿景三格（主体=人 / 行为改变 / 自我模型闸门）——
> 见 `experiments/memory-frameworks-survey/README.md` 数据 5：那三格在 15 个系统里全是空位，买不到。

## 问题

我们的维护面里有一大块是**商品化基建**（向量/混合检索/融合/重排/embedding 服务/生命周期/MCP 工具面/部署）。
census 显示这部分四个框架全都有且更"标准"。用户想少维护这套漂壳。
但要先量：**换掉引擎到底值不值**，以及**能不能无损迁移**。

## 假设（开工前）

1. 候选**可以**无损装载我们的 Markdown 条目（保留条目边界 + `id/title/type/tags/status/sources/owner`）。
2. 候选能表达"只读条目"。
3. 在我们自己的 51 条评测集上，候选**不会显著优于**现有链路。
4. 省下的维护面**小于或接近**新增的依赖/升级/集成面。

## 判据（**开跑前冻结，跑完不得改**；先例 #74）

| # | 判据 | 通过线 | 不过的后果 |
|---|---|---|---|
| **J1** | **无损性**（门槛，先过） | 能保留**条目边界**与 `id/title/type/tags/status/sources/owner` 全部字段；若必须经 LLM 抽取重写，需给出**拆成 N 条 / 元数据丢失清单** | 丢任一项 → 判"不能无损迁移"，**spike 到此为止** |
| **J2** | **主体表达**（门槛） | 能表达"人"为主体；能表达"某条目只读不可写" | 任一不能 → 记为**需自建**，从"省下的维护面"里**扣回** |
| **J3** | **检索质量** | 同一评测集、同 top-k 下 `recall@5`/`nDCG@10`/`MRR`，**paired bootstrap 显著优于**现有 `hybrid-rerank` 链路 | 等价或略好 → **撑不起迁移成本**，判"不值得换" |
| **J4** | **代价账** | 新增依赖面（安装体积 / 常驻进程 / DB / 升级面 / 未决 issue 与严重 bug）**明显小于**省下的维护面；且新增的可用性约束（如"必须常开 DB"）被显式登记 | 新增 ≥ 省下 → 判"只是搬家" |
| **J5** | **可逆性** | 能靠"文件仍是真相源"一键回退 | 候选把真相源变 DB → **不可逆等级上升**，需单列 |

> J3 的判据**故意设为"显著优于"而不是"不差"**：等价不构成迁移理由（迁移是纯成本）。

## 设置

### 候选（按 census 的短名单）
- **Hindsight**（MIT，可嵌 `pg0`，合成层最强）
- **Basic Memory**（AGPL-3.0 → **仅本地试验，不 vendor 代码**）
- 备选：Cognee（主体最接近"人"，但 EE 除外）

### 隔离要求（**硬性**）
- 候选装进**独立环境** `venv-spike/`——**绝不进主 venv**（先例：`ADR-0027` D7 的 `venv-parse`）。
- **绝不触碰**真实 KB（`~/.config/opencode/knowledge`）、真实索引（`memory_agent/vector_db`）、常驻 daemon；
  一律用**副本**（先例：`write_path_sandbox.py` 把真实 KB 克隆到 temp）。
- 端口隔离（候选自己的端口；必要时 `MEMORY_MCP_PORT`）。
- **收工核对**：真实 KB 的 `git rev-parse HEAD` + `git status --short` 与开工前一致。

### 阶段
- **S0 环境与代价**（分钟级）：能否在 Windows / Python 3.12 装上；嵌入式 `pg0` 是否支持 Windows；
  依赖体积；Hindsight 的 `retain` 是否**必须 LLM**（若是，需要什么端点）。
- **S1 无损装载**（小时级）：可写 KB 副本（~40 条）灌进候选，逐条比对 J1 的字段清单。
- **S2 同题对比**（单日级）：`memory_agent/eval/retrieval_eval_set.json`（45 有答案 + 6 无答案），
  候选 vs 现有 `hybrid-rerank`，paired bootstrap。
- **S3 代价账 + 结论**。

## 数据

_（S0 起逐步填）_

### S0 · 环境与代价

**本机**：venv Python **3.12.2**（Hindsight 要 ≥3.11 ✓ / Basic Memory 要 ≥3.12 ✓）· pip 24.0 · **PyPI 可达**（HTTP 200）。
候选框架**均未预装**；隔离环境 `venv-spike/`（已加 `.gitignore`，先例 `venv-parse/` 引 ADR-0027 D7）。

**平台支持（`pg0` = Hindsight 的嵌入式 Postgres）**：

| 平台 | 是否支持 |
|---|---|
| **Windows 10/11 x86_64** | ✅ **支持**（released binary，CI 构建） |
| macOS（Apple Silicon / Intel） | ✅ 支持 |
| Linux glibc ≥2.35 / Alpine 3.20–3.21 | ✅；Alpine 3.22+ ❌（ICU 76 不兼容） |
| 仅 root 的环境（Colab / 受限容器） | ❌ PostgreSQL 拒绝以 root 运行 |

`pg0` 是**单二进制**，捆绑 **PostgreSQL 18 + pgvector 0.8.5**，可离线，数据落 `~/.pg0/`；
Hindsight 无 `HINDSIGHT_API_DATABASE_URL` 时自动起嵌入式实例（文档写 5555，`pg0` 自身默认 5432）。
→ **S0 结论：Windows 路径可行，不需要 Docker。**

**依赖面（PyPI `requires_dist` 顶层条数，安装前实测）**：

| 候选 | 版本 | 顶层依赖条数 | 备注 |
|---|---|---|---|
| **hindsight-api** | 0.10.3 | 1 → 实为 **`hindsight-api-slim[all]`，slim 有 90 条** | 含 `anthropic`/`cohere`/`google-genai`/`boto3`/`claude-agent-sdk`/`github-copilot-sdk`/`litellm`/`markitdown[docx,pdf,pptx,xls,xlsx]`/opentelemetry×5/`psycopg2-binary`/`pgvector`/`fastapi[standard]`/`fastmcp`/`cryptography`/`numpy`/`pillow`/`protobuf` |
| **basic-memory** | 0.23.2 | **46** | 含 `fastmcp==4.0.0b1`（**beta 钉死**）、`psycopg==3.3.1`（精确钉）、`sqlalchemy`/`alembic`/`fastembed`/`sqlite-vec`/`litellm`；并**把 `pyright`、`pytest-aio`、`pytest-asyncio` 放进运行时依赖**（依赖卫生信号） |
| **cognee** | 1.6.3 | **137** | 含 `lancedb`/`pylance`/`numpy`/`pandas`/`onnxruntime`/`dlt`/`litellm`/`instructor`/`tiktoken`/`networkx`/`rdflib`/`fastapi-users`/`gunicorn`/`enola-cli==0.4.26`（Windows 另加 `python-magic-bin`） |

**对照**：本项目 `memory_agent` 的运行时依赖是 **`mcp>=2.2,<3` 一条**（引擎依赖复用 `legal_web/requirements.txt`）。
→ **J4 的初步数量证据：换框架不是净减，是把"自己维护漂壳"换成"背 46–137 条顶层依赖 + 升级面"。**
（最终以 `venv-spike` **实装后**的顶层包数与体积为准，见下。）

**LLM 端点（Hindsight 的 `retain` 必须过 LLM）**：

- 进程环境 `API_KEY` / `BASE_URL` / `Model` / `MEMORY_AGENT_LLM_*` **全部未设**。
- `legal_web/.env` **存在**并含 `API_KEY` / `BASE_URL` / `Model`（**只记录键名，未读取、未打印任何值**）；
  `memory_agent/.env` 只有 `MEMORY_CLOUD_*`。
→ **S1/S2 有一个可用的 OpenAI 兼容端点，但它是 legal RAG 的凭据**；使用时必须由进程环境注入、**绝不落盘或进日志**。

**顺手记一条文档腐化样本**：Hindsight `storage.md` 的"open a GitHub issue"链接指向 `hindsight-ai/hindsight`，
而实际仓库是 `vectorize-io/hindsight` —— 与 census 里"厂商文档也会漂"一致。

**实装结果**（`venv-spike` 安装 `hindsight-api==0.10.3`）：

_（后台作业 `pwsh-172` 运行中，待填：顶层包数 / 体积 / 耗时 / 能否 import）_

### S0 追加 · 三条会改变判断的官方事实（读 README + storage.md）

**① `retain` 走 LLM 抽取 ⇒ 对 J1 是无损性的坏消息。**
官方原话："Behind the scenes, retain **uses an LLM to extract key facts, temporal data, entities, and relationships**…
transform extracted data into canonical entities, time series, and search indexes along with metadata."
→ 我们精心写的**条目边界**会变成 N 条抽取事实，`type` / `status` / `sources` / `owner` **不再是检索字段**。
缓冲余地：retain 支持 `metadata`，且"raw memories 与 mental models 都可被过滤"——所以**标签可带一部分**，
但**一条 = 一条**这个语义**保不住**。**J1 先验：倾向不过。**

**② 引擎不只是商品化——它比我们多两条召回通道。** 官方 recall 是四条并行：
`Semantic（向量）` + `Keyword（BM25）` + **`Graph（实体/时间/因果链接）`** + **`Temporal（时间范围过滤）`**，
再 **RRF** 融合 + **cross-encoder 重排** + 按 token 裁剪。
→ 与本项目既有链路（dense + BM25 + DBSF → rerank）**同族，但多出"图"与"时间"两路**。
这是"换引擎"目前**唯一的技术性理由**（不是省事），且正好对着我们 ADR-0030 量到的"排序头寸为主"。

**③ 它已经出货 opencode 集成。** `npx @vectorize-io/hindsight-coding-agents install all`
——"a per-repo bank built automatically from git history and past sessions, **injected into the agent as it starts working**, plus curated knowledge pages"；
支持列表含 **opencode** 与 **DeepSeek Harness**。
→ 意味着"会话起点注入"这条**人家已经做到能一键装**，也意味着**这个 spike 可以极便宜地试**（不必先写适配层）。

**顺带两条记档**：

- **"独立复现"的措辞要精确**：README 声称 **Hindsight 自身分数**由 Virginia Tech Sanghani Center 与 The Washington Post
  独立复现（**报告未找到**），而"**其他厂商的分数是自报**"——与 census 里"厂商互撕"的记录一致，但**不是**"厂商自认自报"。
- **Memory Defense**（opt-in、per-bank、45 条模式、redact 或 block、**不追溯**）对"记忆主体是人"的知识库是**加分项**：
  它是 census 里少见的、把"敏感信息"当成一等问题处理的机制。



## 结论

**本页 J1–J5 的原计划（Hindsight / Basic Memory / Cognee 三候选）未执行** —— 2026-10-10 owner 把路线改为"先采用低成本开源产品跑通 MVP"，预注册判据随之降级为历史记录（见页首横幅）。**本轮实际执行的验收是 [mvp-protocol.md](mvp-protocol.md) 的 M0–M6，已全部执行完毕，结论如下：**

> **候选（OpenViking 0.4.23）能**原样保存与读回原文、跨消费者共享、按 ACL 做范围授权、显式报告不可用与处理失败、做版本快照与「内容面 + 检索面」双面备份，并**在两个真实 agent 宿主**（opencode + DeepTutor）接入同一实例；**不能**就地挂载本地来源（只能导入快照，且**读到旧快照没有任何信号**）；其**自带的 in-process 本地 GGUF 嵌入器并发不安全**（整进程 abort，须把嵌入放进程外并串行）。

- 逐项判定、读法前提、独立复核结论与已知限制：**[mvp-protocol.md § 结论](mvp-protocol.md)**（Lead 验收）
- 执行证据与恢复手册：**[mvp/README.md](mvp/README.md)**、**[mvp/RECOVERY.md](mvp/RECOVERY.md)**
- 接入与成本账：**[../memory-host-connect/M6-report.md](../memory-host-connect/M6-report.md)**
- 独立复核（预注册判据，未改）：**[../memory-spike-verify/verify_conclusion.md](../memory-spike-verify/verify_conclusion.md)**

**尚未解决、留给后续阶段的**：来源新鲜度无信号（见 M2 读法前提）、备份包不含身份/ACL、恢复是离散点、账户 ACL 默认关闭、工具数注释陈旧（实测 20）。

## 资产与复原

- 候选框架装 `venv-spike/`（gitignored）；**本目录不放原始语料与派生索引**，用的是真实 KB 的**临时副本**。
- 真实 KB 与真实索引**保持字节不变**，收工核对命令记在「设置」。
