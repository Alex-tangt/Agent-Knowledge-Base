# Agent-Knowledge-Base

目标形状：**Agent 知识库**——把 Markdown 知识库变成 agent 可安全读写、可检索的长期知识底座；hero 是**记忆能力包**（MCP + skill，`memory_agent/`）。三模块单仓：`ragcore/`（可复用核心）、`legal_web/`（**【已排除】上一版本遗留的无关产品**，仅保留代码）、`memory_agent/`（记忆能力包）。决策见 `docs/adr/0005`–`0007`，领域语言见 `CONTEXT.md`。

`legal_web/` 是当前**可运行、有评测证据**的适配层：可复用的 RAG 检索/问答核心 + 「21 部中国现行法律法规」问答 demo（起点为大学 NLP 课程作业）。下文即该适配层的评测证据与工程纪律样本。

## 核心亮点

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
├── legal_web/           # 【已排除】上一版本遗留的无关产品（仅保留代码）
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
