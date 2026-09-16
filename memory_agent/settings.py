"""memory_agent 配置：真相源位置 + 只读仓库清单 + 派生索引位置。

真相源是 Markdown（见 docs/adr/0006）：可写 = 全局 KB 条目，只读 = 若干项目仓库的
Markdown 文档（带标签消歧义，见 docs/adr/0014）。
派生索引（Qdrant local mode）必须落在独立目录——local mode 独占锁，不能与
legal_web/vector_db 共用。

配置来源独立（#22 / ADR-0016）：本包用 `MEMORY_*` env 命名空间 + 自己的
`memory_agent/.env`，**不读 `legal_web/.env`**。core/llm 分层（ragcore/config）与之无关。
"""
import json
import os

from dotenv import load_dotenv

MEMORY_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(MEMORY_AGENT_DIR)

ENV_FILE = os.environ.get("MEMORY_ENV_FILE") or os.path.join(MEMORY_AGENT_DIR, ".env")


def load_env_file(path: str | None = None) -> bool:
    """加载 memory_agent 自己的 `.env`（#22）。

    - 只认 `memory_agent/.env`（可用 `MEMORY_ENV_FILE` 覆盖，或显式 `path`）；
      **绝不读 `legal_web/.env`**。
    - `override=False`：已存在的**进程环境变量优先**。
    - 文件不存在则无操作。返回是否真的读取了文件。
    """
    target = path or ENV_FILE
    if not os.path.isfile(target):
        return False
    return load_dotenv(target, override=False)


load_env_file()  # 必须在下面读取任何 os.environ 之前执行

# 可写真相源：全局 KB（条目 + frontmatter）
KB_DIR = os.environ.get("AGENT_KB_DIR") or r"C:\Users\Tan\.config\opencode\knowledge"

# 只读语料：若干**带标签的项目仓库文档**（只取 Markdown，不索引代码）。
# 标签用于消歧义——不同仓库里同名文件（README.md / CONTEXT.md）的相对路径会撞车，
# 故 source = "<label>/<rel>"，只读条目 id = "repo:<label>/<rel>"（见 corpus/loader.py）。
#
# 配置优先级（**运行时重读**，#36 / ADR-0025 D8/D9——不再 import 时定死）：
# 1. `MEMORY_READONLY_ROOTS` 显式设置（os.pathsep 分隔；**空串 = 无只读语料**）——
#    sandbox/评测套件用它把索引限制在可写 KB 内（#16）。
# 2. 否则读 gitignored 的 `readonly_repos.json`（`[{label, path, owner?}]`，相对路径按仓库根解析）。
# 3. 都没有 → 默认仅本仓库（自足、可发布；目录名即标签）。
#
# 显式 overlay（收录清单）独立在 `overlay.json`（`MEMORY_OVERLAY_CONFIG` 可覆盖路径）：
# `include` 追加路径模式（精确文件 / 窄 glob），`exclude` 收窄；运行时与注册表合并
# （注册表默认 ∪ overlay，见 ADR-0025 D8）。
DEFAULT_READONLY_CONFIG_FILE = os.path.join(MEMORY_AGENT_DIR, "readonly_repos.json")
DEFAULT_OVERLAY_CONFIG_FILE = os.path.join(MEMORY_AGENT_DIR, "overlay.json")
DEFAULT_READONLY_LABEL = os.path.basename(ROOT_DIR.rstrip("\\/")) or "repo"

# 可写 KB 的域所有者（#36 / ADR-0025 D3）；缺省 None = 不声明所有权。
KB_OWNER = os.environ.get("MEMORY_KB_OWNER") or None


def _readonly_config_file() -> str:
    """配置路径在调用时解析，便于测试用环境变量隔离机器本地配置。"""
    return os.environ.get("MEMORY_READONLY_REPOS_CONFIG") or DEFAULT_READONLY_CONFIG_FILE


def overlay_config_file() -> str:
    """显式 overlay（收录清单）路径；调用时解析（`MEMORY_OVERLAY_CONFIG` 可覆盖）。"""
    return os.environ.get("MEMORY_OVERLAY_CONFIG") or DEFAULT_OVERLAY_CONFIG_FILE


def _label_from_path(path: str) -> str:
    return os.path.basename(os.path.abspath(path).rstrip("\\/")) or "repo"


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _uniquify(labels: list[str]) -> list[str]:
    """重复标签加 `-2`、`-3` 后缀，保证 source / id 唯一。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
        out.append(label if seen[label] == 1 else f"{label}-{seen[label]}")
    return out


def _dedupe_items(items: list[dict]) -> list[dict]:
    """按路径去重 + 标签唯一化；返回 [{label, path, owner}]。"""
    deduped: list[dict] = []
    seen_paths: set[str] = set()
    for item in items:
        key = os.path.normcase(os.path.normpath(item["path"]))
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append(item)
    labels = _uniquify([item["label"] for item in deduped])
    return [dict(item, label=label) for item, label in zip(deduped, labels)]


def _default_readonly_item() -> dict:
    return {"label": DEFAULT_READONLY_LABEL, "path": ROOT_DIR, "owner": DEFAULT_READONLY_LABEL}


def _readonly_items() -> tuple[list[dict], bool]:
    """运行时解析只读来源：`(items, complete)`，items = [{label, path(abs), owner}]。

    `complete=False` 表示配置文件**存在但读不出/非法**——调用方据此保守处理「条目消失」
    （见 corpus/loader 与 MemoryIndex.refresh 的孤儿安全策略），不把整批条目当孤儿删掉。
    """
    raw = os.environ.get("MEMORY_READONLY_ROOTS")
    if raw is not None:
        items = []
        for part in raw.split(os.pathsep):
            if not part.strip():
                continue
            path = os.path.abspath(part)
            label = _label_from_path(path)
            items.append({"label": label, "path": path, "owner": label})
        return _dedupe_items(items), True

    config_file = _readonly_config_file()
    if not os.path.isfile(config_file):
        return _dedupe_items([_default_readonly_item()]), True

    data = _load_json(config_file)
    if isinstance(data, dict):
        data = data.get("roots")
    if not isinstance(data, list):
        return _dedupe_items([_default_readonly_item()]), False

    items: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(ROOT_DIR, path))
        path = os.path.abspath(path)
        label = str(item.get("label") or "").strip() or _label_from_path(path)
        owner = str(item.get("owner") or "").strip() or label
        items.append({"label": label, "path": path, "owner": owner})
    # 显式空清单 = 无只读来源（合法）；只有文件结构非法才算「不完整」。
    return _dedupe_items(items), True


def readonly_sources() -> tuple[list[dict], bool]:
    """运行时读取只读来源（label/path/owner）+ 配置完整性标志（#36）。"""
    return _readonly_items()


def _readonly_roots() -> list[tuple[str, str]]:
    """返回 [(label, 绝对路径)]（兼容旧形状；运行时一律用 `readonly_sources()`）。"""
    items, _ = _readonly_items()
    return [(item["label"], item["path"]) for item in items]


READONLY_ROOTS = _readonly_roots()  # 兼容旧引用；selection 已改为运行时重读

# 派生索引：代目录 + 指针（issue #13 / ADR-0011）。
# 每代是独立目录 INDEX_DIR/<gen>/{qdrant/,manifest.json}；CURRENT 是指针文件，
# 全量重建在**新代**里建好后用 os.replace 原子切换指针——中断只留下一个未接管的
# 代目录，旧代照常服务，绝不出现「空索引 + 陈旧 manifest」。
INDEX_DIR = os.environ.get("MEMORY_INDEX_DIR") or os.path.join(MEMORY_AGENT_DIR, "vector_db")
POINTER_NAME = "CURRENT"
COLLECTION_NAME = "memory_entries"

# 全量重建的分块大小：单次只嵌入 N 条，远小于 MCP 客户端几十秒的调用超时（D3）。
DEFAULT_REINDEX_BATCH = int(os.environ.get("MEMORY_REINDEX_BATCH", "16"))

# 检索策略（issue #24）：向量 + 关键词混合召回的候选池大小。
# 默认 14（#21）：实测 pool∈{12,14,16,20} 非单调——14 的 nDCG@10/recall@1/MRR 均高于 20，
# 且 rerank 延迟 ~13.3s→9.0s（−32%）；12 会把 rank 13–20 的 gold 挤出候选。证据
# experiments/rerank-latency-survey/{pool_validation,pool_latency_bench}.md。env 可回退。
RETRIEVAL_POOL = int(os.environ.get("MEMORY_RETRIEVAL_POOL", "14"))

# 是否在检索链路里启用交叉编码器重排（目标链路 = 向量 + 关键词 + rerank）。
# 默认**关**：daemon 已有 BGE-M3（~3.9GB），再加 reranker 会重演 #19 的内存压力。
# 评测 / 需要目标链路的场景显式打开。模型名可覆盖（复用 ragcore RerankerService）。
RERANK_ENABLED = os.environ.get("MEMORY_RERANK", "0").strip().lower() in {"1", "true", "yes", "on"}
RERANK_MODEL = os.environ.get("MEMORY_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
# 送进 reranker 的正文截断长度（字符）。BGE reranker 默认 max_seq_length=8192，
# 若喂整条 6000 字条目，单条查询要 ~3.5 分钟（实测 26 条 226s）；截到 512 字约 15s。
# 记忆条目的相关性信号集中在标题 + 开头，截断几乎不损排序。
RERANK_MAX_CHARS = int(os.environ.get("MEMORY_RERANK_MAX_CHARS", "512"))

# ---- 网络化 store（共享平面，issue #33 / ADR-0025 D16）----
# 配了 `MEMORY_STORE_URL` 时，store 工厂切到网络化 Qdrant（**自建服务或云托管同一适配器**）：
# `path=`（本地嵌入）/ `url=`（自建服务）/ `url=+api_key=`（云托管）是同一套 API。
# `api_key` 是凭据：只从进程环境 / gitignored `.env` 读，**绝不落盘 / 落日志**（#25）。
STORE_URL = os.environ.get("MEMORY_STORE_URL") or None
STORE_API_KEY = os.environ.get("MEMORY_STORE_API_KEY") or None
STORE_COLLECTION = os.environ.get("MEMORY_STORE_COLLECTION") or COLLECTION_NAME
# 检索走 store 原生 hybrid（服务端 prefetch + fusion）；关掉退回纯 dense。
STORE_HYBRID = os.environ.get("MEMORY_STORE_HYBRID", "1").strip().lower() in {
    "1", "true", "yes", "on"}
# 原生融合方式（ADR-0019 D6：分数/阈值按平面；实测见
# experiments/networked-store-33/fusion_ablation.json）：rrf | dbsf | dense。
# 默认 rrf（#33 要求的 store 原生 hybrid）；当前语料上 dense 实测更好，可显式切换。
STORE_FUSION = os.environ.get("MEMORY_STORE_FUSION", "rrf").strip().lower() or "rrf"

# ---- 稀疏词法编码器（#40 / ADR-0019 D5/D7）：tfidf | bm25 ----
# `tfidf` = 现有零依赖自制词频哈希（memory_agent/memory/sparse.py，默认，不变）；
# `bm25`  = fastembed `Qdrant/bm25`（ADR-0019 D7 定了没落地的路线；**本地平面**客户端编码，
#           fastembed 为可选软依赖，只有选中才 import）。doc/query 权重不对称，接缝分开取。
SPARSE_BACKEND = os.environ.get("MEMORY_SPARSE_BACKEND", "tfidf").strip().lower() or "tfidf"
SPARSE_BM25_MODEL = os.environ.get("MEMORY_SPARSE_BM25_MODEL", "Qdrant/bm25")

# 共享 daemon 的 HTTP 端点（issue #19）：单实例常驻，多个 opencode 会话经 proxy 转发。
# 只绑本机回环；端口固定，proxy 用 /health 判断「是不是我们的 daemon 在跑」。
MCP_HTTP_HOST = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.environ.get("MEMORY_MCP_PORT", "8765"))
MCP_HTTP_PATH = os.environ.get("MEMORY_MCP_PATH", "/mcp")
# daemon 的日志：走 gitignored 的索引目录，便于排查（proxy 拉起 daemon 时子进程内重定向）。
DAEMON_LOG = os.environ.get("MEMORY_DAEMON_LOG") or os.path.join(INDEX_DIR, "daemon.log")


def daemon_pid_path(port: int) -> str:
    """daemon 的 PID 文件（按端口区分）：供 `proxy.py --stop` 手动停止。"""
    return os.path.join(os.path.dirname(DAEMON_LOG), f"daemon-{port}.pid")

# 条目级嵌入：单条超长时截断（按 section 切分的例外留待需要时再开）
MAX_ENTRY_CHARS = 6000
MAX_CORPUS_FILE_BYTES = 1_000_000

# ---- 网关：authn / authz（#32 / ADR-0018 D2）----
# 单租户阶段：身份 = 进程配置。形状按多租户设计——`MEMORY_AUTH_TOKENS` 给出
# `<token> -> <身份>` 的 JSON 映射后，daemon 在 `/mcp` 边界校验 Bearer token，
# 未带 / 无效即拒绝（可显式 `MEMORY_AUTH_REQUIRE_TOKEN=0` 关掉强制）。
# 身份绝不来自可伪造的 header 字段（proxy 只传输、不是信任源，D2.4）。
AUTH_PRINCIPAL = os.environ.get("MEMORY_AUTH_PRINCIPAL", "local")
AUTH_TENANT = os.environ.get("MEMORY_AUTH_TENANT") or None
AUTH_ROLE = os.environ.get("MEMORY_AUTH_ROLE", "owner")
# 逗号分隔的允许集；缺省 = 全集（本地单租户默认不限制）。
AUTH_CLASSIFICATIONS = os.environ.get("MEMORY_AUTH_CLASSIFICATIONS", "")
AUTH_RESIDENCIES = os.environ.get("MEMORY_AUTH_RESIDENCIES", "")
# 可写域所有权（#36 / ADR-0025 D3）：逗号分隔的 owner 集合；空 = 不限制（单租户默认）。
AUTH_OWNERS = os.environ.get("MEMORY_AUTH_OWNERS", "")
AUTH_TOKENS = os.environ.get("MEMORY_AUTH_TOKENS", "")
AUTH_REQUIRE_TOKEN = os.environ.get("MEMORY_AUTH_REQUIRE_TOKEN", "")
# 审计 JSONL（gitignored）：记录 tools/call + 身份，绝不写凭证。
AUDIT_LOG = os.environ.get("MEMORY_AUDIT_LOG") or os.path.join(INDEX_DIR, "audit.log")

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
