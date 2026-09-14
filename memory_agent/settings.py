"""memory_agent 配置：真相源位置 + 派生索引位置。

真相源是 Markdown（见 docs/adr/0006）：可写 = 全局 KB 条目，只读 = 本仓库文本。
派生索引（Qdrant local mode）必须落在独立目录——local mode 独占锁，不能与
legal_web/vector_db 共用。
"""
import os

MEMORY_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(MEMORY_AGENT_DIR)

# 可写真相源：全局 KB（条目 + frontmatter）
KB_DIR = os.environ.get("AGENT_KB_DIR") or r"C:\Users\Tan\.config\opencode\knowledge"

# 只读语料根：本仓库（不含子模块）。后续票据再扩到其它项目仓库。
READONLY_ROOTS = [ROOT_DIR]

# 派生索引：独立 Qdrant 路径 + manifest
INDEX_DIR = os.environ.get("MEMORY_INDEX_DIR") or os.path.join(MEMORY_AGENT_DIR, "vector_db")
INDEX_DB_PATH = os.path.join(INDEX_DIR, "qdrant")
MANIFEST_PATH = os.path.join(INDEX_DIR, "manifest.json")
COLLECTION_NAME = "memory_entries"

# 条目级嵌入：单条超长时截断（按 section 切分的例外留待需要时再开）
MAX_ENTRY_CHARS = 6000
MAX_CORPUS_FILE_BYTES = 1_000_000
