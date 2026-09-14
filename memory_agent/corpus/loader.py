"""语料装载：从 Markdown 真相源发现条目，并执行噪声排除。

两类语料：
- 可写：全局 KB 条目（必须有 frontmatter `id`；索引/模板/元文件不是条目）。
- 只读：若干**带标签的项目仓库文档**（只取 Markdown，不索引代码；无 frontmatter
  也可，payload 标 `writable:false`）。source = `"<label>/<rel>"` 以消歧义。
"""
from __future__ import annotations

import os

from memory_agent.memory.entries import Entry, parse_frontmatter
from memory_agent.settings import KB_DIR, MAX_CORPUS_FILE_BYTES, READONLY_ROOTS

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


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _iter_markdown(root: str) -> list[tuple[str, str]]:
    """遍历 root 下的 .md，返回 [(绝对路径, 相对 root 的 posix 路径)]，按路径排序。"""
    found: list[tuple[str, str]] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if any(rel == p or rel.startswith(p + "/") for p in EXCLUDE_REL_PREFIXES):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_CORPUS_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append((full, rel))
    return found


def load_kb_entries(kb_dir: str | None = None) -> list[Entry]:
    """装载 KB 条目：必须有 frontmatter `id`，跳过元文件与 `_` 前缀（生成的 _index 等）。"""
    kb_dir = os.path.abspath(kb_dir or KB_DIR)
    if not os.path.isdir(kb_dir):
        return []
    entries: list[Entry] = []
    for full, rel in _iter_markdown(kb_dir):
        name = os.path.basename(rel)
        if name in KB_META_FILES or name.startswith("_"):
            continue
        if any(part.startswith("_") for part in rel.split("/")[:-1]):
            continue
        meta, _ = parse_frontmatter(_read_text(full))
        if not meta.get("id"):
            continue
        entries.append(Entry.from_file(full, source=rel, writable=True))
    return entries


def _normalize_roots(
    roots: list | None,
) -> list[tuple[str, str]]:
    """把只读根规整为 [(label, abs_path)]。

    接受 `(label, path)` 元组（settings.READONLY_ROOTS 的形状）或裸路径字符串
    （label 由目录名派生，兼容环境覆盖与旧测试）。
    """
    items = list(READONLY_ROOTS if roots is None else roots)
    out: list[tuple[str, str]] = []
    for root in items:
        if isinstance(root, (tuple, list)):
            label, path = str(root[0]), str(root[1])
        else:
            path = str(root)
            label = os.path.basename(os.path.abspath(path).rstrip("\\/")) or "repo"
        out.append((label, os.path.abspath(path)))
    return out


def load_readonly_entries(roots: list | None = None) -> list[Entry]:
    """装载只读语料（项目仓库 Markdown），writable=False，source 带仓库标签前缀。"""
    entries: list[Entry] = []
    for label, root in _normalize_roots(roots):
        if not os.path.isdir(root):
            continue
        for full, rel in _iter_markdown(root):
            entries.append(Entry.from_file(full, source=f"{label}/{rel}", writable=False))
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
