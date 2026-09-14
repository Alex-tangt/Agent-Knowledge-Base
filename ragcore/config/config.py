import os
from dotenv import load_dotenv

# 目录锚点：ragcore/config/config.py 向上三级 = 仓库根
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAGCORE_DIR = os.path.join(ROOT_DIR, "ragcore")
LEGAL_WEB_DIR = os.path.join(ROOT_DIR, "legal_web")

# .env 位于适配层 legal_web/；显式锚定，不依赖启动目录
load_dotenv(os.path.join(LEGAL_WEB_DIR, ".env"))
load_dotenv()

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY环境变量未设置！请检查.env文件配置")

BASE_URL = os.getenv("BASE_URL")
if not BASE_URL:
    raise ValueError("BASE_URL环境变量未设置！请检查.env文件配置")

MODEL = os.getenv("Model")
if not MODEL:
    raise ValueError("MODEL环境变量未设置！请检查.env文件配置")

LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "rag-knowledge-base")
LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "true").lower() == "true"

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
