# RAG Knowledge Base — 政策法规问答助手

基于 **FastAPI + Qdrant + BGE-M3 + DeepSeek API** 的检索增强生成（RAG）问答系统，
面向「政策法规」垂直领域。

## 快速开始

```bash
# 1. 激活虚拟环境
venv\Scripts\activate          # Windows

# 2. 安装依赖
pip install -r backend/requirements.txt

# 3. 配置 .env（复制模板后填写 API Key）
cp backend/.env.example backend/.env

# 4. 构建知识库
cd backend && python ingest.py

# 5. 启动
python app.py
# 打开 http://localhost:8000
```

## 技术栈

| 模块 | 选用 | 说明 |
| ---- | ---- | ---- |
| 后端框架 | FastAPI + uvicorn | SSE 流式接口 + 静态前端托管 |
| 前端 | 原生 HTML/CSS/JS（ES 模块） | 零构建步骤 |
| 向量库 | Qdrant（本地持久化） | 支持多集合、多知识库隔离 |
| 大模型 | DeepSeek API（OpenAI 兼容） | 通过 `.env` 配置 |
| Embedding | BAAI/bge-m3（1024-dim） | sentence-transformers 本地运行 |
| Reranker | BAAI/bge-reranker-v2-m3 | Cross-encoder 重排序 |
| Agent | LangGraph | 意图识别 + 知识库自动路由 |
| 文档处理 | LangChain（文档加载/切分） | 法条感知分块 |

## 目录结构

```
RAG Knowledge Base/
├── backend/
│   ├── app.py                     # FastAPI 入口
│   ├── api/routes.py              # API 路由
│   ├── config/config.py           # 配置
│   ├── models/schemas.py          # 请求/响应模型
│   ├── agents/
│   │   ├── router_graph.py        # LangGraph 智能路由
│   │   └── session_memory.py      # 对话记忆
│   ├── services/
│   │   ├── rag_service.py         # RAG 核心 pipeline
│   │   ├── chat_service.py        # LLM-only 对比模式
│   │   ├── vector_store_service.py # Qdrant 封装
│   │   ├── document_service.py    # 文档加载/分块
│   │   ├── reranker_service.py    # Cross-encoder 重排序
│   │   ├── local_embedding_service.py
│   │   ├── kb_registry.py         # 多知识库注册
│   │   └── langsmith_service.py   # 可选追踪
│   ├── utils/model_status.py      # 模型加载状态
│   ├── ingest.py                  # 构建知识库
│   └── requirements.txt
├── frontend/                      # 静态前端
│   ├── index.html
│   ├── styles.css
│   ├── script.js                  # 主控制器
│   └── js/                        # 功能模块
├── data/raw/                      # 21 篇法规文档
└── tests/                         # 评测脚本
```

## 功能特性

- **RAG 问答**：检索相关知识片段 → 基于上下文生成回答，带来源引用 [n]
- **混合检索**：向量语义 + 法条编号匹配 + 锚点关键词过滤
- **Reranker 重排序**：Cross-encoder 精排，显著提升精确率
- **无依据拒答**：检索不相关时明确拒答，不编造
- **查询重写**：LLM 压缩长问题为检索友好格式
- **多知识库**：按领域分库，自动路由或手动切换
- **LLM-only 模式**：关闭 RAG 开关，与大模型直接对话对比
- **加载进度**：启动时显示模型加载进度，就绪后自动启用对话
