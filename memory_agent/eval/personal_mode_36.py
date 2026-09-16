"""#36 个人模式落地验收：运行时语料 + overlay 收录 + 查询时惰性刷新。

要证明的事（对应 issue #36 验收清单）：
1. 外部直接改已收录文件（增 / 改 / 删）→ 下一次 memory_search 反映，**无需重启**。
2. 改 overlay（加 / 减文件）→ 免重启生效；list 能区分「默认 vs 显式」。
3. 写后不再同步刷（D13）；memory_reindex 仍可全量重建。
4. 指纹检查在真实语料（三仓库 Markdown）耗时**毫秒级**。
5. 移除收录走预览 + 确认，且**不误删**他人条目。

隔离与成本：
- 真相源（KB）/ registry / overlay / 索引全在临时目录；真实 KB 与工作树不被触碰。
- 用 **Stub 嵌入**（不加载 BGE-M3，秒级）——本票验的是「收录 + 刷新」机制，不是排序质量。
- 单一 `MemoryIndex` 实例贯穿全程 = 模拟**常驻 daemon 不重启**；改文件 / 改 overlay 后
  只再发 `search`，不重建进程。

用法（仓库根，主树 venv 绝对路径）：
    venv\\Scripts\\python.exe memory_agent/eval/personal_mode_36.py [--json-out out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 真实来源注册表（主树，gitignored）——用于「真实语料指纹耗时」子进程。
DEFAULT_REAL_CONFIG = os.path.join(
    os.path.dirname(ROOT), "Agent-Knowledge-Base", "memory_agent", "readonly_repos.json"
)


class Suite:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        ok = bool(ok)
        self.rows.append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.rows if not row["ok"]]


# --------------------------------------------------------------------- helpers


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _git(repo: str, *args: str) -> None:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr}")


def _make_kb(root: str) -> None:
    os.makedirs(os.path.join(root, "topics"), exist_ok=True)
    _write(os.path.join(root, "tags.md"), "- demo\n")
    _write(os.path.join(root, "INDEX.md"), "# index\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _rmtree(path: str) -> None:
    def on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def _sources_of(hits) -> set[str]:
    return {hit["source"] for hit in hits}


# -------------------------------------------------------- 真实语料指纹耗时（子进程）


def measure_real_fingerprint(python: str, config: str | None) -> dict:
    code = (
        "import json,time;"
        "from memory_agent.corpus.loader import scan_fingerprint;"
        "t=time.perf_counter();fp,c=scan_fingerprint();"
        "print(json.dumps({'files':len(fp),'ms':round((time.perf_counter()-t)*1000,2),"
        "'complete':c}))"
    )
    env = dict(os.environ)
    env.pop("AGENT_KB_DIR", None)
    env.pop("MEMORY_INDEX_DIR", None)
    env.pop("MEMORY_READONLY_ROOTS", None)
    env.pop("MEMORY_OVERLAY_CONFIG", None)
    if config and os.path.isfile(config):
        env["MEMORY_READONLY_REPOS_CONFIG"] = config
    proc = subprocess.run(
        [python, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        return {"error": (proc.stderr or "")[-800:]}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": f"无法解析子进程输出：{proc.stdout[-400:]}"}


# ------------------------------------------------------------------------ main


def run(suite: Suite, kb_dir: str, corpus_dir: str, overlay_path: str,
        index_dir: str) -> None:
    from memory_agent.memory.admission import AdmissionManager
    from memory_agent.memory.index import MemoryIndex
    from memory_agent.memory.layout import IndexLayout
    from memory_agent.memory.reindex import Reindexer
    from memory_agent.memory.store import QdrantLocalStore
    from memory_agent.memory.writer import MemoryWriter
    from ragcore.utils.model_status import EMBEDDING_DIMENSION

    class StubEmbeddings:
        def embed_query(self, text):
            return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

        def embed_documents(self, texts):
            return [self.embed_query(t) for t in texts]

    def store_factory(db_path: str) -> QdrantLocalStore:
        return QdrantLocalStore(db_path=db_path, collection_name="mem",
                                embeddings=StubEmbeddings())

    layout = IndexLayout(root=index_dir)

    # -- 0. 全量重建（memory_reindex 的等价入口）仍可用 --------------------
    build = Reindexer(layout=layout, store_factory=store_factory).run_all(batch=256)
    suite.check(
        "0a memory_reindex/全量重建仍可用且自洽",
        build.get("done") is True and build.get("entries", 0) >= 1,
        f"entries={build.get('entries')} gen={build.get('gen')}",
    )

    index = MemoryIndex(layout=layout, store_factory=store_factory)  # 常驻单实例

    # -- 1. 收录现状：默认 vs 显式 ----------------------------------------
    listing = AdmissionManager().list()
    default_origins = {f["source"] for f in listing["resolved"] if f["origin"] == "default"}
    suite.check(
        "1a list 能区分默认（注册表）与显式（overlay）",
        "corpus/a.md" in default_origins and listing["counts"]["explicit"] == 0,
        f"default={sorted(default_origins)} explicit={listing['counts']['explicit']}",
    )

    hits = index.search("ALPHAONE marker", k=5)
    suite.check(
        "1b 初始检索命中注册表默认文件",
        "corpus/a.md" in _sources_of(hits),
        f"sources={sorted(_sources_of(hits))}",
    )

    # -- 2. 外部改文件：增 / 改 / 删 → 下次搜索反映（免重启） --------------
    _write(os.path.join(corpus_dir, "b.md"), "# B\n\nBETA-TOKEN lives here.\n")
    hits = index.search("BETA-TOKEN", k=5)
    suite.check(
        "2a 外部新增文件 → 下一次 search 可见（免重启）",
        "corpus/b.md" in _sources_of(hits),
        f"sources={sorted(_sources_of(hits))}",
    )

    _write(os.path.join(corpus_dir, "a.md"), "# A\n\nGAMMA-TOKEN replaced it.\n")
    hits = index.search("GAMMA-TOKEN", k=5)
    suite.check(
        "2b 外部修改文件 → 下一次 search 反映新内容（免重启）",
        "corpus/a.md" in _sources_of(hits),
        f"sources={sorted(_sources_of(hits))}",
    )

    os.remove(os.path.join(corpus_dir, "b.md"))
    hits = index.search("BETA-TOKEN", k=8)
    suite.check(
        "2c 外部删除文件 → 下一次 search 不再返回（免重启）",
        "corpus/b.md" not in _sources_of(hits),
        f"sources={sorted(_sources_of(hits))}",
    )

    # -- 3. overlay 收录：加（explicit）/ 减（exclude 预览 + 确认）--------
    notes = os.path.join(os.path.dirname(corpus_dir), "notes")
    _write(os.path.join(notes, "c.md"), "# C\n\nDELTA-TOKEN extra notes.\n")
    include = AdmissionManager().include(
        pattern=os.path.join(notes, "c.md"), label="notes", owner="me", confirm=True
    )
    suite.check(
        "3a overlay include 落盘（confirm 后）",
        include["status"] == "written" and include["preview"]["matched"] == 1,
        f"status={include['status']}",
    )
    hits = index.search("DELTA-TOKEN", k=5)
    listing = AdmissionManager().list()
    explicit_origins = {f["source"] for f in listing["resolved"] if f["origin"] == "explicit"}
    suite.check(
        "3b overlay 新增免重启生效（search 可见 + list 标 explicit）",
        "notes/c.md" in explicit_origins and "notes/c.md" in _sources_of(hits),
        f"explicit={sorted(explicit_origins)} sources={sorted(_sources_of(hits))}",
    )

    # 他人条目：写一条 KB 记录，稍后确认它不因 exclude 被误删。
    writer = MemoryWriter(index, kb_dir=kb_dir)
    added = writer.add(
        title="Personal-mode anchor", body="EPSILON-TOKEN durable fact.",
        domain="topics", type="topic", tags=["demo"], slug="personal-anchor",
    )
    suite.check(
        "3c 写后不再同步刷（D13：index.mode=lazy）",
        added["index"].get("mode") == "lazy" and added["index"].get("refreshed") is False,
        f"index={added['index']}",
    )
    suite.check(
        "3d 写入条目要等下一次 search 才进索引（D13 惰性追平）",
        "topics/personal-anchor" not in index.known_ids(),
        f"known={len(index.known_ids())}",
    )
    index.search("EPSILON-TOKEN", k=5)
    suite.check(
        "3e 一次 search 后写入条目已追平",
        "topics/personal-anchor" in index.known_ids(),
    )

    preview = AdmissionManager().exclude(pattern=os.path.join(notes, "c.md"))
    suite.check(
        "3f exclude 默认只预览、不落盘",
        preview["status"] == "confirmation_required" and preview["written"] is False
        and preview["preview"]["leaving"] == 1,
        f"leaving={preview['preview']['leaving']}",
    )
    confirm = AdmissionManager().exclude(pattern=os.path.join(notes, "c.md"), confirm=True)
    hits = index.search("DELTA-TOKEN", k=8)
    keep_hits = index.search("EPSILON-TOKEN", k=5)
    suite.check(
        "3g exclude confirm 后免重启生效，且不误删他人条目",
        confirm["status"] == "written"
        and "notes/c.md" not in _sources_of(hits)
        and "topics/personal-anchor" in index.known_ids()
        and any(h["id"] == "topics/personal-anchor" for h in keep_hits),
        f"sources={sorted(_sources_of(hits))}",
    )

    # -- 4. 惰性刷新自洽（manifest 条数 == 集合点数）-----------------------
    status = index.status()
    suite.check(
        "4a 惰性刷新后索引自洽",
        status["built"] and status.get("consistent") is True,
        f"entries={status['entries']} points={status.get('points')}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="#36 个人模式验收")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--real-config", default=DEFAULT_REAL_CONFIG,
                        help="真实来源注册表（用于真实语料指纹耗时子进程）")
    args = parser.parse_args()

    python = sys.executable
    real = measure_real_fingerprint(python, args.real_config)

    sandbox = tempfile.mkdtemp(prefix="memory-personal-mode-")
    kb_dir = os.path.join(sandbox, "kb")
    corpus_dir = os.path.join(sandbox, "corpus")
    index_dir = os.path.join(sandbox, "index")
    overlay_path = os.path.join(sandbox, "overlay.json")
    registry_path = os.path.join(sandbox, "readonly_repos.json")
    _write(os.path.join(corpus_dir, "a.md"), "# A\n\nALPHAONE marker text.\n")
    _write(registry_path, json.dumps(
        [{"label": "corpus", "path": corpus_dir, "owner": "me"}]
    ))
    _make_kb(kb_dir)

    # 必须在 import memory_agent.settings 之前注入环境。
    os.environ["AGENT_KB_DIR"] = kb_dir
    os.environ["MEMORY_INDEX_DIR"] = index_dir
    os.environ.pop("MEMORY_READONLY_ROOTS", None)
    os.environ["MEMORY_READONLY_REPOS_CONFIG"] = registry_path
    os.environ["MEMORY_OVERLAY_CONFIG"] = overlay_path
    os.environ["MEMORY_REINDEX_BATCH"] = "256"

    suite = Suite()
    print(f"沙箱: {sandbox}\n真实语料指纹: {real}\n")
    try:
        run(suite, kb_dir, corpus_dir, overlay_path, index_dir)
    except Exception as exc:  # noqa: BLE001 - 验收脚本要如实汇报
        import traceback
        traceback.print_exc()
        suite.check("套件未抛异常", False, repr(exc))
    finally:
        _rmtree(sandbox)

    suite.check(
        "5. 真实语料指纹检查为毫秒级（~百文件量级）",
        isinstance(real.get("ms"), (int, float)) and real.get("files", 0) >= 50
        and real["ms"] < 100,
        f"files={real.get('files')} ms={real.get('ms')} complete={real.get('complete')}",
    )

    passed = sum(1 for row in suite.rows if row["ok"])
    total = len(suite.rows)
    print(f"\n==== {passed}/{total} 通过 ====")
    for row in suite.failed:
        print(f"  FAIL: {row['name']} — {row['detail']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"passed": passed, "total": total, "real_fingerprint": real,
                       "rows": suite.rows}, handle, ensure_ascii=False, indent=2)
    return 0 if passed == total and total else 1


if __name__ == "__main__":
    raise SystemExit(main())
