"""#51 上传接线端到端验收：PDF / DOCX → 解析 → 物化只读条目 → overlay 收录 → 可召回。

要证明的事（对应 #51 验收 + 规格评论补充）：
1. 上传 PDF → 物化 `.md` 落只读 import 根，**原件存 gitignored 上传目录**（D9）；
2. 条目 `writable=false`、`owner=<label>`、`source=<label>/<rel>`、frontmatter 合规、每节 ≤ 窗口；
3. 经 **overlay 显式收录**（免重启），下一次 `memory_search` 的**惰性刷新**即可召回（D9）；
4. 全程**不写记忆 KB、不 git commit**（只读域）；真实 KB / 索引 / 工作树前后不变；
5. 主 venv **未装 Docling**；真 Docling 路径由 `--parse-python` 指向专用解析环境（D7）。

隔离与成本：
- 真相源 / overlay / 索引 / import / uploads 全在临时目录；真实 KB 与工作树不被触碰。
- 用 **Stub 嵌入**（不加载 BGE-M3），验的是「物化 + 收录 + 召回」机制，不是排序质量。
- 单一 `MemoryIndex` 实例贯穿全程 = 模拟常驻 daemon 不重启。

用法（仓库根，主树 venv 绝对路径）：
    venv\\Scripts\\python.exe memory_agent/eval/ingest_51.py
    venv\\Scripts\\python.exe memory_agent/eval/ingest_51.py --parse-python venv-parse\\Scripts\\python.exe
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
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


def _rmtree(path: str) -> None:
    def on_error(func, target, _exc):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def _make_pdf(path: str, lines: list[str]) -> str:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    operators = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operators.append("0 -18 Td")
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        operators.append(f"({escaped}) Tj")
    operators.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(operators).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def _make_docx(path: str) -> str:
    """最小合法 DOCX（含 Heading 样式），供真 Docling 走标题切节。"""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style></w:styles>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>Quarterly Overview</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>DOCLINGDOCX marker appears in this paragraph.</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
        '<w:r><w:t>Cost Details</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>DOCLINGCOST marker appears in the details section.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/document.xml", document)
    return path


def _git_state(repo: str) -> dict:
    def run(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)

    return {
        "head": (run("rev-parse", "HEAD").stdout or "").strip(),
        "status": (run("status", "--porcelain").stdout or ""),
    }


# --------------------------------------------------------------------- main


def run(suite: Suite, sandbox: str, parse_python: str | None) -> None:
    from memory_agent import ingest
    from memory_agent.corpus import loader
    from memory_agent.memory.entries import Entry
    from memory_agent.memory.index import MemoryIndex
    from memory_agent.memory.layout import IndexLayout
    from memory_agent.memory.reindex import Reindexer
    from memory_agent.memory.store import QdrantLocalStore
    from ragcore.utils.model_status import EMBEDDING_DIMENSION

    class StubEmbeddings:
        def embed_query(self, text):
            return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

        def embed_documents(self, texts):
            return [self.embed_query(t) for t in texts]

    def store_factory(db_path: str) -> QdrantLocalStore:
        return QdrantLocalStore(db_path=db_path, collection_name="mem",
                                embeddings=StubEmbeddings())

    import_dir = os.path.join(sandbox, "imports")
    uploads_dir = os.path.join(sandbox, "uploads")
    overlay = os.path.join(sandbox, "overlay.json")
    index_dir = os.path.join(sandbox, "index")
    pdf = _make_pdf(os.path.join(sandbox, "handbook.pdf"),
                    ["Quarterly Overview", "PDFMARKER appears on page one.", "Second line."])
    pdf_bytes = open(pdf, "rb").read()

    layout = IndexLayout(root=index_dir)
    Reindexer(layout=layout, store_factory=store_factory).run_all(batch=256)
    index = MemoryIndex(layout=layout, store_factory=store_factory)  # 常驻单实例

    # -- 1. 上传前：import 来源尚不存在（Stub 密排会让任意 query 都返回种子）
    hits = index.search("PDFMARKER", k=5)
    suite.check(
        "1a 上传前 import 来源不存在",
        "handbook/handbook.md" not in {h["source"] for h in hits},
        f"sources={sorted(h['source'] for h in hits)}",
    )

    # -- 2. 上传 PDF（主环境 pypdf 兜底）----------------------------------
    summary = ingest.ingest_document(
        pdf, label="handbook", parse_python=parse_python,
        import_dir=import_dir, uploads_dir=uploads_dir, overlay_path=overlay,
        tags=["imported"], today="2026-09-17",
    )
    suite.check(
        "2a 物化出条目并落只读 import 根",
        summary["entries"] >= 1 and all(os.path.isfile(p) for p in summary["files"]),
        f"entries={summary['entries']} dir={summary['import_dir']}",
    )
    staged = summary["staged_original"]
    suite.check(
        "2b 原件存 gitignored 上传目录（D9，逐字一致）",
        os.path.isfile(staged) and open(staged, "rb").read() == pdf_bytes,
        f"staged={staged}",
    )
    with open(overlay, "r", encoding="utf-8") as handle:
        overlay_data = json.load(handle)
    suite.check(
        "2c overlay 写入 include（显式收录）",
        overlay_data["include"] and overlay_data["include"][0]["label"] == "handbook",
        f"include={overlay_data['include']}",
    )

    # -- 3. 条目语义：只读 / owner / source / id / frontmatter --------------
    written = summary["files"][0]
    rel = os.path.relpath(written, summary["import_dir"]).replace("\\", "/")
    source = f"handbook/{rel}"
    entry = Entry.from_file(written, source=source, writable=False,
                            owner="handbook", root=summary["import_dir"])
    suite.check(
        "3a writable=false / owner=label / source=label/rel",
        entry.writable is False and entry.owner == "handbook" and entry.source == source,
        f"owner={entry.owner} source={entry.source}",
    )
    suite.check(
        "3b frontmatter 合规：id/type/tags/status/source + id == source 去 .md",
        entry.id == source[:-3] and entry.type == "research"
        and entry.tags == ["imported"] and entry.status == "current",
        f"id={entry.id} type={entry.type} tags={entry.tags} status={entry.status}",
    )
    suite.check(
        "3c 每节 ≤ MAX_ENTRY_CHARS",
        all(len(open(p, encoding="utf-8").read()) <= 20000 for p in summary["files"]),
        f"files={len(summary['files'])}",
    )

    # -- 4. 收录经 selection 解析为显式只读来源 ---------------------------
    selection = loader.resolve_selection()
    readonly = [f for f in selection.files if not f.writable]
    suite.check(
        "4a selection 把 import 视为 explicit 只读来源",
        any(f.explicit and f.source == source and f.owner == "handbook" for f in readonly),
        f"readonly={[(f.source, f.owner, f.explicit) for f in readonly][:3]}",
    )

    # -- 5. 惰性刷新后即可召回（D9，不重启 / 不重建）------------------------
    hits = index.search("PDFMARKER", k=5)
    hit_sources = {hit["source"] for hit in hits}
    suite.check(
        "5a 上传后 memory_search 惰性刷新即召回",
        source in hit_sources,
        f"sources={sorted(hit_sources)}",
    )
    top = next((h for h in hits if h["source"] == source), None)
    suite.check(
        "5b 命中的只读语义正确（writable=false / owner=label）",
        top is not None and top.get("writable") is False and top.get("owner") == "handbook",
        f"writable={top and top.get('writable')} owner={top and top.get('owner')}",
    )

    # -- 6. 真 Docling（专用解析环境，可选）-------------------------------
    if parse_python:
        docx = _make_docx(os.path.join(sandbox, "quarterly.docx"))
        sections = ingest.parse_sections(docx, parse_python=parse_python)
        suite.check(
            "6a 真 Docling 在独立环境解析 DOCX 且保留标题切节",
            [s.title for s in sections] == ["Quarterly Overview", "Cost Details"],
            f"titles={[s.title for s in sections]}",
        )
        docx_summary = ingest.ingest_document(
            docx, label="quarterly", parse_python=parse_python,
            import_dir=import_dir, uploads_dir=uploads_dir, overlay_path=overlay,
            today="2026-09-17",
        )
        bodies = [open(path, encoding="utf-8").read() for path in docx_summary["files"]]
        hits = index.search("DOCLINGCOST", k=5)
        suite.check(
            "6b 真 Docling 解析的 DOCX 条目可召回",
            any("DOCLINGDOCX" in body for body in bodies)
            and any("DOCLINGCOST" in body for body in bodies)
            and any(h["source"].startswith("quarterly/") for h in hits),
            f"entries={docx_summary['entries']} sources={sorted(h['source'] for h in hits)}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="#51 文档上传接线端到端验收")
    parser.add_argument("--parse-python", default=None,
                        help="专用解析环境解释器（给了才跑真 Docling 分支）")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    before = _git_state(ROOT)

    sandbox = tempfile.mkdtemp(prefix="memory-ingest-51-")
    kb_dir = os.path.join(sandbox, "kb")
    corpus_dir = os.path.join(sandbox, "corpus")
    index_dir = os.path.join(sandbox, "index")
    os.makedirs(kb_dir, exist_ok=True)
    _write(os.path.join(corpus_dir, "seed.md"), "# Seed\n\nSEEDTOKEN baseline corpus.\n")

    # 必须在 import memory_agent.settings 之前注入环境。
    os.environ["AGENT_KB_DIR"] = kb_dir
    os.environ["MEMORY_INDEX_DIR"] = index_dir
    os.environ["MEMORY_READONLY_ROOTS"] = corpus_dir  # 一个种子来源，保证首次重建非空
    os.environ["MEMORY_OVERLAY_CONFIG"] = os.path.join(sandbox, "overlay.json")
    os.environ.pop("MEMORY_READONLY_REPOS_CONFIG", None)
    os.environ["MEMORY_SPARSE_BACKEND"] = "tfidf"

    from importlib.util import find_spec

    suite = Suite()
    print(f"沙箱: {sandbox}\n专用解析环境: {args.parse_python or '(未给，仅 pypdf 兜底)'}\n")
    suite.check("0 主 venv 未装 Docling（D7）", find_spec("docling") is None,
                f"docling_spec={find_spec('docling')}")
    try:
        run(suite, sandbox, args.parse_python)
    except Exception as exc:  # noqa: BLE001 - 验收脚本如实汇报
        import traceback
        traceback.print_exc()
        suite.check("套件未抛异常", False, repr(exc))
    finally:
        _rmtree(sandbox)

    after = _git_state(ROOT)
    suite.check("7 真实工作树前后不变（隔离）", before == after,
                f"head={before['head'][:8]} status_delta={before['status'] != after['status']}")

    passed = sum(1 for row in suite.rows if row["ok"])
    total = len(suite.rows)
    print(f"\n==== {passed}/{total} 通过 ====")
    for row in suite.failed:
        print(f"  FAIL: {row['name']} — {row['detail']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"passed": passed, "total": total, "rows": suite.rows},
                      handle, ensure_ascii=False, indent=2)
    return 0 if passed == total and total else 1


if __name__ == "__main__":
    raise SystemExit(main())
