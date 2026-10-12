# Agent-Knowledge-Base

[![CI](https://github.com/Alex-tangt/Agent-Knowledge-Base/actions/workflows/ci.yml/badge.svg)](https://github.com/Alex-tangt/Agent-Knowledge-Base/actions/workflows/ci.yml)

**让多个 agent 共享个人知识和文章、视频总结。**

当前开发入口是 `memory_base/`：固定 OpenViking 0.4.23，统一安装、启动、停止与状态检查；HTTP / MCP 共用追加、搜索、读取与列举接口。Agent-bubble 的 pi 接入使用这一服务。项目 Markdown 经登记实时只读，结果带字节版本；路径权限、完整冷备份/恢复和 Windows 登录启动经本机 CLI 管理。部署方式与已完成宿主验收见 [正式部署说明](docs/memory-base-deployment.md) 和 [真实接入验收](experiments/memory-base-live/README.md)，本轮恢复和产品包验收进度见 [交付任务板](docs/memory-base-delivery-plan-2026-10-11.md)。

OpenViking / MemOS 的固定源码比较、模块取舍和基本架构建议已保存为 [源码研究报告](docs/architecture-memory-base-source-study-2026-10-11.md)。独立产品的保留、剥离、升级及数据出口计划见 [产品计划](docs/memory-base-product-plan.md)。

Windows 新部署入口为 `install-memory-base.ps1`；POSIX 薄壳 `install-memory-base.sh` 的凭据持久化仍待补齐。原 `install.ps1` / `install.sh` 与下面的 `memory_agent` 内容保留旧自研实现的用法；旧引擎按 [ADR-0032](docs/adr/0032-personal-memory-base-adopt-then-productize.md) 冻结。`legal_web` 为已排除的历史产品。

## 当前产品快速开始（Windows）

准备 Python 3.12，在仓库根目录运行安装入口；将示例中的路径换成自己的 Python 3.12：

```powershell
powershell -File .\install-memory-base.ps1 -Python C:\Python312\python.exe
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base start
& "$env:USERPROFILE\.agent-memory-base\runtime\Scripts\python.exe" -m memory_base status
```

独立包只包含 `memory_base`，依赖固定的 OpenViking 与进程外嵌入服务。安装时准备目标环境的 SDK 缓存，首次安装会有数分钟成本；home 使用默认目录或较短路径。宿主注册、来源登记、权限与备份命令见[部署说明](docs/memory-base-deployment.md)。

2026-10-12 已验证 Windows 独立安装、默认首次启动、Bubble/Codex 共享读写、实时项目读取及实际正式快照的空数据根恢复；完整已有单测为 833 passed / 5 skipped。手机/第二设备、真实登录触发与 DSH/opencode 实际对话仍待验收，POSIX 未交付。具体环境和失败记录见[交付任务板](docs/memory-base-delivery-plan-2026-10-11.md)。

<details>
<summary>旧版资产与历史评测记录（冻结实现）</summary>

## 旧版资产记录

三模块单仓：

- **`memory_agent/`** —— 记忆能力包（项目主体）：以 **MCP 协议 + Skill** 对外暴露，让 coding agent 跨会话记住知识；统一多个本地来源、共享单实例服务、权限隔离，配生命周期工具（新增 / 取代 / 归档）与确定性评测。
- **`ragcore/`** —— 可复用核心：检索 / 重排 / 路由 / 会话记忆，零 FastAPI 依赖的真包。
- **`legal_web/`** —— 一个可运行、有评测证据的 RAG 问答 demo：「21 部中国现行法律法规」问答（含 53 题评测集与结果，起点为大学 NLP 课程作业）。

设计要点：**Markdown + Git 为真相源、向量索引可随时重建**；**31 篇 ADR** 记录架构决策，**582 项自动化测试**（CI：`.github/workflows/ci.yml`，Ubuntu + Windows 两腿跑单测 / 评测 harness / 接入冒烟），实验留痕可复现。决策见 `docs/adr/`，领域语言见 `CONTEXT.md`。

---

## 记忆能力包（`memory_agent`）核心亮点

- **统一多来源检索**：把多个项目 / 仓库的 Markdown 统一进一套索引并标注来源（打通数据孤岛），读写分离——读整张知识表、写只经受控网关。
- **共享单实例服务**：一个常驻服务唯一持有语义 embedding 模型，各会话经轻量代理接入，内存占用从 N×3.9GB 降到 1×3.9GB + N×数十 MB，根治多会话并发 OOM。
- **权限隔离**：MCP 边界 Bearer 鉴权 → 身份上下文 → 工具层强制注入租户 / 密级过滤（只可收窄、越权拒绝），审计不落凭证。
- **迭代检索（Agentic RAG）**：基于 MultiHop-RAG 开源基线，三臂实验（N=200）证明多轮检索较单轮**答案 +10.2pp、recall@5 +9.1pp（显著）**。
- **索引一致性**：代目录 + `CURRENT` 指针原子切换（中断不留坏索引）；查询时轻量指纹增量刷新（172 文件 / 51ms）。

---

## 政策法规问答 Demo 亮点（`legal_web`）

- **混合检索**：向量语义（BGE-M3，1024-dim）+ 法条编号精确匹配 + 锚点关键词，合并去重后由 `bge-reranker-v2-m3` cross-encoder 精排 → 自适应选择上下文
- **法条感知切分**：按「第X条」边界切块（≥3 标记启用，带标题前缀），策略抽象支持按知识库绑定（`ragcore/strategies/`）
- **无依据拒答**：post-reranker 距离 > 阈值的证据闸门 + prompt 级"仅在上下文完全不相关时拒答"——实测**误拒率 2.4%**、**无答案拒答率 91.7%**
- **查询重写**：LLM 将长问题压缩为检索友好短语
- **多知识库 + Agent 自动路由**（LangGraph），RAG 与 LLM-only 双模式对比
- **系统化评测**：53 题三类问题集（事实/综合/无答案）+ LLM-as-judge 多维打分 + 确定性硬指标（来源召回、拒答率）

## 评测证据（`legal_web/tests/results_scored.md`）

| 维度 | 总体(53) | 事实型(25) | 综合型(16) | 无答案型(12) |
|------|------|------|------|------|
| 忠实度 | 4.87 | 5.0 | 4.56 | 5.0 |
| 相关性 | 4.91 | 5.0 | 4.69 | 5.0 |
| 上下文精确率 | 4.76 | 5.0 | 4.31 | 4.83 |
| 上下文召回 | 4.49 | 4.84 | 3.81 | 4.67 |
| 来源召回率（事实型法条是否检索到） | — | **1.0** | — | — |
| 误拒率（应答却拒） | **2.4%** | — | — | — |
| 无答案拒答率（该拒则拒） | **91.7%** | — | — | — |

对比 LLM-only：RAG 对知识库外问题**拒答防幻觉**（忠实度 5.0），LLM-only 凭参数记忆编造——检索增强的价值被量化验证。

## 技术栈

| 模块 | 选用 |
| ---- | ---- |
| 后端 | FastAPI + uvicorn（SSE 流式 / JSONL） |
| 向量库 | Qdrant 本地持久化，多集合多知识库隔离 |
| Embedding | BAAI/bge-m3（本地，1024-dim） |
| Reranker | BAAI/bge-reranker-v2-m3（本地 cross-encoder） |
| LLM | DeepSeek API（OpenAI 兼容，`.env` 配置） |
| Agent | LangGraph 意图识别 + 自动路由 |
| 前端 | 原生 HTML/CSS/JS ES 模块，零构建 |

## 目录结构

```
├── ragcore/             # 可复用核心（零 FastAPI 依赖）；真包，含 pyproject.toml
│   ├── services/        # rag/chat/vector_store/document/reranker/embedding/view_registry
│   ├── strategies/      # 切分+检索策略抽象（legal/default）
│   ├── agents/          # LangGraph 路由 + 会话记忆
│   └── config/ · models/ · utils/
├── legal_web/           # 可运行的政策法规 RAG 问答 demo（含评测集与结果）
│   ├── app.py · api/routes.py · frontend/   # FastAPI + 零构建 SPA
│   ├── data/raw/        # 21 部法律法规全文
│   ├── tests/           # 评测子系统：questions/ground_truth/run_eval/score_eval + 结果
│   ├── ingest.py · fetch_laws.py            # 知识库构建 / 法条抓取
│   └── requirements.txt · .env · vector_db/ · uploads/
├── memory_agent/        # 记忆能力包（MCP + skill）；可安装，含 pyproject.toml
├── tests/unit/          # ragcore 核心单测（pytest）
├── experiments/         # 实验留痕：脚本+数据+结论同处一目录
├── docs/adr/            # 架构决策记录（目标形状/基线/评估/开发纪律/记忆架构）
├── CONTEXT.md           # 领域语言
└── AGENTS.md            # 开发工作流（AI 自维护机制）
```

## 工程实践（AI 辅助开发的纪律样本）

- **决策可追溯**：`docs/adr/` 记录目标形状、基线重置、评估策略、开发纪律、产品升级、记忆架构、布局更名
- **实验留痕**：`experiments/` 每实验一目录——延迟调查（rerank-latency、e2e-latency）、拒答根因、查询改写优化器，脚本+数据+结论同处
- **锚点纪律**：`memory_agent/eval/baseline_A.md` 记录每次重构前后可复现的基准（单测 + 导入冒烟 + 启动冒烟）
- **CI 回归闸门（#69）**：`.github/workflows/ci.yml` —— 每次 push / PR 在 Ubuntu 与 Windows 上跑单测 + 检索 agent 评测 harness（确定性桩场景，两次运行逐字节相同）+ 真 `opencode serve` 接入冒烟（无 server 时 SKIP）
- **开发工作流**：`AGENTS.md` 内嵌流程路由（缺陷→根因 / 功能→决策闸门→单元提交 / 实验→留痕 / 收工→健康闸门），解决 AI 开发中"调优无底洞、不提交、文档脱离实际"的失控问题

## 快速开始

```bash
# 1. 激活虚拟环境（Windows）
venv\Scripts\activate

# 2. 安装依赖 + 两个本地包（ragcore / memory_agent 为真包，ADR-0024）
pip install -r legal_web/requirements.txt
pip install -e ragcore -e memory_agent

# 3. 配置 .env
cp legal_web/.env.example legal_web/.env   # 填入 API_KEY / BASE_URL / Model

# 4. 构建知识库（BGE-M3 + reranker 首次自动下载）
venv\Scripts\python.exe legal_web/ingest.py

# 5. 启动，打开 http://localhost:8000
venv\Scripts\python.exe legal_web/app.py
```

> 模型加载在后台完成（约 30-40s），前端显示加载进度；启动加载用 `local_files_only=True`，模型缓存后不依赖网络。

</details>
