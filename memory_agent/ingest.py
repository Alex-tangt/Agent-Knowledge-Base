"""文档上传接线（#51）：PDF / DOCX -> 解析 -> 物化 `.md` -> 收录为**只读语料**。

方向（2026-09-17 owner 定）：解析出的文档属于**只读语料 / 向量库域**，**不写记忆 KB**。
物化 `.md` 落到 gitignored 的 import 根，经 **overlay 显式收录**（#36 / ADR-0025 D8）：
读侧 `writable=false`、`owner=<label>`、`source=<label>/<rel>`，索引由 `memory_search`
的惰性刷新纳入。**不经过 `MemoryWriter`、不 git commit**（只读域无严格版本管理）。

原件按 D9 存 gitignored 的 `memory_agent/uploads/`（真相源 = 物化 Markdown）。
解析在**独立进程 / 独立解析环境**完成（ADR-0027 D7）：本进程默认走已安装的引擎
（主环境 = pypdf 兜底），`--parse-python` 指向专用解析环境解释器即可跑真 Docling。

    python -m memory_agent.ingest <file.pdf|docx> --label <label> [--tags a,b] [--parse-python <exe>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

from memory_agent import settings
from memory_agent.parse import Section, build_entries, write_entries

DEFAULT_TYPE = "research"
DEFAULT_STATUS = "current"
DEFAULT_TAGS = ("imported",)
SUPPORTED_EXTENSIONS = (".pdf", ".docx")

_UPLOAD_DIRNAME = "uploads"
_IMPORT_DIRNAME = "imports"


class IngestError(RuntimeError):
    """上传 / 解析 / 收录失败；消息面向调用方，含修正方向。"""


# --------------------------------------------------------------------- 路径 / 命名

def uploads_root() -> str:
    """原件暂存根（gitignored，D9）；`MEMORY_UPLOAD_DIR` 可覆盖。"""
    return (os.environ.get("MEMORY_UPLOAD_DIR")
            or os.path.join(settings.MEMORY_AGENT_DIR, _UPLOAD_DIRNAME))


def import_root() -> str:
    """物化 `.md` 的只读 import 根（gitignored）；`MEMORY_IMPORT_DIR` 可覆盖。"""
    return (os.environ.get("MEMORY_IMPORT_DIR")
            or os.path.join(settings.MEMORY_AGENT_DIR, _IMPORT_DIRNAME))


def slug_for(name: str) -> str:
    """文档名 -> slug；纯非 ASCII 标题回落 `doc-<hash8>`（与写网关的 slugify 同源）。"""
    from memory_agent.memory.authoring import slugify

    slug = slugify(name)
    if slug:
        return slug
    return "doc-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------- 原件暂存（D9）

def stage_source(file: str, *, uploads_dir: str, label: str) -> str:
    """把原件复制进 gitignored 上传根，返回暂存路径；已在目标内则原地不动。"""
    target_dir = os.path.join(uploads_dir, label)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(file))
    if os.path.abspath(file) != os.path.abspath(target):
        shutil.copy2(file, target)
    return target


# --------------------------------------------------------------------- 解析

def parse_sections(
    file: str,
    *,
    parse_python: str | None = None,
    parser: str | None = None,
    timeout: int = 900,
) -> list[Section]:
    """解析成节：`parse_python` 给定时走**独立解析环境子进程**（D7），否则本进程解析。"""
    if parse_python:
        return _parse_via_subprocess(file, parse_python, parser, timeout)
    from memory_agent.parse import parse_document
    from memory_agent.parse.ports import ParseError

    try:
        return parse_document(file, parser=parser)
    except ParseError as exc:  # 含 ParserUnavailable：统一成 IngestError 供 CLI 消费
        raise IngestError(str(exc)) from exc


def _parse_via_subprocess(
    file: str, python: str, parser: str | None, timeout: int
) -> list[Section]:
    command = [python, "-m", "memory_agent.parse_worker", "--file", os.path.abspath(file)]
    if parser:
        command += ["--parser", parser]
    env = dict(os.environ)
    env["PYTHONPATH"] = settings.ROOT_DIR + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            command, cwd=settings.ROOT_DIR, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise IngestError(f"解析解释器不存在：{python}") from exc
    except subprocess.TimeoutExpired as exc:
        raise IngestError(f"解析超时（{timeout}s）：{file}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise IngestError("解析子进程失败：" + (detail[-1] if detail else f"exit {proc.returncode}"))
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise IngestError(f"解析子进程输出不可解析：{(proc.stdout or '')[-400:]}") from exc
    if payload.get("error"):
        raise IngestError(payload["error"])
    return [
        Section(title=item["title"], level=int(item["level"]),
                markdown=item["markdown"], order=index)
        for index, item in enumerate(payload.get("sections", []))
    ]


# --------------------------------------------------------------------- 物化

def materialize_import(
    sections: list[Section],
    *,
    doc_path: str,
    label: str,
    type: str = DEFAULT_TYPE,
    tags= DEFAULT_TAGS,
    status: str = DEFAULT_STATUS,
    import_dir: str | None = None,
    id_base: str | None = None,
    max_chars: int | None = None,
    today: str | None = None,
) -> tuple[list, list[str]]:
    """节 -> 带 frontmatter 的 `.md`，落到 `<import_dir>/<label>/`；返回 (草稿, 路径)。

    `id_base = "<label>/<doc_slug>"` 时，frontmatter `id` == `source` 去掉 `.md`
    （`source = "<label>/<doc_slug>[-NN].md"`），与读侧条目自洽。
    """
    doc_slug = slug_for(os.path.splitext(os.path.basename(doc_path))[0])
    drafts = build_entries(
        list(sections),
        doc_path=doc_path,
        id_base=id_base or f"{label}/{doc_slug}",
        source=os.path.basename(doc_path),
        type=type,
        tags=list(tags),
        status=status,
        updated=today,
        max_chars=max_chars,
    )
    root = os.path.abspath(import_dir or import_root())
    written = write_entries(drafts, root)
    return drafts, written


# --------------------------------------------------------------------- 收录（只读）

def _resolve_config_path(pattern: str) -> str:
    return pattern if os.path.isabs(pattern) else os.path.join(settings.ROOT_DIR, pattern)


def register_import(
    import_dir: str,
    *,
    label: str,
    owner: str | None = None,
    overlay_path: str | None = None,
) -> dict:
    """把 import 目录加进 overlay 收录（显式只读来源，幂等）。"""
    from memory_agent.corpus import loader
    from memory_agent.memory.admission import AdmissionManager

    manager = AdmissionManager(overlay_path=overlay_path)
    pattern = os.path.abspath(import_dir)
    overlay, complete = loader.load_overlay()
    if not complete:
        raise IngestError(f"overlay 配置非法，请先修好：{manager.overlay_path}")
    for spec in overlay.get("include", []):
        existing = os.path.normcase(os.path.abspath(_resolve_config_path(spec["pattern"])))
        if existing == os.path.normcase(pattern):
            return {"status": "noop", "pattern": pattern, "reason": "已在 overlay 收录清单"}
    return manager.include(pattern=pattern, label=label, owner=owner, confirm=True)


# --------------------------------------------------------------------- 编排

def ingest_document(
    file: str,
    *,
    label: str | None = None,
    owner: str | None = None,
    tags=DEFAULT_TAGS,
    type: str = DEFAULT_TYPE,
    status: str = DEFAULT_STATUS,
    parser: str | None = None,
    parse_python: str | None = None,
    import_dir: str | None = None,
    uploads_dir: str | None = None,
    overlay_path: str | None = None,
    register: bool = True,
    today: str | None = None,
    max_chars: int | None = None,
) -> dict:
    """一篇文档 → 解析 → 物化只读 `.md` → overlay 收录；返回可机器消费的摘要。"""
    if not os.path.isfile(file):
        raise IngestError(f"文件不存在：{file}")
    extension = os.path.splitext(file)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise IngestError(
            f"不支持的格式 {extension or '(无扩展名)'}：仅支持 "
            + " / ".join(ext.upper() for ext in SUPPORTED_EXTENSIONS)
        )

    resolved_label = (label or slug_for(
        os.path.splitext(os.path.basename(file))[0])).strip()
    if not resolved_label:
        raise IngestError("label 不能为空")

    import_root_dir = os.path.abspath(import_dir or import_root())
    staged = stage_source(file, uploads_dir=os.path.abspath(uploads_dir or uploads_root()),
                          label=resolved_label)
    sections = parse_sections(file, parse_python=parse_python, parser=parser)
    if not sections:
        raise IngestError(f"未解析出任何节：{file}")
    drafts, written = materialize_import(
        sections, doc_path=file, label=resolved_label, type=type, tags=tags,
        status=status, import_dir=import_root_dir, max_chars=max_chars, today=today,
    )

    import_dir_for_label = os.path.join(import_root_dir, resolved_label)
    registration = None
    if register:
        registration = register_import(
            import_dir_for_label, label=resolved_label,
            owner=owner or resolved_label, overlay_path=overlay_path,
        )
    return {
        "status": "ingested",
        "label": resolved_label,
        "owner": owner or resolved_label,
        "source_file": os.path.abspath(file),
        "staged_original": staged,
        "import_dir": import_dir_for_label,
        "entries": len(drafts),
        "ids": [draft.id for draft in drafts],
        "files": written,
        "registration": registration,
        "note": "只读语料：物化 .md 经 overlay 收录，索引由 memory_search 惰性刷新纳入",
    }


# --------------------------------------------------------------------- CLI

def _split_tags(raw: str | None) -> tuple[str, ...]:
    tags = tuple(part.strip() for part in (raw or "").split(",") if part.strip())
    return tags or DEFAULT_TAGS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory-agent-ingest",
        description="PDF / DOCX → 解析 → 物化只读 Markdown 条目 → overlay 收录（#51）",
    )
    parser.add_argument("file", help="PDF / DOCX 路径")
    parser.add_argument("--label", help="来源标签（默认取文件名的 slug）")
    parser.add_argument("--owner", help="域 owner（默认 = label）")
    parser.add_argument("--tags", help="逗号分隔标签（默认 imported）")
    parser.add_argument("--type", default=DEFAULT_TYPE, help=f"frontmatter type（默认 {DEFAULT_TYPE}）")
    parser.add_argument("--status", default=DEFAULT_STATUS, help=f"frontmatter status（默认 {DEFAULT_STATUS}）")
    parser.add_argument("--parser", choices=["docling", "pypdf"], default=None)
    parser.add_argument("--parse-python", default=os.environ.get("MEMORY_PARSE_PYTHON"),
                        help="专用解析环境解释器（缺省用当前环境；主环境走 pypdf 兜底）")
    parser.add_argument("--import-dir", default=None, help="物化 .md 根（默认 memory_agent/imports）")
    parser.add_argument("--uploads-dir", default=None, help="原件暂存根（默认 memory_agent/uploads）")
    parser.add_argument("--overlay", default=None, help="overlay 配置路径（默认 memory_agent/overlay.json）")
    parser.add_argument("--no-register", action="store_true", help="只物化，不写 overlay 收录")
    args = parser.parse_args(argv)

    try:
        summary = ingest_document(
            args.file, label=args.label, owner=args.owner, tags=_split_tags(args.tags),
            type=args.type, status=args.status, parser=args.parser,
            parse_python=args.parse_python, import_dir=args.import_dir,
            uploads_dir=args.uploads_dir, overlay_path=args.overlay,
            register=not args.no_register,
        )
    except IngestError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
