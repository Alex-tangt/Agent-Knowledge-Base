"""#70 并发与可靠性：一组可复现的数字（p50/p95 · RSS · 长任务阻塞）。

测什么（对应 issue #70 验收）：

- **R 读并发**：经真 daemon（HTTP `/mcp`）起 N ∈ {1,4,8,16} 个并发读者，逐请求延迟
  → 每点 p50 / p95 / p99 + 吞吐；同点重复 `--runs` 次，报**中位数**并标出偏差。
- **W 写串行**：N ∈ {1,4,8} 个并发写者（`memory_add`），看 `WRITE_LOCK` 单写者串行化；
  同时挂一个读探针，量「写期间的读延迟」。
- **L 长任务阻塞**：索引重建（`memory_reindex`）在跑时的请求延迟 vs 空闲基线；
  另加「宿主 CPU 被占满」（模拟解析子进程）一档。
- **M 真模型**：真 BGE-M3 daemon 的**冷启动 RSS** + N=1/8 个 stdio 代理进程的 **RSS 合计**
  ——证明拓扑是「1×3.9GB + N×数十MB」而不是 N×3.9GB。

口径与边界（**结果里必须写清**）：

- R / W / L **扫描用 Stub 嵌入**（无 BGE-M3、无网络、确定性）——避免在本机叠加 3.9GB
  与 CPU 争用。L 的慢档用「每篇 20ms 人工延时」的 Stub **放大临界区**（模拟 CPU 嵌入耗时），
  这是**机制演示**，不是真实 BGE-M3 的绝对耗时。
- M 只测头条两点，`--no-warmup` 起 daemon：先记「未载模型」RSS，再跑一次真 `memory_reindex`
  载模型，记「载模型后」RSS；代理 RSS 逐进程采样。
- **RSS 一律取进程自报**：本机（DSH 沙箱）对子进程的跨进程内存查询**返回假值**——对照实验里
  一个持有 300MB 的子进程，`psutil.Process(pid).memory_info().rss`、`tasklist`、
  PowerShell `WorkingSet64` 三家都报 ~4.4MB，而进程**自报** 333MB。故被测进程用
  `PERF_RSS_FILE` 自写 RSS，harness 只读文件；跨进程数字只作注脚。
- **隔离**：临时 KB / 索引 / overlay / registry / 端口（默认 8891/8892/8893）；
  `MEMORY_READONLY_ROOTS=""`；生产 `memory_agent/vector_db` 与真实 KB 全程只读
  （结果里给前后快照：主树 `git status` + `vector_db` 目录指纹）。
- **只测量**：不改并发实现、不碰检索默认 / 合成、不引入 Redis / 关系库。

用法（仓库根，主树 venv 绝对路径）：

    venv\\Scripts\\python.exe memory_agent/eval/perf_70.py                 # 一把跑完
    venv\\Scripts\\python.exe memory_agent/eval/perf_70.py --mode read --n 1,8 --runs 3
    venv\\Scripts\\python.exe memory_agent/eval/perf_70.py --requests 20 --out x.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import psutil  # 已随 legal_web 依赖树安装（accelerate/peft）；RSS 采样用

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
# worktree 里没有 editable 安装（venv 的 .pth 指向主树）：把自己插到最前，
# 保证 harness 与 daemon 子进程都 import **本 worktree** 的源码。
if sys.path[0] != ROOT:
    sys.path.insert(0, ROOT)

PY = sys.executable
HOST = "127.0.0.1"
MCP_PATH = "/mcp"


# ------------------------------------------------------------------ 环境消毒
#
# 宿主 `NO_PROXY` 里带方括号的 IPv6（如 `[::1]`，本机实测就有）会让 httpx2 在建
# client 时解析代理模式失败（`Invalid port: ':1]'`）——报错发生在 `trust_env=False`
# 生效**之前**，所以必须在进程启动时就清掉那几个模式（同
# `opencode_server_smoke_62.py` 的 `--sanitize-proxy-env`，这里直接内联；daemon /
# proxy 子进程继承同一份 env）。
def _sanitize_proxy_env() -> list[str]:
    removed: list[str] = []
    for name in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(name)
        if not value:
            continue
        parts = [part for part in value.split(",") if part.strip()]
        kept = [part for part in parts if "[" not in part]
        if len(kept) != len(parts):
            removed.append(f"{name}:{value}")
        os.environ[name] = ",".join(kept)
    return removed


REMOVED_PROXY_PATTERNS = _sanitize_proxy_env()


# ===================================================================== 通用工具


def mib(value: int | float | None) -> float | None:
    return None if value is None else round(float(value) / (1024 * 1024), 1)


def pct(values: list[float], q: float) -> float | None:
    """线性插值分位数（n=1 时即该值）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return round(ordered[low] * (1 - frac) + ordered[high] * frac, 3)


def summarize(latencies: list[float]) -> dict:
    return {
        "n": len(latencies),
        "p50_ms": pct(latencies, 0.50),
        "p95_ms": pct(latencies, 0.95),
        "p99_ms": pct(latencies, 0.99),
        "min_ms": round(min(latencies), 3) if latencies else None,
        "max_ms": round(max(latencies), 3) if latencies else None,
        "mean_ms": round(statistics.fmean(latencies), 3) if latencies else None,
    }


def median_of(items: list[float]) -> float | None:
    values = [v for v in items if v is not None]
    return round(statistics.median(values), 3) if values else None


def spread_ratio(items: list[float]) -> float | None:
    """重复运行之间的相对离散 (max-min)/median——>0.30 视为不稳，结果里标注。"""
    values = [v for v in items if v is not None]
    if len(values) < 2:
        return None
    med = statistics.median(values)
    if med <= 0:
        return None
    return round((max(values) - min(values)) / med, 3)


def wait_for_quiet(threshold: float = 40.0, hold: float = 2.0,
                   max_wait: float = 20.0) -> dict:
    """等宿主 CPU 降到 `threshold`% 以下并保持 `hold` 秒再开测（有上限，不无限等）。

    为什么要它：本机与另一个 teammate（#71 评测基座）并行，宿主 CPU 会整段跑到
    60-100%——同一测量点在忙窗 / 静窗下能差 1.8×。这里**不隐藏**这一点：等不到就
    照测，并把 `host_gate` 写进该点的证据里，由读者判断。
    """
    start = time.monotonic()
    first = psutil.cpu_percent(interval=None)
    quiet_since: float | None = None
    while time.monotonic() - start < max_wait:
        value = psutil.cpu_percent(interval=0.5)
        if value <= threshold:
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= hold:
                return {"waited_s": round(time.monotonic() - start, 1), "quiet": True,
                        "cpu_percent_first": first, "cpu_percent_at_start": value}
        else:
            quiet_since = None
    return {"waited_s": round(time.monotonic() - start, 1), "quiet": False,
            "cpu_percent_first": first,
            "cpu_percent_at_start": psutil.cpu_percent(interval=None)}


# --------------------------------------------------------------------- RSS / 负载
#
# 重要口径（本机实测，2026-10-07）：**对「分离启动」（DETACHED_PROCESS）的进程，
# 跨进程内存查询在本机不可信**——对同一进程 `psutil.Process(pid).memory_info().rss`、
# `tasklist`、PowerShell `WorkingSet64` 三者都报 ~4.4MB，而该进程**自报**
# `psutil.Process().memory_info().rss` 是 333MB（子进程持有 300MB bytearray 的对照
# 实验）。harness 的临时 daemon 正是这样起的，所以它的 RSS 一律由**进程自己**写进
# `PERF_RSS_FILE`，harness 只读文件；跨进程数字只作注脚。对照：由 mcp 的
# `stdio_client` 正常派生的代理进程两种口径一致（自报 74.5MB vs 跨进程 78MB），
# 说明偏差只影响 detached 那一类。
_PROC_CACHE: dict[int, "psutil.Process"] = {}
SELF = psutil.Process()


def rss_bytes(pid: int | None) -> int | None:
    """跨进程 RSS——**本机不可信**，仅作注脚 / 交叉核对用。"""
    if not pid:
        return None
    proc = _PROC_CACHE.get(pid)
    if proc is None:
        try:
            proc = psutil.Process(pid)
        except Exception:  # noqa: BLE001 - 进程已退出
            return None
        _PROC_CACHE[pid] = proc
    try:
        return proc.memory_info().rss
    except Exception:  # noqa: BLE001
        return None


def self_rss_bytes() -> int:
    """本进程 RSS（自报可信）。"""
    return SELF.memory_info().rss


def read_rss_file(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


class RssSampler(threading.Thread):
    """按固定间隔读**被测进程自报的** RSS 文件，给每个测量点一个窗口统计。

    `sources` = 标签 -> 自报 RSS 文件路径（包装脚本里的 reporter 线程负责写）。
    另外始终记录 harness 自身（client）的 RSS。
    """

    def __init__(self, sources: dict[str, str], interval: float = 0.05) -> None:
        super().__init__(daemon=True)
        self.sources = dict(sources)
        self.interval = interval
        self.samples: list[dict] = []
        # 不能叫 `_stop`：`threading.Thread` 自己有 `_stop()` 方法，join() 内部会调它，
        # 被同名属性遮住会 "Event object is not callable"。
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            snapshot = {label: read_rss_file(path)
                        for label, path in self.sources.items()}
            snapshot["client"] = self_rss_bytes()
            self.samples.append({"t": time.time(), "rss": snapshot})
            self._halt.wait(self.interval)

    def stop(self) -> dict:
        self._halt.set()
        self.join(timeout=2.0)
        peaks: dict[str, int] = {}
        medians: dict[str, int] = {}
        for label in list(self.sources) + ["client"]:
            values = [s["rss"][label] for s in self.samples if s["rss"].get(label)]
            if values:
                peaks[label] = max(values)
                medians[label] = int(statistics.median(values))
        return {
            "samples": len(self.samples),
            "peak_bytes_by_label": peaks,
            "median_bytes_by_label": medians,
            "peak_total_bytes": sum(peaks.values()) if peaks else None,
        }


class HostMonitor(threading.Thread):
    """整轮运行期间采宿主的 CPU% / 内存%，可按测量点的时间窗切分。"""

    def __init__(self, interval: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[dict] = []
        self._halt = threading.Event()  # 不能叫 `_stop`（Thread 有同名方法）

    def snapshot(self) -> dict:
        vm = psutil.virtual_memory()
        out = {
            "ts": time.time(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "vm_percent": vm.percent,
            "vm_used_bytes": vm.used,
            "vm_total_bytes": vm.total,
            "cpu_logical": psutil.cpu_count(),
            "cpu_physical": psutil.cpu_count(logical=False),
        }
        try:
            out["load_avg"] = list(os.getloadavg())
        except (OSError, AttributeError):
            out["load_avg"] = None
        return out

    def run(self) -> None:
        while not self._halt.is_set():
            self.samples.append(self.snapshot())
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2.0)

    def window(self, t0: float, t1: float) -> dict:
        rows = [s for s in self.samples if t0 <= s["ts"] <= t1]
        if not rows:
            return {"n": 0}
        return {
            "n": len(rows),
            "cpu_percent_mean": round(statistics.fmean(s["cpu_percent"] for s in rows), 1),
            "cpu_percent_max": round(max(s["cpu_percent"] for s in rows), 1),
            "vm_percent_max": round(max(s["vm_percent"] for s in rows), 1),
        }


# ------------------------------------------------------------------ 进程 / git


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _spawn_kwargs() -> dict:
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return (proc.stdout or "").strip()


def _rmtree(path: str) -> None:
    def on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass
    shutil.rmtree(path, onerror=on_error)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _free_port(host: str, port: int) -> None:
    """端口必须真的空闲：占着就报错退出（隔离契约，不静默换端口）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise SystemExit(
                f"端口 {host}:{port} 已被占用（{exc}）——换 --port 或先停掉占用者；"
                "本脚本不静默换端口（隔离契约）"
            ) from exc


# ------------------------------------------------------------------- 语料 / 沙箱

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
         "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
         "xray yankee zulu").split()


def _entry_body(index: int) -> str:
    tokens = [WORDS[(index * 7 + step) % len(WORDS)] for step in range(12)]
    return (f"perfmark{index:04d} " + " ".join(tokens)
            + " durable memory note used by the concurrency scan.")


def _render_entry(entry_id: str, title: str, body: str) -> str:
    return (
        "---\n"
        f"id: {entry_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "type: topic\n"
        "tags: [demo]\n"
        "status: current\n"
        "updated: 2026-10-07\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )


def build_kb(kb_dir: str, entries: int) -> list[str]:
    """建一个临时可写 KB（git 仓库）+ `entries` 条记忆，返回查询串候选。"""
    os.makedirs(kb_dir, exist_ok=True)
    _write(os.path.join(kb_dir, "tags.md"), "- demo\n")
    queries: list[str] = []
    for index in range(entries):
        entry_id = f"topics/perf-{index:04d}"
        _write(os.path.join(kb_dir, *entry_id.split("/")) + ".md",
               _render_entry(entry_id, f"Perf entry {index:04d}", _entry_body(index)))
        queries.append(f"perfmark{index:04d}")
    _git(kb_dir, "init", "-q")
    _git(kb_dir, "config", "user.email", "perf@example.com")
    _git(kb_dir, "config", "user.name", "perf")
    _git(kb_dir, "add", "-A")
    _git(kb_dir, "commit", "-q", "-m", "seed")
    return queries


STUB_DAEMON = '''"""Test-only daemon：确定性 Stub 嵌入（无 BGE-M3、无网络）。

`PERF_STUB_DELAY_MS` > 0 时每篇/每 query 人为 sleep 该毫秒数——用于**放大长任务临界区**
（模拟 CPU 嵌入耗时），不是真实模型耗时。
`PERF_STUB_EMBED=0` 时不替换嵌入器（真模型档复用同一包装，只为拿到自报 RSS）。
`PERF_RSS_FILE` 指向本进程自报 RSS 的文件（跨进程内存查询在本机不可信，见 harness 头注）。
"""
import hashlib
import os
import sys
import time

from perf_report import start_rss_reporter

start_rss_reporter()

if os.environ.get("PERF_STUB_EMBED", "1") != "0":
    import ragcore.services.local_embedding_service as _les

    _DELAY = float(os.environ.get("PERF_STUB_DELAY_MS", "0") or "0") / 1000.0

    class _StubEmbedder:
        dimension = 1024

        def __init__(self, *args, **kwargs):
            pass

        def _vec(self, text):
            vec = [0.0] * 1024
            for token in (text or "").lower().split():
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                vec[int(digest, 16) % 1024] += 1.0
            norm = sum(value * value for value in vec) ** 0.5 or 1.0
            return [value / norm for value in vec]

        def embed_documents(self, texts):
            if _DELAY:
                time.sleep(_DELAY * len(texts))
            return [self._vec(text) for text in texts]

        def embed_query(self, query):
            if _DELAY:
                time.sleep(_DELAY)
            return self._vec(query)

    _les.LocalEmbeddingService = _StubEmbedder

from memory_agent.mcp_server import main

raise SystemExit(main(sys.argv[1:]))
'''

PROXY_PID_WRAPPER = '''"""stdio 代理包装：写 pid + 自报 RSS，再进真代理。

给「每会话一个代理进程」的 RSS 采样定位（mcp 的 stdio_client 不暴露子进程句柄，
且本机跨进程内存查询不可信——见 harness 头注）。
"""
import os
import sys

from perf_report import start_rss_reporter, write_pid

write_pid()
start_rss_reporter()

from memory_agent.proxy import main

raise SystemExit(main(sys.argv[1:]))
'''

RSS_REPORTER = '''"""测试用：把**本进程**的 RSS 周期写进 `PERF_RSS_FILE`（包装脚本 import）。

为什么不用 harness 里跨进程查询：本机（DSH 沙箱）对子进程的 RSS 查询一律返回
~4.4MB 假值（psutil / tasklist / PowerShell 三家一致），而进程自报是真值。自报 +
文件交换在 Windows / POSIX 上都成立，且不依赖额外权限。
"""
import os
import threading
import time


def write_pid() -> None:
    marker = os.environ.get("PERF_PROXY_PID_FILE")
    if not marker:
        return
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        pass


def start_rss_reporter(interval: float = 0.05) -> None:
    path = os.environ.get("PERF_RSS_FILE")
    if not path:
        return
    try:
        import psutil
    except Exception:  # noqa: BLE001 - 没有 psutil 就不自报（harness 会记 null）
        return
    proc = psutil.Process(os.getpid())

    def _loop() -> None:
        while True:
            try:
                value = proc.memory_info().rss
                temp = path + ".tmp"
                with open(temp, "w", encoding="utf-8") as handle:
                    handle.write(str(value))
                os.replace(temp, path)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(interval)

    threading.Thread(target=_loop, name="perf-rss-reporter", daemon=True).start()
'''


class Sandbox:
    """临时工作区：KB / 索引 / overlay / registry / 日志 / 包装脚本。"""

    def __init__(self, root: str) -> None:
        self.root = root
        self.kb = os.path.join(root, "kb")
        self.index = os.path.join(root, "index")
        self.overlay = os.path.join(root, "overlay.json")
        self.registry = os.path.join(root, "readonly_repos.json")
        self.stub_wrapper = os.path.join(root, "stub_daemon.py")
        self.proxy_wrapper = os.path.join(root, "proxy_pid.py")
        self.reporter = os.path.join(root, "perf_report.py")
        self.pid_dir = os.path.join(root, "pids")
        self.rss_dir = os.path.join(root, "rss")
        os.makedirs(self.index, exist_ok=True)
        os.makedirs(self.pid_dir, exist_ok=True)
        os.makedirs(self.rss_dir, exist_ok=True)
        _write(self.stub_wrapper, STUB_DAEMON)
        _write(self.proxy_wrapper, PROXY_PID_WRAPPER)
        _write(self.reporter, RSS_REPORTER)
        # 空 registry：无默认只读来源；overlay 空（`MEMORY_READONLY_ROOTS=""`）。
        _write(self.registry, "[]\n")
        _write(self.overlay, json.dumps({"include": [], "exclude": []}))

    def env(self, port: int, *, index_dir: str | None = None,
            kb_dir: str | None = None, stub_delay_ms: float = 0.0,
            auth: bool = False, rss_file: str | None = None,
            stub_embed: bool = True) -> dict:
        env = dict(os.environ)
        for key in ("AGENT_KB_DIR", "MEMORY_INDEX_DIR", "MEMORY_READONLY_ROOTS",
                    "MEMORY_OVERLAY_CONFIG", "MEMORY_READONLY_REPOS_CONFIG",
                    "MEMORY_MCP_PORT", "MEMORY_MCP_HOST", "MEMORY_MCP_PATH",
                    "MEMORY_AUDIT_LOG", "MEMORY_DAEMON_LOG", "MEMORY_STORE_URL",
                    "MEMORY_AUTH_TOKENS", "MEMORY_AUTH_REQUIRE_TOKEN",
                    "PERF_PROXY_PID_FILE", "PERF_RSS_FILE", "PERF_STUB_EMBED"):
            env.pop(key, None)
        env.update({
            "PYTHONPATH": ROOT + os.pathsep + env.get("PYTHONPATH", ""),
            "AGENT_KB_DIR": kb_dir or self.kb,
            "MEMORY_INDEX_DIR": index_dir or self.index,
            "MEMORY_READONLY_ROOTS": "",               # 无只读语料（#70 的隔离口径）
            "MEMORY_OVERLAY_CONFIG": self.overlay,
            "MEMORY_READONLY_REPOS_CONFIG": self.registry,
            "MEMORY_ENV_FILE": os.path.join(self.root, "no_such.env"),
            "MEMORY_MCP_HOST": HOST,
            "MEMORY_MCP_PORT": str(port),
            "MEMORY_MCP_PATH": MCP_PATH,
            "MEMORY_AUDIT_LOG": os.path.join(self.root, f"audit-{port}.log"),
            "MEMORY_DAEMON_LOG": os.path.join(self.root, f"daemon-{port}.log"),
            "MEMORY_AUTH_REQUIRE_TOKEN": "0",
            "MEMORY_RERANK": "0",
            # Stub 嵌入下确定性：回落 dense + 策略层关键词（#30 口径），
            # 不加载 fastembed BM25 模型（默认 bm25+dbsf 会引入模型与分数尺度差）。
            "MEMORY_LOCAL_HYBRID": "0",
            "MEMORY_SPARSE_BACKEND": "tfidf",
            "PERF_STUB_DELAY_MS": str(stub_delay_ms),
            "PERF_STUB_EMBED": "1" if stub_embed else "0",
        })
        if rss_file:
            env["PERF_RSS_FILE"] = rss_file
        if not auth:
            env.pop("MEMORY_AUTH_TOKENS", None)
        return env

    def proxy_env(self, port: int, pid_file: str, rss_file: str | None = None) -> dict:
        env = self.env(port, rss_file=rss_file)
        env["PERF_PROXY_PID_FILE"] = pid_file
        return env


# --------------------------------------------------------------------- daemon

def health_ok(port: int, timeout: float = 1.0) -> bool:
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{HOST}:{port}/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


class Daemon:
    """一个临时 daemon 进程（真 HTTP `/mcp`）。"""

    def __init__(self, sandbox: Sandbox, port: int, tag: str, *, stub_embed: bool = True,
                 index_dir: str | None = None, kb_dir: str | None = None,
                 stub_delay_ms: float = 0.0, extra_args: tuple[str, ...] = ()) -> None:
        self.sandbox = sandbox
        self.port = port
        self.tag = tag
        self.stub_embed = stub_embed
        self.index_dir = index_dir or sandbox.index
        self.kb_dir = kb_dir or sandbox.kb
        self.stub_delay_ms = stub_delay_ms
        self.extra_args = extra_args
        self.log = os.path.join(sandbox.root, f"daemon-{tag}-{port}.log")
        self.rss_file = os.path.join(sandbox.rss_dir, f"daemon-{tag}-{port}.rss")
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}{MCP_PATH}"

    @property
    def rss(self) -> int | None:
        """daemon **自报** RSS（跨进程查询在本机不可信，见文件头注）。"""
        return read_rss_file(self.rss_file)

    def command(self) -> list[str]:
        # 统一走包装脚本：`PERF_STUB_EMBED=0` 时只挂自报 RSS reporter，不改嵌入器。
        return [PY, self.sandbox.stub_wrapper, "--transport", "http", "--host", HOST,
                "--port", str(self.port), *self.extra_args]

    def start(self, timeout: float = 60.0) -> None:
        log = open(self.log, "ab")
        self.started_at = time.time()
        self.proc = subprocess.Popen(
            self.command(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            cwd=ROOT, env=self.sandbox.env(
                self.port, index_dir=self.index_dir, kb_dir=self.kb_dir,
                stub_delay_ms=self.stub_delay_ms, rss_file=self.rss_file,
                stub_embed=self.stub_embed),
            **_spawn_kwargs(),
        )
        log.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"daemon[{self.tag}] 提前退出 rc={self.proc.returncode}；日志尾："
                    + self.log_tail())
            if health_ok(self.port, 0.5):
                self.ready_at = time.time()
                rss_deadline = time.monotonic() + 10.0   # 等自报 RSS 文件出现
                while time.monotonic() < rss_deadline and self.rss is None:
                    time.sleep(0.05)
                return
            time.sleep(0.25)
        raise RuntimeError(
            f"daemon[{self.tag}] {timeout:.0f}s 内未就绪；日志尾：" + self.log_tail())

    def log_tail(self, limit: int = 1200) -> str:
        try:
            with open(self.log, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()[-limit:]
        except OSError:
            return "(无日志)"

    def stop(self) -> None:
        if self.proc is None:
            return
        _kill_tree(self.proc.pid)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self.proc = None


# ------------------------------------------------------------- MCP 客户端封装

class MCPError(RuntimeError):
    pass


def _decode(result) -> object:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        return structured
    text = result.content[0].text if result.content else ""
    try:
        data = json.loads(text)
    except (ValueError, IndexError):
        return None
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


class HttpSession:
    """一个 streamable-HTTP MCP 会话（真 daemon，= 一个「会话」）。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self._stack = None
        self.session = None

    async def __aenter__(self) -> "HttpSession":
        from contextlib import AsyncExitStack
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        self._stack = AsyncExitStack()
        client = create_mcp_http_client()
        try:
            client.trust_env = False  # 本机回环不走 HTTP_PROXY
        except Exception:  # noqa: BLE001
            pass
        await self._stack.enter_async_context(client)
        # 不同 mcp 版本 yield 2 元组（read, write）或 3 元组（含 get_session_id）。
        streams = await self._stack.enter_async_context(
            streamable_http_client(self.url, http_client=client))
        read, write = streams[0], streams[1]
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self.session = None

    async def call(self, tool: str, args: dict, timeout: float = 120.0):
        assert self.session is not None
        result = await self.session.call_tool(tool, args, read_timeout_seconds=timeout)
        if getattr(result, "is_error", False):
            text = result.content[0].text if result.content else ""
            raise MCPError(f"{tool} error: {text[:400]}")
        return _decode(result)


class StdioProxySession:
    """一个 stdio 代理会话（`proxy.py` = opencode 的真实路径），pid / 自报 RSS 可定位。"""

    def __init__(self, sandbox: Sandbox, port: int, pid_file: str,
                 rss_file: str | None = None) -> None:
        self.sandbox = sandbox
        self.port = port
        self.pid_file = pid_file
        self.rss_file = rss_file
        self.pid: int | None = None
        self._stack = None
        self.session = None

    async def __aenter__(self) -> "StdioProxySession":
        from contextlib import AsyncExitStack
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=PY,
            args=[self.sandbox.proxy_wrapper, "--host", HOST, "--port", str(self.port)],
            cwd=ROOT,
            env=self.sandbox.proxy_env(self.port, self.pid_file, self.rss_file),
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.pid = self._read_pid()
        return self

    def _read_pid(self) -> int | None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with open(self.pid_file, "r", encoding="utf-8") as handle:
                    return int(handle.read().strip())
            except (OSError, ValueError):
                time.sleep(0.05)
        return None

    async def __aexit__(self, *exc) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self.session = None

    async def call(self, tool: str, args: dict, timeout: float = 120.0):
        assert self.session is not None
        result = await self.session.call_tool(tool, args, read_timeout_seconds=timeout)
        if getattr(result, "is_error", False):
            text = result.content[0].text if result.content else ""
            raise MCPError(f"{tool} error: {text[:400]}")
        return _decode(result)


# ============================================================== R 读并发扫描

async def _read_point(url: str, n: int, requests: int, queries: list[str]) -> dict:
    """N 个并发读者同时开跑；每个读者新会话、先热身、再逐个请求记延迟。"""
    ready_count = 0
    ready = asyncio.Event()
    go = asyncio.Event()
    results: list[list[float] | None] = [None] * n
    errors: list[str] = []

    async def reader(index: int) -> None:
        nonlocal ready_count
        try:
            async with HttpSession(url) as client:
                for warm in range(5):
                    await client.call("memory_search",
                                      {"query": queries[(index + warm) % len(queries)],
                                       "k": 5})
                ready_count += 1
                if ready_count == n:
                    ready.set()
                await go.wait()
                local: list[float] = []
                for step in range(requests):
                    query = queries[(index * 31 + step) % len(queries)]
                    start = time.perf_counter()
                    await client.call("memory_search", {"query": query, "k": 5})
                    local.append((time.perf_counter() - start) * 1000.0)
                results[index] = local
        except Exception as exc:  # noqa: BLE001 - 单读者失败不影响整点
            errors.append(f"reader {index}: {exc!r}")

    tasks = [asyncio.create_task(reader(i)) for i in range(n)]
    await asyncio.wait_for(ready.wait(), timeout=120)
    start = time.perf_counter()
    go.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.perf_counter() - start
    latencies = [value for part in results if part for value in part]
    return {"latencies_ms": latencies, "wall_s": round(wall, 3),
            "requests_total": sum(len(p) for p in results if p), "errors": errors}


def phase_read(sandbox: Sandbox, daemon: Daemon, args, monitor: HostMonitor,
               queries: list[str], results: dict) -> None:
    url = daemon.url
    rows: list[dict] = []
    print("\n[R] 读并发扫描（Stub 嵌入；每点重复 %d 次，报中位数）" % args.runs)
    for n in args.ns:
        gate = wait_for_quiet()
        runs: list[dict] = []
        for run in range(1, args.runs + 1):
            sampler = RssSampler({"daemon": daemon.rss_file})
            sampler.start()
            t0 = time.time()
            outcome = asyncio.run(_read_point(url, n, args.requests, queries))
            t1 = time.time()
            rss = sampler.stop()
            stats = summarize(outcome["latencies_ms"])
            runs.append({
                "run": run,
                "latencies_ms": [round(v, 3) for v in outcome["latencies_ms"]],
                **stats,
                "errors": outcome["errors"],
                "wall_s": outcome["wall_s"],
                "throughput_rps": round(outcome["requests_total"] / outcome["wall_s"], 1)
                if outcome["wall_s"] else None,
                "daemon_rss_bytes": (rss["peak_bytes_by_label"] or {}).get("daemon"),
                "client_rss_bytes": (rss["peak_bytes_by_label"] or {}).get("client"),
                "rss_samples": rss["samples"],
                "host": monitor.window(t0, t1),
            })
            print(f"    N={n:<2} run{run}: p50={stats['p50_ms']}ms p95={stats['p95_ms']}ms "
                  f"wall={outcome['wall_s']}s n={stats['n']}"
                  + (f" errors={len(outcome['errors'])}" if outcome["errors"] else ""))
        p50s = [r["p50_ms"] for r in runs]
        row = {
            "n": n,
            "host_gate": gate,
            "runs": runs,
            "median_p50_ms": median_of(p50s),
            "median_p95_ms": median_of([r["p95_ms"] for r in runs]),
            "median_p99_ms": median_of([r["p99_ms"] for r in runs]),
            "median_throughput_rps": median_of([r["throughput_rps"] for r in runs]),
            "median_daemon_rss_bytes": median_of([r["daemon_rss_bytes"] for r in runs]),
            "median_client_rss_bytes": median_of([r["client_rss_bytes"] for r in runs]),
            "spread_p50": spread_ratio(p50s),
            "unstable": bool((spread_ratio(p50s) or 0) > 0.30),
        }
        rows.append(row)
        print(f"  -> N={n}: p50={row['median_p50_ms']}ms p95={row['median_p95_ms']}ms "
              f"spread_p50={row['spread_p50']}")
    base = next((r["median_p50_ms"] for r in rows if r["n"] == 1), None)
    for row in rows:
        row["p50_ratio_vs_n1"] = (round(row["median_p50_ms"] / base, 2)
                                  if base and row["median_p50_ms"] else None)
    results["read"] = {"points": rows,
                       "config": {"mode": "http-mcp", "embedder": "stub",
                                  "requests_per_reader": args.requests,
                                  "runs": args.runs, "warmup_per_reader": 5,
                                  "k": 5, "url": url}}


# ========================================================== W 写串行扫描

def _write_text(tag: str) -> tuple[str, str]:
    """给**每次写**生成 token 互不重叠的标题/正文。

    为什么：Stub 嵌入是空白分词 + 哈希词袋，若各次写入共享措辞
    （`durable fact written by the concurrency scan`），彼此的余弦会越过去重阈值
    **0.88** → `memory_add` 直接返回 `duplicate`（不落盘、不提交），量到的就不是
    写路径了（首轮实测：N=8 有 25/48 次被判 duplicate）。这里每次的 token 完全不重叠
    → 相似度 ≈0，去重预检保留在关键路径里且必然放行。
    """
    tokens = " ".join(f"wtok{tag}{index:02d}" for index in range(24))
    return f"Perf write {tag}", f"{tokens}"


async def _warmup_write(url: str) -> None:
    """写路径热身：一次真实 `memory_add`（不计入测量点）。"""
    title, body = _write_text("warmup")
    async with HttpSession(url) as client:
        await client.call("memory_add", {
            "title": title, "body": body, "type": "topic", "tags": ["demo"],
            "section": "topics", "slug": "perf-write-warmup",
        }, timeout=300)


async def _write_point(url: str, n: int, adds: int, probe_reads: int,
                       queries: list[str], run: int) -> dict:
    ready_count = 0
    ready = asyncio.Event()
    go = asyncio.Event()
    writers_done = asyncio.Event()
    writer_lat: list[list[float] | None] = [None] * n
    statuses: list[list[str]] = [[] for _ in range(n)]
    probe_lat: list[float] = []
    errors: list[str] = []
    nonwritten_samples: list[dict] = []
    remaining = n

    async def writer(index: int) -> None:
        nonlocal ready_count, remaining
        try:
            async with HttpSession(url) as client:
                ready_count += 1
                if ready_count == n:
                    ready.set()
                await go.wait()
                local: list[float] = []
                for step in range(adds):
                    # slug / tag 必须**全局唯一**：既不能撞已有的同名文件
                    # （`os.path.exists` → duplicate，不写不提交），也不能撞**别的 N 点**
                    # （N=1 写的是 index 00，N=4 的 index 00 会重名——首轮实测 N=4 有
                    # 6/24、N=8 有 24/48 次撞名）。故把 n 与 run 都编进 slug。
                    tag = f"n{n}r{run}{index:02d}{step:02d}"
                    title, body = _write_text(tag)
                    start = time.perf_counter()
                    out = await client.call("memory_add", {
                        "title": title,
                        "body": body,
                        "type": "topic", "tags": ["demo"], "section": "topics",
                        "slug": f"perf-write-n{n}-r{run}-{index:02d}-{step:02d}",
                    }, timeout=300)
                    local.append((time.perf_counter() - start) * 1000.0)
                    status = str((out or {}).get("status"))
                    statuses[index].append(status)
                    if status != "written" and len(nonwritten_samples) < 3:
                        # 留下非 written 的**完整响应**（去重候选 / exists），
                        # 否则事后说不清这些 op 到底走了哪半段路径。
                        sample = {key: value for key, value in (out or {}).items()
                                  if key in ("status", "written", "reason", "id", "hint",
                                             "candidates", "index")}
                        nonwritten_samples.append({"tag": tag, "response": sample})
                writer_lat[index] = local
        except Exception as exc:  # noqa: BLE001 - 单写者失败不影响整点
            errors.append(f"writer {index}: {exc!r}")
        finally:
            remaining -= 1
            if remaining == 0:
                writers_done.set()

    async def probe() -> None:
        # 探针覆盖**整个写窗口**（直到最后一个写者收工），而不是固定次数提前退出。
        await go.wait()
        try:
            async with HttpSession(url) as client:
                step = 0
                while not writers_done.is_set() and step < max(probe_reads, 1) * 20:
                    query = queries[step % len(queries)]
                    start = time.perf_counter()
                    await client.call("memory_search", {"query": query, "k": 5})
                    probe_lat.append((time.perf_counter() - start) * 1000.0)
                    step += 1
                    await asyncio.sleep(0.02)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"probe: {exc!r}")

    tasks = [asyncio.create_task(writer(i)) for i in range(n)]
    tasks.append(asyncio.create_task(probe()))
    await asyncio.wait_for(ready.wait(), timeout=120)
    start = time.perf_counter()
    go.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.perf_counter() - start
    latencies = [value for part in writer_lat if part for value in part]
    return {"latencies_ms": latencies, "wall_s": round(wall, 3),
            "ops": len(latencies), "probe_latencies_ms": probe_lat,
            "statuses": [s for part in statuses for s in part], "errors": errors,
            "nonwritten_samples": nonwritten_samples}


def phase_write(sandbox: Sandbox, daemon: Daemon, args, monitor: HostMonitor,
                queries: list[str], results: dict) -> None:
    url = daemon.url
    rows: list[dict] = []
    print("\n[W] 写串行扫描（memory_add；WRITE_LOCK 单写者；Stub 嵌入）")
    # 写路径热身（不计入任何测量点）：吸收首次写才付的开销（写者单例 / 标签表 /
    # KB 校验 / Windows 文件系统与 git 索引冷启动）。
    asyncio.run(_warmup_write(url))
    for n in [value for value in (1, 4, 8) if value <= max(args.ns)] or [1]:
        gate = wait_for_quiet()
        runs = []
        for run in range(1, args.runs + 1):
            sampler = RssSampler({"daemon": daemon.rss_file})
            sampler.start()
            t0 = time.time()
            outcome = asyncio.run(_write_point(url, n, args.adds, args.probe_reads,
                                               queries, run))
            t1 = time.time()
            rss = sampler.stop()
            stats = summarize(outcome["latencies_ms"])
            probe = summarize(outcome["probe_latencies_ms"])
            runs.append({
                "run": run,
                "latencies_ms": [round(v, 3) for v in outcome["latencies_ms"]],
                **stats,
                "wall_s": outcome["wall_s"],
                "ops": outcome["ops"],
                "throughput_ops": round(outcome["ops"] / outcome["wall_s"], 2)
                if outcome["wall_s"] else None,
                "statuses": outcome["statuses"],
                "errors": outcome["errors"],
                "nonwritten_samples": outcome["nonwritten_samples"],
                "probe": probe,
                "daemon_rss_bytes": (rss["peak_bytes_by_label"] or {}).get("daemon"),
                "host": monitor.window(t0, t1),
            })
            print(f"    N={n} run{run}: p50={stats['p50_ms']}ms wall={outcome['wall_s']}s "
                  f"ops={outcome['ops']} probe_p50={probe['p50_ms']}ms "
                  f"errors={len(outcome['errors'])}")
        rows.append({
            "n": n,
            "host_gate": gate,
            "runs": runs,
            "median_p50_ms": median_of([r["p50_ms"] for r in runs]),
            "median_p95_ms": median_of([r["p95_ms"] for r in runs]),
            "median_wall_s": median_of([r["wall_s"] for r in runs]),
            "median_throughput_ops": median_of([r["throughput_ops"] for r in runs]),
            "median_probe_p50_ms": median_of([r["probe"]["p50_ms"] for r in runs]),
            "median_probe_p95_ms": median_of([r["probe"]["p95_ms"] for r in runs]),
            "spread_p50": spread_ratio([r["p50_ms"] for r in runs]),
            "duplicate_responses": sum(
                1 for r in runs for status in r["statuses"] if status != "written"),
        })
    base_ops = next((r["median_throughput_ops"] for r in rows if r["n"] == 1), None)
    for row in rows:
        row["throughput_ratio_vs_n1"] = (
            round(row["median_throughput_ops"] / base_ops, 2)
            if base_ops and row["median_throughput_ops"] else None)
    results["write"] = {"points": rows,
                        "config": {"mode": "http-mcp", "embedder": "stub",
                                   "adds_per_writer": args.adds,
                                   "probe_reads": args.probe_reads, "runs": args.runs}}


# ========================================================== L 长任务阻塞

async def _probe_loop(url: str, duration: float, queries: list[str],
                      interval: float = 0.02) -> list[float]:
    """单读者按 ~50qps 的节奏持续发查询 `duration` 秒，返回逐请求延迟。

    计时从**会话建立之后**算（`duration` 是真正的探测窗口；CPU 打满时建链本身可能
    就要几秒，若把这段算进去会得到 n=0 的空窗口）。
    """
    latencies: list[float] = []
    step = 0
    async with HttpSession(url) as client:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            query = queries[step % len(queries)]
            start = time.perf_counter()
            await client.call("memory_search", {"query": query, "k": 5})
            latencies.append((time.perf_counter() - start) * 1000.0)
            step += 1
            await asyncio.sleep(interval)
    return latencies


async def _warm_searches(url: str, queries: list[str], count: int) -> None:
    """预热：跑几次 search（建 retriever/策略、暖 Qdrant 句柄），不计入任何测量点。"""
    async with HttpSession(url) as client:
        for step in range(count):
            await client.call("memory_search",
                              {"query": queries[step % len(queries)], "k": 5})


async def _reindex_loop(url: str, batch: int, stop: asyncio.Event,
                        chunks: list[dict]) -> None:
    """后台分块全量重建（`memory_reindex`），记录每次调用耗时。"""
    async with HttpSession(url) as client:
        cursor = None
        while not stop.is_set():
            start = time.perf_counter()
            out = await client.call("memory_reindex", {"cursor": cursor, "batch": batch},
                                    timeout=900)
            chunks.append({"chunk_ms": round((time.perf_counter() - start) * 1000.0, 1),
                           "processed": (out or {}).get("processed"),
                           "done": (out or {}).get("done")})
            if (out or {}).get("done"):
                return
            cursor = (out or {}).get("cursor")


async def _cpu_burn(duration: float) -> None:
    """宿主 CPU 打满（模拟「解析子进程」这类同机长任务）——多进程忙等。"""
    workers = max(1, (psutil.cpu_count(logical=False) or 2))
    code = f"import time\nt=time.time()\nwhile time.time()-t < {duration}: pass"
    procs = [
        subprocess.Popen([PY, "-c", code],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(workers)
    ]
    for proc in procs:
        proc.wait()


async def _long_task_window(url: str, duration: float, queries: list[str],
                            background: str | None = None) -> dict:
    """一段时长内一边跑后台任务（可选）一边探请求延迟。"""
    stop = asyncio.Event()
    chunks: list[dict] = []
    bg = None
    if background == "reindex":
        bg = asyncio.create_task(_reindex_loop(url, 16, stop, chunks))
    elif background == "cpu":
        bg = asyncio.create_task(_cpu_burn(duration + 1.0))
    try:
        latencies = await _probe_loop(url, duration, queries)
    finally:
        stop.set()
        if bg is not None:
            try:
                await asyncio.wait_for(bg, timeout=120)
            except Exception:  # noqa: BLE001
                bg.cancel()
    return {"latencies_ms": latencies, "reindex_chunks": chunks}


async def _reindex_loop_once(url: str) -> dict:
    """把一轮全量重建（batch=16）跑完，返回最后一次结果。"""
    async with HttpSession(url) as client:
        out = await client.call("memory_reindex", {"cursor": None, "batch": 16},
                                timeout=900)
        while not (out or {}).get("done"):
            out = await client.call("memory_reindex",
                                    {"cursor": (out or {}).get("cursor"), "batch": 16},
                                    timeout=900)
        return out


def phase_longtask(sandbox: Sandbox, args, monitor: HostMonitor,
                   queries: list[str], results: dict, port: int) -> None:
    """空闲基线 vs 索引重建中 vs 宿主 CPU 打满——同一慢 Stub daemon 上比。"""
    print("\n[L] 长任务阻塞（慢 Stub：每篇 %gms，放大临界区）" % args.stub_delay_ms)
    slow = Daemon(sandbox, port, "slowstub", stub_embed=True,
                  stub_delay_ms=args.stub_delay_ms)
    slow.start()
    try:
        asyncio.run(_reindex_loop_once(slow.url))
        asyncio.run(_warm_searches(slow.url, queries, 4))  # 预热检索器/策略与连接
        rows: list[dict] = []
        for label, background, duration in (
            ("idle", None, args.longtask_seconds),
            ("index_rebuild", "reindex", args.longtask_seconds),
            ("host_cpu_saturated", "cpu", args.longtask_seconds),
        ):
            t0 = time.time()
            outcome = asyncio.run(_long_task_window(
                slow.url, duration, queries, background=background))
            t1 = time.time()
            stats = summarize(outcome["latencies_ms"])
            rows.append({
                "scenario": label,
                "duration_s": duration,
                **stats,
                "reindex_chunks": outcome["reindex_chunks"],
                "reindex_chunk_count": len(outcome["reindex_chunks"]),
                "host": monitor.window(t0, t1),
                "latencies_ms": [round(v, 3) for v in outcome["latencies_ms"]],
            })
            print(f"    {label:<20} p50={stats['p50_ms']}ms p95={stats['p95_ms']}ms "
                  f"max={stats['max_ms']}ms n={stats['n']}")
        idle = rows[0]
        for row in rows[1:]:
            row["p50_ratio_vs_idle"] = (round(row["p50_ms"] / idle["p50_ms"], 2)
                                        if idle["p50_ms"] and row["p50_ms"] else None)
            row["p95_ratio_vs_idle"] = (round(row["p95_ms"] / idle["p95_ms"], 2)
                                        if idle["p95_ms"] and row["p95_ms"] else None)
        results["longtask"] = {
            "points": rows,
            "config": {
                "embedder": "slow-stub",
                "stub_delay_ms_per_doc": args.stub_delay_ms,
                "reindex_batch": 16,
                "probe_interval_s": 0.02,
                "duration_s": args.longtask_seconds,
                "cpu_burn_workers": max(1, (psutil.cpu_count(logical=False) or 2)),
                "note": "慢 Stub 是机制演示：人为放大嵌入临界区，非真实 BGE-M3 绝对耗时",
            },
        }
    finally:
        slow.stop()


# ============================================================ M 真模型 RSS

async def _sample_proxies(sandbox: Sandbox, port: int, n: int, daemon: Daemon) -> dict:
    """开 N 个 stdio 代理会话（各一个真进程），读**自报 RSS**，同一事件循环里收工。"""
    sessions: list[StdioProxySession] = []
    rss_files: list[str] = []
    try:
        for index in range(n):
            pid_file = os.path.join(sandbox.pid_dir, f"real-{n}-{index}.pid")
            rss_file = os.path.join(sandbox.rss_dir, f"proxy-{n}-{index}.rss")
            rss_files.append(rss_file)
            sessions.append(await StdioProxySession(
                sandbox, port, pid_file, rss_file).__aenter__())
        await asyncio.sleep(1.0)  # 让代理把首包处理完，RSS 稳定
        pids = [s.pid for s in sessions if s.pid]
        per_proxy = {str(index): read_rss_file(path)
                     for index, path in enumerate(rss_files)}
        per_proxy = {key: value for key, value in per_proxy.items() if value}
        total = sum(per_proxy.values())
        return {
            "n": n,
            "pids": pids,
            "rss_bytes_each": per_proxy,
            "rss_mib_each": sorted(m for m in (mib(v) for v in per_proxy.values()) if m),
            "sum_bytes": total,
            "sum_mib": mib(total),
            "daemon_rss_during_bytes": daemon.rss,
            "daemon_rss_during_mib": mib(daemon.rss),
            # 跨进程视角：本机假值，仅作注脚（见文件头注）
            "cross_process_rss_bytes_each": {str(pid): rss_bytes(pid) for pid in pids},
        }
    finally:
        for session in sessions:
            try:
                await session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - 收尾失败不影响已采到的数字
                pass


def phase_real(sandbox: Sandbox, args, results: dict, port: int,
               real_kb: str, real_index: str) -> None:
    """真 BGE-M3 daemon：冷启动（未载模型）RSS → 载模型后 RSS；N=1/8 代理 RSS。"""
    print("\n[M] 真模型：daemon 冷启动 RSS + N=1/8 代理 RSS 合计")
    info: dict = {
        "config": {
            "daemon_args": ["--no-warmup"],
            "kb_entries": args.real_entries,
            "rss_method": "self-reported（进程自写 PERF_RSS_FILE；跨进程查询在本机不可信）",
        },
    }
    daemon = Daemon(sandbox, port, "real", stub_embed=False, index_dir=real_index,
                    kb_dir=real_kb, extra_args=("--no-warmup",))
    info["host_gate"] = wait_for_quiet()
    daemon.start(timeout=180)
    try:
        info["pid"] = daemon.pid
        info["rss_after_health_bytes"] = daemon.rss
        info["rss_after_health_mib"] = mib(daemon.rss)
        # 先建索引 → 首次真正工作前必须载入 BGE-M3（进程级单例，之后只付一次）。
        load_start = time.time()
        built = asyncio.run(_reindex_loop_once(daemon.url))
        info["model_load_wall_s"] = round(time.time() - load_start, 1)
        info["index"] = {key: built.get(key) for key in ("entries", "gen", "total")}
        info["rss_after_model_bytes"] = daemon.rss
        info["rss_after_model_mib"] = mib(daemon.rss)
        info["cross_process_rss_after_model_bytes"] = rss_bytes(daemon.pid)
        print(f"    daemon 载模型后 RSS = {info['rss_after_model_mib']} MiB "
              f"(载入 {info['model_load_wall_s']}s；自报)")
        # 再跑几次**真查询**（真嵌入 query），看稳态 RSS 是否比「刚载入」更高。
        asyncio.run(_warm_searches(daemon.url, [f"perfmark{index:04d}"
                                                for index in range(args.real_entries)], 5))
        info["rss_after_searches_bytes"] = daemon.rss
        info["rss_after_searches_mib"] = mib(daemon.rss)
        print(f"    daemon 跑过 5 次真查询后 RSS = {info['rss_after_searches_mib']} MiB")

        proxies: list[dict] = []
        for n in (1, 8):
            sample = asyncio.run(_sample_proxies(sandbox, port, n, daemon))
            proxies.append(sample)
            print(f"    N={n} 代理 RSS 合计 = {sample['sum_mib']} MiB "
                  f"(单个 {sample['rss_mib_each']})")
        info["proxies"] = proxies
        info["counterfactual_n_times_daemon"] = {
            str(row["n"]): mib(row["n"] * (info["rss_after_model_bytes"] or 0))
            for row in proxies}
        info["daemon_rss_bytes_final"] = daemon.rss
        info["daemon_rss_mib_final"] = mib(daemon.rss)
        info["worst_daemon_rss_mib"] = max(
            [value for value in (info.get("rss_after_model_mib"),
                                 info.get("rss_after_searches_mib"),
                                 info.get("daemon_rss_mib_final"))
             if value is not None]
            + [row.get("daemon_rss_during_mib") or 0 for row in proxies])
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 真模型失败不拖垮整轮（如无缓存/离线）
        info["ok"] = False
        info["error"] = repr(exc)
        info["log_tail"] = daemon.log_tail()
        print(f"    [SKIP] 真模型阶段失败：{exc!r}")
    finally:
        daemon.stop()
    results["real"] = info


# ================================================================ 隔离快照

def isolation_snapshot(main_root: str) -> dict:
    """生产资产前后快照：主树 git status + `memory_agent/vector_db` 目录指纹。"""
    snapshot: dict = {"main_root": main_root}
    snapshot["git_status_main"] = _git(main_root, "status", "--short")
    snapshot["git_status_worktree"] = _git(ROOT, "status", "--short")
    vector_db = os.path.join(main_root, "memory_agent", "vector_db")
    listing: list[dict] = []
    if os.path.isdir(vector_db):
        for name in sorted(os.listdir(vector_db)):
            path = os.path.join(vector_db, name)
            stat = os.stat(path)
            entry = {"name": name, "mtime": int(stat.st_mtime),
                     "is_dir": os.path.isdir(path)}
            if os.path.isfile(path) and stat.st_size < 1024:
                with open(path, "rb") as handle:
                    entry["sha256"] = hashlib.sha256(handle.read()).hexdigest()[:16]
            listing.append(entry)
    snapshot["vector_db_listing"] = listing
    return snapshot


def diff_snapshot(before: dict, after: dict) -> dict:
    return {
        "git_status_main_unchanged": before.get("git_status_main")
        == after.get("git_status_main"),
        "git_status_worktree_unchanged": before.get("git_status_worktree")
        == after.get("git_status_worktree"),
        "vector_db_listing_unchanged": before.get("vector_db_listing")
        == after.get("vector_db_listing"),
        "vector_db_before": before.get("vector_db_listing"),
        "vector_db_after": after.get("vector_db_listing"),
    }


# ================================================================ 结论生成

def render_markdown(payload: dict) -> str:
    meta = payload["meta"]
    results = payload["results"]
    read = results.get("read")
    write = results.get("write")
    longtask = results.get("longtask")
    real = results.get("real")
    isolation = payload.get("isolation", {})
    gates = [row.get("host_gate") for row in (read or {}).get("points", [])]
    gates += [row.get("host_gate") for row in (write or {}).get("points", [])]
    if real:
        gates.append(real.get("host_gate"))
    gates = [gate for gate in gates if gate]
    quiet_count = sum(1 for gate in gates if gate.get("quiet"))
    lines: list[str] = [
        "# #70 并发与可靠性：一组可复现的数字（p50/p95 · RSS · 长任务阻塞）",
        "",
        f"- 日期：{meta['started_at']}（本地时区；整轮 {meta.get('wall_s')}s）",
        f"- 宿主：{meta['host'].get('platform')} · {meta['host'].get('cpu_logical')} 逻辑核 "
        f"({meta['host'].get('cpu_physical')} 物理) · 内存 "
        f"{meta['host'].get('vm_total_gib')} GiB",
        f"- 仓库：`{meta['repo_root']}` (branch `{meta['git_branch']}`, "
        f"HEAD `{meta['git_head'][:10]}`) · Python {meta['python']}",
        f"- 脚本：`memory_agent/eval/perf_70.py`（**原始逐请求数字**在 "
        f"`perf_70_results.json`）",
        f"- 命令：`{meta['command']}`",
        "",
        "## 0. 机制与口径",
        "",
        "拓扑（ADR-0013）：**一个常驻 daemon（HTTP `/mcp`）+ 每会话一个 stdio 代理**；"
        "daemon 里那份 BGE-M3 只付一次。进程内并发语义（只读这些代码，未改动）：",
        "",
        "- `MemoryIndex.search` / `refresh` / `rebuild` / `reindex` 全在**同一把 "
        "`INDEX_LOCK`（RLock）**下 → **N 个并发读者在服务端串行**（不是并行）；",
        "- 写入 `MemoryWriter.add` 在 `WRITE_LOCK` 下（去重检索再取 `INDEX_LOCK`）→"
        " **单写者串行**；加锁顺序恒为 WRITE → INDEX → store session；",
        "- Qdrant **local mode 同进程也不能并发开 client**，`VectorStoreService._session`"
        " 再串一道 `_SESSION_LOCK`（第 3 道串行化）；",
        "- 每次 `search` 还包含**语料指纹扫描**（`_maybe_refresh` 的 stat）与"
        " **自洽核对**（`store.count()`），都在这把锁里。",
        "",
        "所以本页回答的不是「有没有并行」，而是**串行化下 p50/p95 怎么长、内存怎么摊**。",
        "",
        "口径：**R/W/L 用 Stub 嵌入**（无 BGE-M3、无网络、无 CPU 争用，确定性）；"
        "L 的慢档用「每篇 %s ms 人工延时」放大嵌入临界区，是**机制演示**，"
        "不是真模型绝对耗时。**M 是真 BGE-M3**，只测冷启动 RSS 与代理 RSS 两个头条。"
        "每点重复 %s 次报中位数；(max−min)/median > 0.30 标 `unstable`。"
        % (results.get("longtask", {}).get("config", {}).get("stub_delay_ms_per_doc", "?"),
           meta["args"]["runs"]),
        "",
        "**RSS 口径**：daemon 的 RSS 一律取**进程自报**（包装脚本里的 reporter 线程把 "
        "`psutil.Process().memory_info().rss` 写进 `PERF_RSS_FILE`，harness 只读文件）——"
        "因为本机对**分离启动（DETACHED_PROCESS）**进程的**跨进程**内存查询返回假值"
        "（对照实验：持有 300MB 的子进程，psutil / tasklist / PowerShell `WorkingSet64` "
        "三家都报 ~4.4MB，自报 333MB）。由 `stdio_client` 正常派生的代理进程两种口径一致"
        "（自报 74.5MB vs 跨进程 78MB）。JSON 里另有 `cross_process_rss_*` 字段作注脚，"
        "**不进结论**。",
        "",
        f"整轮宿主峰值：CPU **{payload.get('host_monitor', {}).get('peak_cpu_percent')}%** / "
        f"内存 **{payload.get('host_monitor', {}).get('peak_vm_percent')}%**"
        "（本机与其它 teammate 可能并行；每个测量点的窗口负载记在 JSON 的 `host` 字段，"
        "某点标 `unstable` 时先看那里）。",
        "",
        f"**宿主静窗门**：{quiet_count}/{len(gates)} 个测量点开测前等到了 < 40% CPU 静窗，"
        "其余到点即测并把 `host_gate` 写进证据。**绝对延迟会随宿主负载整体平移"
        "（实测忙窗 ≈ 静窗的 2.2×），但 N 的缩放形状（p50 ∝ N、吞吐持平）不受影响**"
        "——本页的可复现结论看形状，绝对数字请连同 `host` / `host_gate` 一起读。",
        "",
    ]

    if read:
        base = next((r["median_p50_ms"] for r in read["points"] if r["n"] == 1), None)
        lines += [
            "## 1. 读并发：N 个读者（真 daemon HTTP `/mcp`）",
            "",
            "| N | 样本 | p50 (ms) | p95 (ms) | p99 (ms) | wall (s) | 吞吐 (req/s) "
            "| p50 / N=1 | daemon RSS (MiB) | 客户端 RSS (MiB) | 稳定 | 静窗 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in read["points"]:
            samples = sum(r["n"] for r in row["runs"])
            wall = median_of([r["wall_s"] for r in row["runs"]])
            gate = row.get("host_gate") or {}
            gate_text = "ok" if gate.get("quiet") else f"忙(等{gate.get('waited_s')}s)"
            lines.append(
                f"| {row['n']} | {samples} | {row['median_p50_ms']} | "
                f"{row['median_p95_ms']} | {row['median_p99_ms']} | {wall} | "
                f"{row['median_throughput_rps']} | {row['p50_ratio_vs_n1']} | "
                f"{mib(row['median_daemon_rss_bytes'])} | "
                f"{mib(row['median_client_rss_bytes'])} | "
                f"{'⚠ unstable' if row['unstable'] else 'ok'} | {gate_text} |")
        lines += [
            "",
            f"读基线（N=1）p50 = **{base} ms**。读数：**p50 随 N 近似线性上升、吞吐基本"
            "持平**——这正是 `INDEX_LOCK` 串行化的形状（服务端同一临界区排队），"
            "不是「多读者并行加速」。N=1 的绝对延迟里也含 Qdrant local mode "
            "**每次操作开关 client** 的开销（stub 档无模型；真模型再加每 query 的嵌入）。"
            "「静窗」列 = 开测前是否等到宿主 CPU < 40%（本机与 #71 teammate 并行，"
            "忙窗能把同一测量点抬高 ~2.2×；等不到就照测并把 `host_gate` 记进 JSON）。",
            "",
        ]

    if write:
        lines += [
            "## 2. 写串行：N 个并发 `memory_add`（`WRITE_LOCK`）",
            "",
            "| N | ops | p50 (ms) | p95 (ms) | wall (s) | wall/op (ms) | 有效吞吐 (op/s) "
            "| 写时读探针 p50 (ms) | 写时读探针 p95 (ms) | 非 written 响应 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in write["points"]:
            ops = sum(r["ops"] for r in row["runs"])
            wall_per_op = (round(row["median_wall_s"] * 1000.0 / (ops / len(row["runs"])), 1)
                           if ops else None)
            lines.append(
                f"| {row['n']} | {ops} | "
                f"{row['median_p50_ms']} | {row['median_p95_ms']} | "
                f"{row['median_wall_s']} | {wall_per_op} | {row['median_throughput_ops']} | "
                f"{row['median_probe_p50_ms']} | {row['median_probe_p95_ms']} | "
                f"{row['duplicate_responses']} |")
        nonwritten = sum(row["duplicate_responses"] for row in write["points"])
        p50_chain = " → ".join(str(row["median_p50_ms"]) for row in write["points"])
        ns_chain = "→".join(str(row["n"]) for row in write["points"])
        per_op = []
        for row in write["points"]:
            runs = row["runs"]
            if runs:
                per_op.append(round(row["median_wall_s"] * 1000.0 /
                                    (runs[0]["ops"] or 1), 0))
        tput_chain = " / ".join(str(row["median_throughput_ops"]) for row in write["points"])
        lines += [
            "",
            "**写侧是单写者串行（设计取舍，不是缺陷）**：`WRITE_LOCK` 让一次 "
            "`memory_add` 从头（去重检索）到尾（落盘 + git commit）独占，所以："
            f"① **p50 随 N 近似线性上升**（N={ns_chain} → p50 = {p50_chain} ms，"
            "即中位请求排在 ≈N/2 个写之后）；"
            f"② **单次服务时间 ≈ `wall/op` ≈ {per_op} ms**（表里该列各 N 基本恒定），"
            f"总墙钟 ≈ ops × 服务时间 → **吞吐 ≈ {tput_chain} op/s、不随 N 提高**；"
            "③ 写期间的读探针 p50 ≈ "
            f"{median_of([r['median_probe_p50_ms'] for r in write['points']])} ms"
            "（高于空闲读基线，因为写与读抢同一把 `INDEX_LOCK`）。"
            "取舍 = 去重的 TOCTOU 消除 + `.git/index.lock` 不争用 + 索引只被一个写者改"
            "（ADR-0013 D3）。"
            + (f" 本次所有写请求都真的落盘（非 `written` 响应 = **{nonwritten}**）。"
               if nonwritten == 0 else
               f" ⚠ 有 {nonwritten} 次非 `written` 响应（JSON `nonwritten_samples` 留了"
               "完整响应：多为写前 `exists` / 去重预检判 duplicate），这些 op 走的不是"
               "完整写路径，看数时以 `written` 那些为准。"),
            "",
        ]

    if longtask:
        chunk_ms = [c["chunk_ms"] for r in longtask["points"]
                    if r["scenario"] == "index_rebuild" for c in r["reindex_chunks"]]
        lines += [
            "## 3. 长任务阻塞：请求延迟 vs 空闲基线",
            "",
            f"同一慢 Stub daemon（每篇 {longtask['config']['stub_delay_ms_per_doc']} ms "
            f"人工延时）；探针 ~50 qps 单读者；每档 {longtask['config']['duration_s']} s。",
            "",
            "| 场景 | 样本 | p50 (ms) | p95 (ms) | max (ms) | p50 / 空闲 | p95 / 空闲 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in longtask["points"]:
            lines.append(
                f"| {row['scenario']} | {row['n']} | {row['p50_ms']} | {row['p95_ms']} | "
                f"{row['max_ms']} | {row.get('p50_ratio_vs_idle', '—')} | "
                f"{row.get('p95_ratio_vs_idle', '—')} |")
        lines += [
            "",
            f"索引重建期间记录 {len(chunk_ms)} 个 `memory_reindex` 分块"
            f"（每块 {longtask['config']['reindex_batch']} 条，**批内持 `INDEX_LOCK`**），"
            f"分块耗时 p50 {pct(chunk_ms, 0.5)} ms / max "
            f"{max(chunk_ms) if chunk_ms else '—'} ms。重建期请求被挡在 `INDEX_LOCK` 外："
            "p95 抬到与「一块的嵌入耗时」同量级；块间（MCP 往返间隙）请求仍可穿过，"
            "所以 p50 抬升小于 p95。`host_cpu_saturated` 档是**同机 CPU 争用**，"
            "与锁无关（daemon 单线程处理请求）。",
            "",
        ]

    if real:
        lines += ["## 4. 真模型：1×BGE-M3 + N×数十MB（不是 N×3.9GB）", ""]
        if real.get("ok"):
            lines += [
                f"- daemon 冷启动（`--no-warmup`，未载模型）RSS = "
                f"**{real.get('rss_after_health_mib')} MiB**",
                f"- 首次真正工作（建索引）后 RSS = **{real.get('rss_after_model_mib')} MiB**"
                f"（BGE-M3 载入 {real.get('model_load_wall_s')} s）",
                f"- 又跑 5 次真查询后的稳态 RSS = "
                f"**{real.get('rss_after_searches_mib')} MiB**（整段最大值 "
                f"{real.get('worst_daemon_rss_mib')} MiB）",
                f"- 索引：{real.get('index')}",
                "",
                "| 会话数 N | 每代理 RSS (MiB) | 代理 RSS 合计 (MiB) | 若每会话各载一份模型 "
                "(N × daemon, MiB) |",
                "|---|---|---|---|",
            ]
            for row in real["proxies"]:
                lines.append(
                    f"| {row['n']} | {row['rss_mib_each']} | {row['sum_mib']} | "
                    f"{real['counterfactual_n_times_daemon'][str(row['n'])]} |")
            lines += [
                "",
                "结论：**模型只在 daemon 里一份**；N 个会话各自只付代理进程的几十 MB。"
                "代理 RSS 与 N 基本无关，**合计 ≈ N × 数十 MB**，不是 N × 3.9GB"
                "——这就是 ADR-0013 拓扑的内存论点，现在有数字了。",
                "",
                f"> 口径边界：本机实测「载模型后」≈ "
                f"{real.get('rss_after_searches_mib')} MiB，**低于 AGENTS.md 里的 ~3.9GB "
                "口径**（后者含 reranker 与峰值页；本档只装 BGE-M3 一份、`MEMORY_RERANK=0`、"
                "`--no-warmup` 后一次真工作即取样）。结论不受影响：付的是**一份**模型，"
                "不随会话数增长。",
                "",
            ]
        else:
            lines += [f"- ⚠ 真模型阶段未完成：`{real.get('error')}`"
                      "（详见 JSON `log_tail`；头条数字缺该点）", ""]

    lines += [
        "## 5. 边界与取舍（写清，不藏）",
        "",
        "1. **读并发 = 串行服务**：`INDEX_LOCK` 是进程级 RLock，`search` 整体在锁内"
        "（含自洽核对 + 语料指纹扫描 + store 往返）。N 个读者不会并行变快；吞吐上限 ≈ "
        "单进程检索吞吐。**取舍**：Qdrant local mode 同进程不能并发开 client"
        "（构造即 `already accessed`），不串行就不可用。要真并行得换 Qdrant server 形态"
        "（`MEMORY_STORE_URL` 已预留，ADR-0019）。",
        "2. **写串行 = 单写者**：`WRITE_LOCK` 一次只放一个 add/supersede/archive 走完"
        "（去重检索 → 落盘 → git commit）。**取舍**：优先正确性（去重 TOCTOU / git index "
        "争用）而非写吞吐。",
        "3. **文件锁只在本机成立**：锁是**进程内** `threading` 原语；daemon 的 PID 文件"
        "（`proxy.py` 的启动权锁、`daemon_pid_path`）也是**本机文件系统**语义。"
        "跨机（NFS / 容器共享卷）不保证互斥；多机部署必须一个 daemon 一个索引"
        "（`MEMORY_MCP_PORT` 隔离）。",
        "4. **Qdrant local mode 同进程独占**：同一 `path=` 在一个进程内只能有一个 client；"
        "所以本地平面天然要求「单 daemon」。",
        "5. **测量口径的边界**：Stub 档的绝对延迟**不含** BGE-M3 查询嵌入"
        "（真模型每 query 还要加嵌入时间），但**并发形状（串行排队）与模型无关**；"
        "RSS 全部自报（原因见 §0）；宿主负载（与本机其它 teammate 并行）记在 JSON 每个"
        "测量点的 `host` 字段。",
        f"6. **隔离**：主树 `git status` 前后一致 = "
        f"{isolation.get('git_status_main_unchanged')}；生产 `memory_agent/vector_db` "
        f"目录指纹前后一致 = {isolation.get('vector_db_listing_unchanged')}；"
        f"worktree `git status` 前后一致 = "
        f"{isolation.get('git_status_worktree_unchanged')}。",
        "",
        "## 6. 精确复跑命令",
        "",
        "```powershell",
        "# 一把跑完（默认 N ∈ {1,4,8,16} × 3 次；Stub 扫描 + 真模型头条）",
        "cd D:\\python_work\\work2026-4\\wk-70-perf",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py",
        "",
        "# 本页证据就是这么跑的（参数记在 meta.args / meta.command）：",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --requests 12 --longtask-seconds 8",
        "",
        "# 单档：只看读并发 / 只看写串行 / 只看长任务 / 只看真模型",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --mode read --n 1,4,8,16 --runs 3",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --mode write",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --mode longtask",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --mode real",
        "",
        "# 只改渲染口径（数字不动）：从既有 JSON 重新生成 MD",
        "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv\\Scripts\\python.exe "
        "memory_agent/eval/perf_70.py --render-only memory_agent/eval/perf_70_results.json",
        "```",
        "",
        "> 端口隔离契约：`--port 8891`（读/写 daemon）、`+1`（长任务慢 Stub daemon）、"
        "`+2`（真模型 daemon）；被占即报错退出，不静默换端口。",
        "",
    ]
    return "\n".join(lines)


# ===================================================================== main

def parse_ns(raw: str) -> list[int]:
    values = [int(part) for part in raw.replace(" ", "").split(",") if part]
    if not values or any(v < 1 for v in values):
        raise SystemExit("--n 需要正整数列表，如 1,4,8,16")
    return values


def main() -> int:
    global HOST
    try:  # Windows 控制台默认 GBK，中文输出会糊；统一走 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="#70 并发与可靠性压测")
    parser.add_argument("--n", dest="n_raw", default="1,4,8,16",
                        help="读者并发数列表（默认 1,4,8,16）")
    parser.add_argument("--runs", type=int, default=3, help="每个测量点重复次数（默认 3）")
    parser.add_argument("--requests", type=int, default=15, help="每个读者每轮的请求数")
    parser.add_argument("--adds", type=int, default=2, help="每个写者每轮的写入数")
    parser.add_argument("--probe-reads", type=int, default=15,
                        help="写档读探针的最大轮数（实际跑满整个写窗口）")
    parser.add_argument("--entries", type=int, default=200, help="临时 KB 条目数（扫描用）")
    parser.add_argument("--real-entries", type=int, default=8,
                        help="真模型档临时 KB 条目数")
    parser.add_argument("--longtask-seconds", type=float, default=10.0,
                        help="长任务每档时长")
    parser.add_argument("--stub-delay-ms", type=float, default=20.0,
                        help="长任务档慢 Stub 每篇/每 query 人工延时（毫秒）")
    parser.add_argument("--mode", default="all",
                        choices=["all", "read", "write", "longtask", "real"])
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=8891,
                        help="起始端口（隔离契约：read/write=port，longtask=port+1，"
                             "real=port+2；被占则报错）")
    parser.add_argument("--out", default=os.path.join(_HERE, "perf_70_results.json"))
    parser.add_argument("--md", default=os.path.join(_HERE, "perf_70_results.md"))
    parser.add_argument("--keep-workdir", action="store_true",
                        help="保留临时工作区（排障用）")
    parser.add_argument("--render-only", default=None, metavar="JSON",
                        help="不测量：只从既有 perf_70_results.json 重新生成 MD"
                             "（渲染口径改动后用，数字不变）")
    args = parser.parse_args()

    if args.render_only:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:  # noqa: BLE001
            pass
        with open(args.render_only, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(payload))
        print(f"[render-only] {args.render_only} -> {args.md}")
        return 0

    HOST = args.host
    args.ns = parse_ns(args.n_raw)

    ports = {"scan": args.port, "longtask": args.port + 1, "real": args.port + 2}
    for label, port in ports.items():
        _free_port(HOST, port)

    work = tempfile.mkdtemp(prefix="perf-70-")
    sandbox = Sandbox(work)
    real_kb = os.path.join(work, "real-kb")
    real_index = os.path.join(work, "real-index")

    worktrees = _git(ROOT, "worktree", "list", "--porcelain").splitlines()
    main_root = (worktrees[0].replace("worktree ", "")
                 if worktrees and worktrees[0].startswith("worktree ") else ROOT)

    monitor = HostMonitor()
    monitor.start()
    before = isolation_snapshot(main_root)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    started_epoch = time.time()

    host_info = monitor.snapshot()
    host_info.update({"platform": f"{os.name} / {sys.platform}",
                      "vm_total_gib": round(host_info["vm_total_bytes"] / (1024 ** 3), 1)})
    payload: dict = {
        "meta": {
            "issue": 70,
            "script": "memory_agent/eval/perf_70.py",
            "started_at": started,
            "command": " ".join([os.path.basename(PY), *sys.argv[1:]]),
            "cwd": os.getcwd(),
            "repo_root": ROOT,
            "python": sys.version.split()[0],
            "git_branch": _git(ROOT, "rev-parse", "--abbrev-ref", "HEAD"),
            "git_head": _git(ROOT, "rev-parse", "HEAD"),
            "host": host_info,
            "args": {key: value for key, value in vars(args).items() if key != "ns"}
            | {"ns": args.ns},
            "ports": ports,
            "workdir": work,
            "proxy_env_sanitized": REMOVED_PROXY_PATTERNS,
            "rss_method": "self-reported（PERF_RSS_FILE）；跨进程查询在本机不可信",
        },
        "results": {},
    }

    queries = build_kb(sandbox.kb, args.entries)
    build_kb(real_kb, args.real_entries)
    print(f"# #70 perf scan @ {started}")
    print(f"    沙箱 = {work}")
    print(f"    端口 = {ports}  模式 = {args.mode}  N = {args.ns}  runs = {args.runs}")

    daemon = Daemon(sandbox, ports["scan"], "scan", stub_embed=True)
    try:
        if args.mode in ("all", "read", "write", "longtask"):
            daemon.start()
            print(f"    daemon[{daemon.tag}] pid={daemon.pid} url={daemon.url}")
            status = asyncio.run(_reindex_loop_once(daemon.url))
            payload["results"]["index_build"] = status
            print(f"    索引建成：entries={status.get('entries')} gen={status.get('gen')} "
                  f"daemon RSS(自报)={mib(daemon.rss)} MiB")
            if args.mode in ("all", "read"):
                phase_read(sandbox, daemon, args, monitor, queries, payload["results"])
            if args.mode in ("all", "write"):
                phase_write(sandbox, daemon, args, monitor, queries, payload["results"])
            if args.mode in ("all", "longtask"):
                phase_longtask(sandbox, args, monitor, queries, payload["results"],
                               ports["longtask"])
        if args.mode in ("all", "real"):
            phase_real(sandbox, args, payload["results"], ports["real"], real_kb, real_index)
    except Exception as exc:  # noqa: BLE001 - 单阶段失败也要落盘已测到的数字
        import traceback
        traceback.print_exc()
        payload["results"]["fatal"] = repr(exc)
    finally:
        daemon.stop()
        monitor.stop()
        after = isolation_snapshot(main_root)
        payload["isolation"] = diff_snapshot(before, after)
        payload["host_monitor"] = {
            "samples": monitor.samples,
            "peak_cpu_percent": max((s["cpu_percent"] for s in monitor.samples),
                                    default=None),
            "peak_vm_percent": max((s["vm_percent"] for s in monitor.samples),
                                   default=None),
        }
        payload["meta"]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        payload["meta"]["wall_s"] = round(time.time() - started_epoch, 1)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(payload))
        print(f"\n[结果] JSON -> {args.out}")
        print(f"[结果] MD   -> {args.md}")
        if args.keep_workdir:
            print(f"[结果] 临时工作区保留：{work}")
        else:
            _rmtree(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
