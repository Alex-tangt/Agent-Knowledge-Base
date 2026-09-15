"""core 层配置：目录锚点 + 模型名 + 检索阈值。

**本层无密钥、import 不校验**：路径 / 模型名 / 阈值都是无凭证即可用的值，任何进程
（含只做检索、不用 LLM 的 `memory_agent`）都能安全 import。

LLM 凭证（API_KEY / BASE_URL / Model）与可选的 LangSmith 配置在 `config/llm.py`——
那一层惰性加载 `legal_web/.env` 并只在构造 LLM client 时校验。分层契约见 ADR-0016。
"""
import os

# 目录锚点：ragcore/config/config.py 向上三级 = 仓库根
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAGCORE_DIR = os.path.join(ROOT_DIR, "ragcore")
LEGAL_WEB_DIR = os.path.join(ROOT_DIR, "legal_web")

VECTOR_DB_PATH = os.environ.get("VECTOR_DB_PATH") or os.path.join(LEGAL_WEB_DIR, "vector_db")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR") or os.path.join(LEGAL_WEB_DIR, "uploads")

ARTICLE_MAX_CHARS = 800

ADAPTIVE_POOL = 20
ADAPTIVE_MAX = 8
ADAPTIVE_FACTOR = 1.8

RELEVANCE_THRESHOLD = 0.85  # post-reranker 距离（越低越相关），设高避免误拒

USE_LOCAL_EMBEDDINGS = True
LOCAL_EMBEDDING_MODEL = "BAAI/bge-m3"

USE_LOCAL_RERANKER = True
LOCAL_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

QDRANT_COLLECTION_NAME = "documents"

API_PREFIX = "/api"
CORS_ORIGINS = ["*"]
