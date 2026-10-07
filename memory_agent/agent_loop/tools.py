"""工具适配：把记忆检索暴露成 `ToolRegistry`。

- `MemoryToolRegistry`（检索面）保持不变：`memory_search` / `memory_get`。
- `MemoryNavToolRegistry`（#63 / ADR-0030 D7.6）在它之上加六个**只读导航**工具：
  `memory_list` / `memory_grep` / `memory_read` / `memory_outline` / `memory_history`
  / `memory_links`——把检索从"只吃 top-k"升级为"由宽到窄导航"。

纪律（照 #63 的硬约束）：
- **只读**：本模块只有三种动作——读 manifest（元数据）+ 读真相源 Markdown + 只读 git
  （`log` / `diff` / `blame`，子命令白名单，**永不** commit / checkout / reset）；
  没有任何写路径，也不改检索默认 / 合成。
- **治理只可收窄**：`visibility(meta) -> bool` 由**网关注入**（`gateway.authz.can_read`），
  工具参数里**没有** tenant / classification / residency，模型无法放宽；谓词出错按"看不见"处理。
- **工具目录**来自 `list_tools()`（ADR-0030 D7.6），派发仍走 `TOOL:` 协议（`loop.py`）。
- **证据单一语义**（ADR-0030 D7.4）：逐条目结果带顶层 `id`（`memory_read` /
  `memory_outline(entry_id)` / `memory_history` / `memory_links`）与 `memory_grep` 的命中
  列表 → 被 `AgentLoop` 记为 `evidence_ids`（真展示给模型）；**浏览类**结果
  （`memory_list` / 全局 `memory_outline`）不带顶层 `id`，只做导航、不占证据额度。
- 本模块**不 import 评测**（守卫 `tests/unit/test_agent_loop_isolation.py`）。

边界（如实标注）：导航工具作用在**索引基表**上——只读语料只装 `.md`，代码文件不在基表里，
所以 `memory_grep` 等**结构性够不到**代码（#71 的 `nav-071-b0*` 边界层，见
`standard_sets/nav_probes_71.spec.md` §2）。
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from typing import Any, Callable, Iterator

SEARCH_TOOL = "memory_search"
GET_TOOL = "memory_get"
LIST_TOOL = "memory_list"
GREP_TOOL = "memory_grep"
READ_TOOL = "memory_read"
OUTLINE_TOOL = "memory_outline"
HISTORY_TOOL = "memory_history"
LINKS_TOOL = "memory_links"

#: 六个导航工具（不含检索面）——评测 / 文档按此枚举。
NAV_TOOLS = (LIST_TOOL, GREP_TOOL, READ_TOOL, OUTLINE_TOOL, HISTORY_TOOL, LINKS_TOOL)

# 与 `memory_agent.memory.index.RETIRED_STATUSES` 同义。这里刻意**不 import 具体实现**
# （agent_loop 只依赖端口 / 鸭子类型）；若那边改了退役口径，这里要一起改。
RETIRED_STATUSES = frozenset({"superseded", "archived"})

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
DEFAULT_GREP_LIMIT = 50
MAX_GREP_LIMIT = 200
DEFAULT_OUTLINE_ENTRIES = 50
MAX_OUTLINE_HEADINGS = 40
DEFAULT_READ_LINES = 200
MAX_READ_LINES = 400
GIT_TIMEOUT_S = 20
#: 只读 git 子命令白名单（**防注入 + 防写**）。
GIT_OPS = frozenset({"log", "diff", "blame"})


# ------------------------------------------------------------------ 纯文本工具
#
# 这些是纯函数（不碰 index / 网络），单测直接打边界；工具方法只是薄壳。
# 口径与 `memory_agent/eval/eval_71_nav.py` 的验证器**一致但独立**实现——
# 运行时不得反向依赖评测（ADR-0030 D7.1）。

def scan_headings(text: str) -> list[dict[str, Any]]:
    """标题树：`[{level, text, line}]`，**跳过 fenced code block**（``` / ~~~）。"""
    out: list[dict[str, Any]] = []
    fence: str | None = None
    for lineno, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        match = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if match:
            out.append({"level": len(match.group(1)),
                        "text": match.group(2).strip(), "line": lineno})
    return out


def line_window(text: str, start: Any = None, end: Any = None, *,
                max_lines: int = DEFAULT_READ_LINES) -> dict[str, Any]:
    """1-based 闭区间行窗；越界**夹取**并如实报告（不抛异常）。

    返回 `{line_range, returned_lines, total_lines, text, clamped, out_of_range}`。
    空文件 / 起点在 EOF 之后 → `line_range=None`、`out_of_range=True`（不是错误）。
    """
    lines = (text or "").splitlines()
    total = len(lines)
    empty = {"line_range": None, "returned_lines": 0, "total_lines": total,
             "text": "", "clamped": False, "out_of_range": False}
    if total == 0:
        return empty
    try:
        lo = 1 if start is None else int(start)
        hi = total if end is None else int(end)
    except (TypeError, ValueError):
        return {**empty, "error": "bad_line_range", "requested": [start, end]}
    clamped = False
    if lo < 1:
        lo, clamped = 1, True
    if hi > total:
        hi, clamped = total, True
    if lo > total:
        return {**empty, "clamped": True, "out_of_range": True}
    if hi < lo:
        return {**empty, "clamped": True, "out_of_range": True, "requested": [start, end]}
    width = max(1, int(max_lines))
    if hi - lo + 1 > width:
        hi, clamped = lo + width - 1, True
    return {"line_range": [lo, hi], "returned_lines": hi - lo + 1, "total_lines": total,
            "text": "\n".join(lines[lo - 1:hi]), "clamped": clamped,
            "out_of_range": False}


def grep_lines(text: str, pattern: str, *, regex: bool = False,
               ignore_case: bool = True) -> list[tuple[int, str]]:
    """逐行匹配，返回 `[(行号, 行文本)]`。字面量默认大小写不敏感。"""
    lines = (text or "").splitlines()
    if regex:
        flags = re.IGNORECASE if ignore_case else 0
        matcher = re.compile(pattern, flags)
        return [(i, line) for i, line in enumerate(lines, 1) if matcher.search(line)]
    needle = pattern.lower() if ignore_case else pattern
    found: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        haystack = line.lower() if ignore_case else line
        if needle in haystack:
            found.append((i, line))
    return found


def frontmatter_scalar(text: str, key: str) -> str | None:
    """取 frontmatter 里的顶层标量（够用于 `supersedes` / `superseded_by` 链接）。

    刻意不 import yaml：导航工具只需要标量，保持运行时零重依赖。
    """
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---", text or "", re.DOTALL)
    if not match:
        return None
    found = re.search(rf"^{re.escape(key)}[ \t]*:[ \t]*(.+?)[ \t]*$",
                      match.group(1), re.MULTILINE)
    if not found:
        return None
    value = found.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value or None


def glob_match(rel: str, pattern: str) -> bool:
    """支持 `**` 的简化 glob（相对 posix 路径）——与 #71 验证器同口径。"""
    rel = (rel or "").replace("\\", "/")
    if "**" in pattern:
        head, _, tail = pattern.partition("**")
        prefix = head.rstrip("/")
        suffix = tail.lstrip("/") or "*"
        return (not prefix or rel == prefix or rel.startswith(prefix + "/")) \
            and fnmatch.fnmatch(rel, suffix)
    return fnmatch.fnmatch(rel, pattern)


# ------------------------------------------------------------------ 只读 git

def default_git_runner(args: list[str]) -> dict[str, Any]:
    """跑一条**只读** git 命令（`args` 已含 `-C <root>`）；返回 rc/stdout/stderr。"""
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S)
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "找不到 git 可执行文件"}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": f"git 超时（>{GIT_TIMEOUT_S}s）"}
    return {"returncode": proc.returncode, "stdout": proc.stdout or "",
            "stderr": proc.stderr or ""}


def entry_history(repo_root: str, rel_path: str, *, op: str = "log", rev: str | None = None,
                  limit: int = 20, pattern: str | None = None, added_only: bool = False,
                  max_chars: int = 4000,
                  git_runner: Callable[[list[str]], dict] | None = None) -> dict[str, Any]:
    """条目的只读 git 历史：`log`（可选 `-S` pickaxe / `--diff-filter=A`）/ `diff` / `blame`。

    只走白名单子命令；路径以 `--` 结束分隔，防参数注入。
    """
    if op not in GIT_OPS:
        return {"error": "unsupported_op", "op": op, "allowed": sorted(GIT_OPS)}
    runner = git_runner or default_git_runner
    rel = (rel_path or "").replace("\\", "/")
    if not rel:
        return {"error": "missing_path"}
    if rel.startswith("-"):
        rel = "./" + rel
    pre = ["-C", repo_root]
    count = max(1, int(limit))

    if op == "log":
        args = [*pre, "log", "--no-color", "--date=short", "--format=%H\t%ad\t%s",
                "-n", str(count)]
        if added_only:
            args.append("--diff-filter=A")
        if pattern:
            args += ["-S", str(pattern)]
        args += ["--", rel]
        result = runner(args)
        if result.get("returncode") != 0:
            return {"error": "git_failed", "op": op, "path": rel,
                    "detail": (result.get("stderr") or "")[:200],
                    "returncode": result.get("returncode")}
        commits = []
        for line in (result.get("stdout") or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
        return {"op": "log", "repo_root": repo_root, "path": rel,
                "pattern": pattern, "added_only": bool(added_only),
                "commits": commits, "count": len(commits)}

    if op == "diff":
        base = str(rev or "HEAD")
        args = [*pre, "diff", "--no-color", base, "--", rel]
        result = runner(args)
        if result.get("returncode") != 0:
            return {"error": "git_failed", "op": op, "path": rel, "base": base,
                    "detail": (result.get("stderr") or "")[:200],
                    "returncode": result.get("returncode")}
        patch = result.get("stdout") or ""
        return {"op": "diff", "repo_root": repo_root, "path": rel, "base": base,
                "patch": patch[:max_chars], "truncated": len(patch) > max_chars,
                "bytes": len(patch)}

    # blame：默认只看前 N 行，避免整条大文件把结果撑爆。
    lines = max(1, min(count, MAX_READ_LINES))
    args = [*pre, "blame", "--no-color", "--date=short", "-L", f"1,{lines}", "--", rel]
    result = runner(args)
    if result.get("returncode") != 0:
        return {"error": "git_failed", "op": op, "path": rel,
                "detail": (result.get("stderr") or "")[:200],
                "returncode": result.get("returncode")}
    raw = result.get("stdout") or ""
    return {"op": "blame", "repo_root": repo_root, "path": rel, "lines": lines,
            "blame": raw[:max_chars], "truncated": len(raw) > max_chars}


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


# ------------------------------------------------------------------ 检索面

class MemoryToolRegistry:
    """`memory_search` / `memory_get` 的只读适配器。"""

    def __init__(self, index, *, exclude_retired: bool = False):
        self._index = index
        self._exclude_retired = exclude_retired

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": SEARCH_TOOL,
                "description": "语义检索记忆条目（可写 KB + 只读语料）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": GET_TOOL,
                "description": "按 id 读回条目真实 Markdown。",
                "parameters": {
                    "type": "object",
                    "properties": {"entry_id": {"type": "string"}},
                    "required": ["entry_id"],
                },
            },
        ]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == SEARCH_TOOL:
            return self._index.search(
                args["query"],
                k=int(args.get("k", 5)),
                exclude_retired=self._exclude_retired,
            )
        if name == GET_TOOL:
            return self._index.get(args["entry_id"])
        raise KeyError(f"未知工具：{name}")


# ------------------------------------------------------------------ 导航面

class MemoryNavToolRegistry(MemoryToolRegistry):
    """检索面 + 六个**只读导航**工具（#63）。

    `visibility` 是**网关注入**的读授权谓词（生产接 `gateway.authz.can_read`）；
    它**不在**任何工具参数里，因此模型无法放宽——所有导航结果都先过它。
    `visibility=None` = 不限制（本地单租户默认）。
    """

    def __init__(self, index, *, exclude_retired: bool = False,
                 visibility: Callable[[dict], bool] | None = None,
                 git_runner: Callable[[list[str]], dict] | None = None,
                 max_entries: int = MAX_LIST_LIMIT,
                 max_read_lines: int = MAX_READ_LINES):
        super().__init__(index, exclude_retired=exclude_retired)
        self._visibility = visibility
        self._git_runner = git_runner
        self._max_entries = _clamp(max_entries, MAX_LIST_LIMIT, 1, MAX_LIST_LIMIT)
        self._max_read_lines = _clamp(max_read_lines, MAX_READ_LINES, 1, MAX_READ_LINES)

    # ---------------------------------------------------------------- catalog

    def list_tools(self) -> list[dict[str, Any]]:
        tools = list(super().list_tools())
        tools.extend([
            {
                "name": LIST_TOOL,
                "description": "浏览记忆条目（目录树 / owner / tag / type / status），"
                               "由宽到窄的第一步；不返回正文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "tag": {"type": "string"},
                        "type": {"type": "string"},
                        "status": {"type": "string"},
                        "source_prefix": {"type": "string"},
                        "glob": {"type": ["string", "array"]},
                        "writable": {"type": "boolean"},
                        "group_by": {"type": "string",
                                     "enum": ["source", "owner", "tag", "type"]},
                        "limit": {"type": "integer", "default": DEFAULT_LIST_LIMIT},
                        "offset": {"type": "integer", "default": 0},
                    },
                },
            },
            {
                "name": GREP_TOOL,
                "description": "在可见条目正文里做字面 / 正则精搜（词典外 token：sha、"
                               "环境变量名、编号）；glob 收窄来源。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "glob": {"type": ["string", "array"]},
                        "regex": {"type": "boolean", "default": False},
                        "ignore_case": {"type": "boolean", "default": True},
                        "context": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": DEFAULT_GREP_LIMIT},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": READ_TOOL,
                "description": "按 id + 行范围读回条目（1-based 闭区间，省 token）；"
                               "越界自动夹取并如实报告。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                        "max_lines": {"type": "integer", "default": DEFAULT_READ_LINES},
                    },
                    "required": ["entry_id"],
                },
            },
            {
                "name": OUTLINE_TOOL,
                "description": "看标题结构（跳过 fenced code block）；不给 entry_id = "
                               "看全局大纲。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "limit": {"type": "integer", "default": DEFAULT_OUTLINE_ENTRIES},
                    },
                },
            },
            {
                "name": HISTORY_TOOL,
                "description": "看条目文件的 git 历史（只读）：log（可 -S 模式 / 只看新增）"
                               "/ diff / blame。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "op": {"type": "string", "enum": ["log", "diff", "blame"]},
                        "pattern": {"type": "string"},
                        "added_only": {"type": "boolean", "default": False},
                        "rev": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["entry_id"],
                },
            },
            {
                "name": LINKS_TOOL,
                "description": "看条目的 supersede 链（supersedes / superseded_by 双向 + 链）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "depth": {"type": "integer", "default": 3},
                    },
                    "required": ["entry_id"],
                },
            },
        ])
        return tools

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == LIST_TOOL:
            return self._tool_list(args or {})
        if name == GREP_TOOL:
            return self._tool_grep(args or {})
        if name == READ_TOOL:
            return self._tool_read(args or {})
        if name == OUTLINE_TOOL:
            return self._tool_outline(args or {})
        if name == HISTORY_TOOL:
            return self._tool_history(args or {})
        if name == LINKS_TOOL:
            return self._tool_links(args or {})
        return super().call(name, args)

    # ------------------------------------------------------------- visibility

    def _visible(self, meta: dict) -> bool:
        """过**网关注入**的读授权。谓词出错 = 看不见（只可收窄，绝不静默放宽）。"""
        if self._visibility is None:
            return True
        try:
            return bool(self._visibility(meta))
        except Exception:
            return False

    def _load(self, entry_id: Any) -> dict | None:
        """按 id 取可见条目（含正文）；不存在 / 不可见 / 文件已丢 → None。

        不存在与不可见返回同一个信号：不泄漏"存在但你看不到"。
        """
        if not entry_id or not isinstance(entry_id, str):
            return None
        try:
            meta = self._index.get(entry_id)
        except (KeyError, FileNotFoundError):
            return None
        if not isinstance(meta, dict) or not self._visible(meta):
            return None
        return meta

    def _iter_entries(self) -> Iterator[tuple[str, dict]]:
        try:
            ids = sorted(self._index.known_ids())
        except Exception:
            return
        for entry_id in ids:
            meta = self._load(entry_id)
            if meta is None:
                continue
            if self._exclude_retired and meta.get("status") in RETIRED_STATUSES:
                continue
            yield entry_id, meta

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _summary(entry_id: str, meta: dict) -> dict[str, Any]:
        content = meta.get("content")
        return {
            "id": entry_id,
            "title": meta.get("title"),
            "source": meta.get("source"),
            "owner": meta.get("owner"),
            "type": meta.get("type"),
            "tags": list(meta.get("tags") or []),
            "status": meta.get("status"),
            "writable": bool(meta.get("writable")),
            "classification": meta.get("classification"),
            "residency": meta.get("residency"),
            "tenant": meta.get("tenant"),
            "lines": (content.count("\n") + 1) if isinstance(content, str) else None,
        }

    def _match_filters(self, meta: dict, args: dict) -> bool:
        owner = args.get("owner")
        if owner and meta.get("owner") != owner:
            return False
        tag = args.get("tag")
        if tag and tag not in (meta.get("tags") or []):
            return False
        kind = args.get("type")
        if kind and meta.get("type") != kind:
            return False
        status = args.get("status")
        if status and meta.get("status") != status:
            return False
        source = str(meta.get("source") or "")
        prefix = args.get("source_prefix")
        if prefix and not source.startswith(str(prefix)):
            return False
        writable = args.get("writable")
        if writable is not None and bool(meta.get("writable")) != bool(writable):
            return False
        globs = _as_list(args.get("glob"))
        if globs and not any(glob_match(source, pattern) for pattern in globs):
            return False
        return True

    @staticmethod
    def _group_key(meta: dict, group_by: str) -> str:
        if group_by == "owner":
            return str(meta.get("owner") or "(none)")
        if group_by == "type":
            return str(meta.get("type") or "(none)")
        if group_by == "tag":
            tags = meta.get("tags") or []
            return str(tags[0]) if tags else "(none)"
        source = str(meta.get("source") or "")
        head = source.split("/", 1)[0]
        return head or "(root)"

    # --------------------------------------------------------------- tools

    def _tool_list(self, args: dict) -> dict[str, Any]:
        limit = _clamp(args.get("limit"), DEFAULT_LIST_LIMIT, 1, self._max_entries)
        offset = _clamp(args.get("offset"), 0, 0, 1_000_000)
        group_by = str(args.get("group_by") or "source")
        if group_by not in ("source", "owner", "tag", "type"):
            return {"error": "bad_group_by", "group_by": group_by,
                    "allowed": ["source", "owner", "tag", "type"]}
        rows: list[tuple[str, dict]] = []
        for entry_id, meta in self._iter_entries():
            if self._match_filters(meta, args):
                rows.append((entry_id, meta))
        total = len(rows)
        groups: dict[str, int] = {}
        for _entry_id, meta in rows:
            key = self._group_key(meta, group_by)
            groups[key] = groups.get(key, 0) + 1
        page = rows[offset:offset + limit]
        return {
            "group_by": group_by,
            "total": total,
            "offset": offset,
            "returned": len(page),
            "truncated": offset + len(page) < total,
            "groups": [{"key": key, "count": groups[key]} for key in sorted(groups)],
            "entries": [self._summary(entry_id, meta) for entry_id, meta in page],
        }

    def _tool_grep(self, args: dict) -> Any:
        """精搜：返回**命中列表**（与 `memory_search` 同形状）。

        形状对齐不是审美问题：`AgentLoop._dispatch` 只把「list 里的 id」或「带 id 的单个
        dict」记为 `evidence_ids`（ADR-0030 D7.4 = 真正展示给模型的条目）。grep 的命中
        确实是展示给模型的，所以这里回 list（每项带 `id`），而不是包一层聚合 dict。
        截断由 `len(hits) == limit` 表达。
        """
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return {"error": "empty_pattern"}
        regex = bool(args.get("regex"))
        ignore_case = bool(args.get("ignore_case", True))
        limit = _clamp(args.get("limit"), DEFAULT_GREP_LIMIT, 1, MAX_GREP_LIMIT)
        context = _clamp(args.get("context"), 0, 0, 10)
        globs = _as_list(args.get("glob"))
        matcher = None
        if regex:
            try:
                matcher = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
            except re.error as exc:
                return {"error": "bad_regex", "detail": str(exc)}
        hits: list[dict[str, Any]] = []
        for entry_id, meta in self._iter_entries():
            source = str(meta.get("source") or "")
            if globs and not any(glob_match(source, item) for item in globs):
                continue
            content = meta.get("content")
            if not isinstance(content, str):
                continue
            lines = content.splitlines()
            for lineno, line in enumerate(lines, 1):
                if matcher is not None:
                    matched = bool(matcher.search(line))
                elif ignore_case:
                    matched = pattern.lower() in line.lower()
                else:
                    matched = pattern in line
                if not matched:
                    continue
                hit = {"id": entry_id, "source": source, "line": lineno, "text": line}
                if context:
                    lo = max(1, lineno - context)
                    hi = min(len(lines), lineno + context)
                    hit["context_range"] = [lo, hi]
                    hit["context"] = "\n".join(lines[lo - 1:hi])
                hits.append(hit)
                if len(hits) >= limit:
                    return hits
        return hits

    def _tool_read(self, args: dict) -> dict[str, Any]:
        entry_id = args.get("entry_id")
        meta = self._load(entry_id)
        if meta is None:
            return {"error": "unknown_entry", "entry_id": entry_id}
        max_lines = min(self._max_read_lines,
                        _clamp(args.get("max_lines"), DEFAULT_READ_LINES, 1, MAX_READ_LINES))
        window = line_window(meta.get("content") or "", args.get("start_line"),
                             args.get("end_line"), max_lines=max_lines)
        return {"id": entry_id, "source": meta.get("source"), "title": meta.get("title"),
                **window}

    def _tool_outline(self, args: dict) -> dict[str, Any]:
        entry_id = args.get("entry_id")
        limit = _clamp(args.get("limit"), DEFAULT_OUTLINE_ENTRIES, 1, self._max_entries)
        if entry_id:
            meta = self._load(entry_id)
            if meta is None:
                return {"error": "unknown_entry", "entry_id": entry_id}
            headings = scan_headings(meta.get("content") or "")
            return {"id": entry_id, "title": meta.get("title"),
                    "headings": headings[:MAX_OUTLINE_HEADINGS],
                    "count": len(headings),
                    "truncated": len(headings) > MAX_OUTLINE_HEADINGS}
        entries: list[dict[str, Any]] = []
        total = 0
        for item_id, meta in self._iter_entries():
            headings = scan_headings(meta.get("content") or "")
            if not headings:
                continue
            total += 1
            if len(entries) >= limit:
                continue
            entries.append({"id": item_id, "title": meta.get("title"),
                            "top": headings[0],
                            "headings": headings[:MAX_OUTLINE_HEADINGS],
                            "count": len(headings)})
        return {"entries": entries, "returned": len(entries), "with_outline": total,
                "truncated": total > len(entries)}

    def _repo_root(self, meta: dict) -> str | None:
        bases = [meta.get("root"), os.path.dirname(str(meta.get("path") or ""))]
        for base in bases:
            if not base:
                continue
            probe = os.path.abspath(str(base))
            for _ in range(8):
                git_marker = os.path.join(probe, ".git")
                if os.path.isdir(git_marker) or os.path.isfile(git_marker):
                    return probe
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
        return None

    def _tool_history(self, args: dict) -> dict[str, Any]:
        entry_id = args.get("entry_id")
        meta = self._load(entry_id)
        if meta is None:
            return {"error": "unknown_entry", "entry_id": entry_id}
        op = str(args.get("op") or "log")
        if op not in GIT_OPS:
            return {"error": "unsupported_op", "op": op, "allowed": sorted(GIT_OPS)}
        path = str(meta.get("path") or "")
        if not path or not os.path.isfile(path):
            return {"error": "missing_file", "entry_id": entry_id, "path": path}
        repo = self._repo_root(meta)
        if repo is None:
            return {"error": "not_a_git_repo", "entry_id": entry_id, "path": path}
        rel = os.path.relpath(path, repo)
        if rel.startswith(".."):
            return {"error": "outside_repo", "entry_id": entry_id, "path": path}
        result = entry_history(
            repo, rel, op=op, rev=args.get("rev"),
            limit=_clamp(args.get("limit"), 20, 1, MAX_READ_LINES),
            pattern=args.get("pattern"), added_only=bool(args.get("added_only")),
            git_runner=self._git_runner,
        )
        result["entry_id"] = entry_id
        result["id"] = entry_id
        result["source"] = meta.get("source")
        return result

    def _tool_links(self, args: dict) -> dict[str, Any]:
        entry_id = args.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            return {"error": "missing_entry_id"}
        depth = _clamp(args.get("depth"), 3, 1, 10)
        nodes: dict[str, dict[str, Any]] = {}
        for item_id, meta in self._iter_entries():
            content = meta.get("content") or ""
            nodes[item_id] = {
                "status": meta.get("status"),
                "supersedes": frontmatter_scalar(content, "supersedes"),
                "superseded_by": frontmatter_scalar(content, "superseded_by"),
            }
        if entry_id not in nodes:
            return {"error": "unknown_entry", "entry_id": entry_id}

        newer: list[str] = []
        cursor = entry_id
        for _ in range(depth):
            nxt = nodes.get(cursor, {}).get("superseded_by")
            if not nxt or nxt in newer or nxt == entry_id or nxt not in nodes:
                break
            newer.append(nxt)
            cursor = nxt

        older: list[str] = []
        cursor = entry_id
        for _ in range(depth):
            prev = nodes.get(cursor, {}).get("supersedes")
            if not prev or prev in older or prev == entry_id or prev not in nodes:
                break
            older.insert(0, prev)
            cursor = prev

        incoming = sorted(
            item_id for item_id, node in nodes.items()
            if item_id != entry_id
            and (node.get("supersedes") == entry_id
                 or node.get("superseded_by") == entry_id)
        )
        node = nodes[entry_id]
        return {
            "id": entry_id,
            "status": node["status"],
            "supersedes": node["supersedes"],
            "superseded_by": node["superseded_by"],
            "chain": [*older, entry_id, *newer],
            "incoming": incoming,
        }


def describe_tools(registry) -> str:
    """把注册表渲染成给模型看的工具目录（ACI：名称 + 参数 + 描述）。

    参数名后带 `*` = 必填。`AgentLoop` 每轮把它放进 prompt——注册的工具因此**可达**
    （此前 `list_tools` / `memory_get` 无人调用）。
    """
    lines: list[str] = []
    for tool in registry.list_tools():
        schema = tool.get("parameters") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        signature = ", ".join(
            f"{name}{'*' if name in required else ''}" for name in properties
        )
        lines.append(f"- {tool.get('name')}({signature})：{tool.get('description', '')}")
    return "\n".join(lines) or "- (无)"
