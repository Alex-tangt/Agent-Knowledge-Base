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
# `MEMORY_READONLY_ROOTS` 可覆盖（os.pathsep 分隔的绝对/相对路径；空串 = 无只读语料）。
# sandbox/评测套件用它把索引限制在可写 KB 内，避免把整个代码仓库也索引进去（#16）。
def _readonly_roots() -> list[str]:
    raw = os.environ.get("MEMORY_READONLY_ROOTS")
    if raw is None:
        return [ROOT_DIR]
    return [os.path.abspath(part) for part in raw.split(os.pathsep) if part.strip()]


READONLY_ROOTS = _readonly_roots()

# 派生索引：代目录 + 指针（issue #13 / ADR-0011）。
# 每代是独立目录 INDEX_DIR/<gen>/{qdrant/,manifest.json}；CURRENT 是指针文件，
# 全量重建在**新代**里建好后用 os.replace 原子切换指针——中断只留下一个未接管的
# 代目录，旧代照常服务，绝不出现「空索引 + 陈旧 manifest」。
INDEX_DIR = os.environ.get("MEMORY_INDEX_DIR") or os.path.join(MEMORY_AGENT_DIR, "vector_db")
POINTER_NAME = "CURRENT"
COLLECTION_NAME = "memory_entries"

# 全量重建的分块大小：单次只嵌入 N 条，远小于 MCP 客户端几十秒的调用超时（D3）。
DEFAULT_REINDEX_BATCH = int(os.environ.get("MEMORY_REINDEX_BATCH", "16"))

# 条目级嵌入：单条超长时截断（按 section 切分的例外留待需要时再开）
MAX_ENTRY_CHARS = 6000
MAX_CORPUS_FILE_BYTES = 1_000_000

# 写入门禁：写前检索命中的余弦相似度 >= 此值即判为近似重复（只报告、不写）。
# 0.88 由 #16 在真实 KB 上校准（详见 docs/adr/0009 的「校准」小节与
# experiments/dedup-threshold-calibration/）：实测不同条目最近邻 ≤0.792、
# 精确重加最低 0.949，0.88 落在间隔中部，比原 0.92 多留出同义重加的捕获余量。
DEDUP_THRESHOLD = float(os.environ.get("MEMORY_DEDUP_THRESHOLD", "0.88"))


def warmup_on_start() -> bool:
    """服务启动时是否预热嵌入模型。

    默认**关**（issue #19）：BGE-M3 常驻约 3.9GB 私有内存，每个 opencode 会话都会拉起
    一份 MCP，无条件预热会把系统 commit 打满。改为惰性——首次真正检索时才加载。
    需要低延迟的场景可设 `MEMORY_WARMUP=1` 换回预热。
    """
    return os.environ.get("MEMORY_WARMUP", "0").strip().lower() in {"1", "true", "yes", "on"}
