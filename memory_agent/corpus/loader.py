"""语料装载：从 Markdown 真相源发现条目，并执行噪声排除。

两类语料：
- 可写：全局 KB 条目（必须有 frontmatter `id`；索引/模板/元文件不是条目）。
- 只读：注册表来源（带标签的项目仓库文档）+ 显式 overlay 收录（#36 / ADR-0025 D8）。
  只取 Markdown，不索引代码；source = `"<label>/<rel>"` 以消歧义。

**运行时重读**（#36 / ADR-0025 D8/D9）：来源注册表与 overlay 都在每次解析时重读，
不再 import 时定死——改配置 / overlay 免重启生效。`resolve_selection()` 产出
`Selection`（来源 + 选中文件 + 完整性标志），指纹与装载共用同一份选择。
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
from dataclasses import dataclass, field

from memory_agent import settings
from memory_agent.memory.entries import Entry, parse_frontmatter
from memory_agent.settings import MAX_CORPUS_FILE_BYTES, ROOT_DIR

EXCLUDE_DIR_NAMES = frozenset({
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".trae", ".tox", "vector_db", "uploads", "dist", "build",
    ".egg-info", "_generated",
    # 其它项目仓库里常见的生成物 / 工具缓存 / 数据目录（#7 用户故事 17）：
    "outputs", "dataset", ".scratch", ".playwright-cli", ".pi-agent",
    ".pi", ".claude", ".codex", ".uv", ".cache", "models",
})

# 相对仓库根的排除前缀（原始语料 / 生成数据）
EXCLUDE_REL_PREFIXES = ("legal_web/data/raw",)

# KB 里不是"条目"的元文件（生成或约定文件）
KB_META_FILES = frozenset({"INDEX.md", "AGENTS.md", "tags.md"})

KB_LABEL = "kb"
_WILDCARDS = "*?["


# --------------------------------------------------------------------- 文件遍历

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _iter_markdown(root: str) -> list[tuple[str, str, int, int]]:
    """遍历 root 下的 .md：`[(绝对路径, 相对 posix 路径, mtime_ns, size)]`。

    用 `os.scandir` + `DirEntry.stat()` 一次拿全（#36 / D9 的廉价指纹就靠它）——
    避免 `os.walk` + 二次 stat 的双倍系统调用。
    """
    root = os.path.abspath(root)
    found: list[tuple[str, str, int, int]] = []
    stack = [root]
    while stack:
        dirpath = stack.pop()
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            continue
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for entry in sorted(entries, key=lambda e: e.name):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in EXCLUDE_DIR_NAMES:
                        stack.append(entry.path)
                    continue
                if not entry.name.lower().endswith(".md"):
                    continue
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                if any(rel == p or rel.startswith(p + "/") for p in EXCLUDE_REL_PREFIXES):
                    continue
                stat = entry.stat()
                if stat.st_size > MAX_CORPUS_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append((entry.path, rel, stat.st_mtime_ns, stat.st_size))
    return found


def _within_size(path: str) -> bool:
    try:
        return os.path.getsize(path) <= MAX_CORPUS_FILE_BYTES
    except OSError:
        return False


# ------------------------------------------------------------- selection（#36）

@dataclass(frozen=True)
class Source:
    """一个收录来源：注册表一行，或 overlay 的一条显式 include。"""

    label: str
    root: str
    owner: str | None
    explicit: bool = False


@dataclass(frozen=True)
class SelectedFile:
    """被收录的一个文件（尚未读正文）；`origin` 由 `explicit` 区分默认 / 显式。"""

    path: str
    source: str
    root: str
    owner: str | None
    writable: bool
    explicit: bool
    mtime_ns: int | None = None
    size: int | None = None


@dataclass
class Selection:
    """一次运行时解析的结果：来源清单 + 选中文件 + 配置完整性。"""

    sources: list[Source] = field(default_factory=list)
    include_specs: list[dict] = field(default_factory=list)
    exclude_specs: list[str] = field(default_factory=list)
    files: list[SelectedFile] = field(default_factory=list)
    complete: bool = True


def _has_wildcard(text: str) -> bool:
    return any(ch in text for ch in _WILDCARDS)


def _abspath(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)


def _pattern_root(pattern: str) -> str:
    """路径模式对应的来源根（绝对路径）：wildcard 取首个通配符前的目录。"""
    absolute = os.path.abspath(_abspath(pattern))
    if _has_wildcard(absolute):
        cut = min(absolute.find(ch) for ch in _WILDCARDS if absolute.find(ch) >= 0)
        base = absolute[:cut]
        root = base if os.path.isdir(base) else os.path.dirname(base)
        return os.path.abspath(root or os.sep)
    if os.path.isdir(absolute):
        return absolute
    return os.path.dirname(absolute)


def _normalize_include(item) -> dict | None:
    """把 overlay include 项规整为 `{pattern, label, owner}`；非法返回 None。"""
    if isinstance(item, str):
        pattern = item.strip()
        return {"pattern": pattern, "label": None, "owner": None} if pattern else None
    if isinstance(item, dict):
        pattern = str(
            item.get("pattern") or item.get("glob") or item.get("path") or ""
        ).strip()
        if not pattern:
            return None
        label = str(item.get("label") or "").strip() or None
        owner = str(item.get("owner") or "").strip() or None
        return {"pattern": pattern, "label": label, "owner": owner}
    return None


def _normalize_exclude(item) -> str | None:
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        pattern = str(
            item.get("pattern") or item.get("glob") or item.get("path") or ""
        ).strip()
        return pattern or None
    return None


def load_overlay() -> tuple[dict, bool]:
    """读显式收录清单：`({"include": [...], "exclude": [...]}, complete)`。

    文件不存在 = 空 overlay 且 complete=True；文件存在但非法 = complete=False
    （调用方保守处理，不因读不出清单而误删条目）。
    """
    path = settings.overlay_config_file()
    if not os.path.isfile(path):
        return {"include": [], "exclude": []}, True
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"include": [], "exclude": []}, False
    if not isinstance(data, dict):
        return {"include": [], "exclude": []}, False

    raw_include = data.get("include") or []
    raw_exclude = data.get("exclude") or []
    if isinstance(raw_include, (str, dict)):
        raw_include = [raw_include]
    if isinstance(raw_exclude, str):
        raw_exclude = [raw_exclude]
    include = [spec for spec in (_normalize_include(i) for i in raw_include) if spec]
    exclude = [p for p in (_normalize_exclude(i) for i in raw_exclude) if p]
    return {"include": include, "exclude": exclude}, True


def _stat_file(path: str) -> tuple[str, int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return os.path.abspath(path), stat.st_mtime_ns, stat.st_size


def resolve_include(spec: dict) -> tuple[str, str, str | None, list[tuple[str, int, int]]]:
    """展开一条 include：返回 `(root, label, owner, [(绝对路径, mtime_ns, size)])`。

    - 精确文件 → 该文件；label 取所在目录名。
    - 目录 → 目录下 `.md`（沿用噪声排除）。
    - 其它 → glob（支持 `**`）；root 取通配符前的目录。
    """
    pattern = spec["pattern"]
    absolute = os.path.abspath(_abspath(pattern))
    if os.path.isfile(absolute):
        entries = [absolute] if absolute.lower().endswith(".md") else []
        root = os.path.dirname(absolute)
        files = [s for s in (_stat_file(f) for f in entries) if s]
    elif os.path.isdir(absolute):
        root = absolute
        files = [(full, mtime, size) for full, _, mtime, size in _iter_markdown(root)]
    else:
        root = _pattern_root(absolute)
        matches = sorted(
            os.path.abspath(m) for m in glob.glob(absolute, recursive=True)
            if os.path.isfile(m) and m.lower().endswith(".md")
        )
        files = [s for s in (_stat_file(f) for f in matches) if s
                 and s[2] <= MAX_CORPUS_FILE_BYTES]
    label = spec.get("label") or os.path.basename(root.rstrip("\\/")) or "overlay"
    return root, label, spec.get("owner"), files


def owner_for_path(path: str, registry: list[dict], fallback: str | None) -> str | None:
    """文件所属域 owner：优先登记它的注册表来源，否则用显式 fallback。"""
    normalized = os.path.normcase(os.path.abspath(path))
    best: tuple[int, str | None] = (-1, fallback)
    for item in registry:
        root = os.path.normcase(os.path.abspath(item["path"]))
        if normalized == root or normalized.startswith(root.rstrip("\\/") + os.sep):
            if len(root) > best[0]:
                best = (len(root), item.get("owner"))
    return best[1]


def matches_exclude(selected: SelectedFile, pattern: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(selected.path)).replace("\\", "/")
    target = os.path.normcase(os.path.normpath(_abspath(pattern))).replace("\\", "/")
    if _has_wildcard(target):
        return (fnmatch.fnmatch(normalized, target)
                or fnmatch.fnmatch(selected.source, pattern))
    return normalized == target or normalized.startswith(target.rstrip("/") + "/")


def resolve_selection() -> Selection:
    """运行时解析收录：KB（可写）∪ 注册表默认 ∪ overlay 显式，再减去 exclude。

    同一文件被多处收录时，先到先得（KB > 显式 overlay > 注册表默认）；
    overlay 显式收录会覆盖注册表给同一文件的归属（`explicit=True`）。
    """
    registry, registry_complete = settings.readonly_sources()
    overlay, overlay_complete = load_overlay()
    selection = Selection(
        include_specs=overlay["include"],
        exclude_specs=overlay["exclude"],
        complete=registry_complete and overlay_complete,
    )

    sources: list[Source] = []
    files: list[SelectedFile] = []
    seen: set[str] = set()

    def add(path: str, source: str, root: str, owner, writable: bool, explicit: bool,
            mtime_ns: int | None = None, size: int | None = None) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        files.append(SelectedFile(
            path=os.path.abspath(path), source=source, root=root,
            owner=owner, writable=writable, explicit=explicit,
            mtime_ns=mtime_ns, size=size,
        ))

    kb_dir = os.path.abspath(settings.KB_DIR)
    kb_source = Source(label=KB_LABEL, root=kb_dir, owner=settings.KB_OWNER)
    sources.append(kb_source)
    if os.path.isdir(kb_dir):
        for full, rel, mtime_ns, size in _iter_markdown(kb_dir):
            add(full, rel, kb_dir, settings.KB_OWNER, True, False, mtime_ns, size)

    for spec in overlay["include"]:
        root, label, owner, matched = resolve_include(spec)
        label = spec.get("label") or label
        owner = spec.get("owner") or owner
        sources.append(Source(label=label, root=root, owner=owner, explicit=True))
        for full, mtime_ns, size in matched:
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            resolved_owner = owner or owner_for_path(full, registry, label)
            add(full, f"{label}/{rel}", root, resolved_owner, False, True, mtime_ns, size)

    for item in registry:
        root = item["path"]
        source = Source(label=item["label"], root=root, owner=item.get("owner"))
        sources.append(source)
        if not os.path.isdir(root):
            continue
        for full, rel, mtime_ns, size in _iter_markdown(root):
            add(full, f"{item['label']}/{rel}", root, item.get("owner"), False, False,
                mtime_ns, size)

    if selection.exclude_specs:
        files = [f for f in files if not any(
            matches_exclude(f, pattern) for pattern in selection.exclude_specs
        )]

    selection.sources = sources
    selection.files = sorted(files, key=lambda f: (not f.writable, f.source))
    return selection


# --------------------------------------------------------------------- 装载

def load_kb_entries(kb_dir: str | None = None) -> list[Entry]:
    """装载 KB 条目：必须有 frontmatter `id`，跳过元文件与 `_` 前缀（生成的 _index 等）。"""
    kb_dir = os.path.abspath(kb_dir or settings.KB_DIR)
    if not os.path.isdir(kb_dir):
        return []
    entries: list[Entry] = []
    for full, rel, _mtime_ns, _size in _iter_markdown(kb_dir):
        name = os.path.basename(rel)
        if name in KB_META_FILES or name.startswith("_"):
            continue
        if any(part.startswith("_") for part in rel.split("/")[:-1]):
            continue
        meta, _ = parse_frontmatter(_read_text(full))
        if not meta.get("id"):
            continue
        entries.append(Entry.from_file(
            full, source=rel, writable=True, owner=settings.KB_OWNER, root=kb_dir
        ))
    return entries


def _normalize_roots(roots: list | None) -> list[tuple[str, str]]:
    """把只读根规整为 [(label, abs_path)]（显式 roots / 兼容旧调用）。

    接受 `(label, path)` 元组或裸路径字符串（label 由目录名派生）。
    """
    items = list(settings._readonly_roots() if roots is None else roots)
    out: list[tuple[str, str]] = []
    for root in items:
        if isinstance(root, (tuple, list)):
            label, path = str(root[0]), str(root[1])
        else:
            path = str(root)
            label = os.path.basename(os.path.abspath(path).rstrip("\\/")) or "repo"
        out.append((label, os.path.abspath(path)))
    return out


def load_readonly_entries(roots: list | None = None,
                          selection: Selection | None = None) -> list[Entry]:
    """装载只读语料，writable=False，source 带来源标签前缀。

    `roots` 显式给定时走兼容路径（测试 / 旧调用）；否则运行时解析 selection。
    """
    if roots is not None:
        entries: list[Entry] = []
        for label, root in _normalize_roots(roots):
            if not os.path.isdir(root):
                continue
            for full, rel, _mtime_ns, _size in _iter_markdown(root):
                entries.append(Entry.from_file(
                    full, source=f"{label}/{rel}", writable=False, owner=label, root=root
                ))
        return entries

    resolved = selection or resolve_selection()
    entries = []
    for selected in resolved.files:
        if selected.writable:
            continue
        entries.append(Entry.from_file(
            selected.path, source=selected.source, writable=False,
            owner=selected.owner, root=selected.root,
        ))
    return entries


def load_corpus(
    kb_dir: str | None = None,
    readonly_roots: list | None = None,
) -> list[Entry]:
    """装载全部语料，按 id 去重（KB 优先），返回稳定排序的条目列表。"""
    entries = load_kb_entries(kb_dir) + load_readonly_entries(readonly_roots)
    deduped: dict[str, Entry] = {}
    for entry in entries:
        deduped.setdefault(entry.id, entry)
    return sorted(deduped.values(), key=lambda e: (not e.writable, e.id))


def scan_fingerprint() -> tuple[dict[str, list[int]], bool]:
    """廉价指纹（#36 / D9）：只 stat 选中文件，不读正文。

    返回 `({normcase(path): [mtime_ns, size]}, complete)`；调用方与 manifest 里存的
    指纹比对，不同才做增量刷新。
    """
    selection = resolve_selection()
    fingerprint: dict[str, list[int]] = {}
    for selected in selection.files:
        if selected.mtime_ns is None or selected.size is None:
            continue
        fingerprint[os.path.normcase(selected.path)] = [selected.mtime_ns, selected.size]
    return fingerprint, selection.complete
