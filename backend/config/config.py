import os
from dotenv import load_dotenv

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

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTOR_DB_PATH = os.path.join(BACKEND_DIR, "vector_db")
UPLOAD_DIR = os.path.join(BACKEND_DIR, "uploads")

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
