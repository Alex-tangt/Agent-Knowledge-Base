# 个人 RAG 工具 · 政策法规问答旗舰 Demo

可复用的个人 RAG 检索/问答工具：以 **service 层 + 切分策略为可复用核心**，通过适配层对外暴露（FastAPI 网页 / MCP 服务器[延后]），「21 部中国现行法律法规」是旗舰垂直 demo（起点为大学 NLP 课程作业）。

## 核心亮点

- **混合检索**：向量语义（BGE-M3，1024-dim）+ 法条编号精确匹配 + 锚点关键词，合并去重后由 `bge-reranker-v2-m3` cross-encoder 精排 → 自适应选择上下文
- **法条感知切分**：按「第X条」边界切块（≥3 标记启用，带标题前缀），策略抽象支持按知识库绑定（`backend/strategies/`）
- **无依据拒答**：post-reranker 距离 > 阈值的证据闸门 + prompt 级"仅在上下文完全不相关时拒答"——实测**误拒率 2.4%**、**无答案拒答率 91.7%**
- **查询重写**：LLM 将长问题压缩为检索友好短语
- **多知识库 + Agent 自动路由**（LangGraph），RAG 与 LLM-only 双模式对比
- **系统化评测**：53 题三类问题集（事实/综合/无答案）+ LLM-as-judge 多维打分 + 确定性硬指标（来源召回、拒答率）

## 评测证据（`tests/results_scored.md`）

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
├── backend/
│   ├── app.py · api/routes.py · config/ · models/
│   ├── agents/          # LangGraph 路由 + 会话记忆
│   ├── services/        # 可复用核心：rag/chat/vector_store/document/reranker/embedding/kb_registry
│   ├── strategies/      # 切分+检索策略抽象（legal/default）
│   ├── ingest.py        # 知识库构建
│   └── requirements.txt
├── frontend/            # 静态 SPA，零构建
├── data/raw/            # 21 部法律法规全文
├── tests/               # 评测子系统：questions/ground_truth/run_eval/score_eval + 结果
├── experiments/         # 实验留痕：脚本+数据+结论同处一目录
├── Docs/adr/            # 架构决策记录（目标形状/基线/评估/开发纪律）
├── CONTEXT.md           # 领域语言
└── AGENTS.md            # 开发工作流（AI 自维护机制）
```

## 工程实践（AI 辅助开发的纪律样本）

- **决策可追溯**：`Docs/adr/` 记录目标形状、基线重置、评估策略、开发纪律四则决策
- **实验留痕**：`experiments/` 每实验一目录——延迟调查（rerank-latency、e2e-latency）、拒答根因、查询改写优化器，脚本+数据+结论同处
- **开发工作流**：`AGENTS.md` 内嵌流程路由（缺陷→根因 / 功能→决策闸门→单元提交 / 实验→留痕 / 收工→健康闸门），解决 AI 开发中"调优无底洞、不提交、文档脱离实际"的失控问题

## 快速开始

```bash
# 1. 激活虚拟环境（Windows）
venv\Scripts\activate

# 2. 安装依赖
pip install -r backend/requirements.txt

# 3. 配置 .env
cp backend/.env.example backend/.env   # 填入 API_KEY / BASE_URL / Model

# 4. 构建知识库（BGE-M3 + reranker 首次自动下载）
cd backend && python ingest.py

# 5. 启动，打开 http://localhost:8000
python app.py
```

> 模型加载在后台完成（约 30-40s），前端显示加载进度；启动加载用 `local_files_only=True`，模型缓存后不依赖网络。
