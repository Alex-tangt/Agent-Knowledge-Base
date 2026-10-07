"""#63 只读导航工具集的边界单测。

覆盖（照 task-5 验收锚点 1/2/3/5）：
- 工具目录来自 `list_tools()`（六个导航工具可枚举、签名可渲染）；
- 每个工具的边界：**空结果 / 行窗越界 / 条目不存在 / 过滤收窄**；
- **治理只可收窄**：`visibility` 谓词对六个工具一律生效，且工具参数无法放宽它；
- **只读**：静态（源码无写动词）+ 行为（索引的写方法一次都不被调用）；
- 与 `AgentLoop` 的 `TOOL:` 派发通路（命名工具可达、结果进证据）。

真 git 的部分只用**只读** `git log`（本仓就是 git 仓库，不写任何东西）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from memory_agent.agent_loop import AgentLoop, Budget
from memory_agent.agent_loop.tools import (
    GET_TOOL,
    GREP_TOOL,
    HISTORY_TOOL,
    LINKS_TOOL,
    LIST_TOOL,
    NAV_TOOLS,
    OUTLINE_TOOL,
    READ_TOOL,
    SEARCH_TOOL,
    MemoryNavToolRegistry,
    MemoryToolRegistry,
    describe_tools,
    entry_history,
    frontmatter_scalar,
    glob_match,
    grep_lines,
    line_window,
    scan_headings,
)
from memory_agent.trace import STOP_ANSWER

ROOT = Path(__file__).resolve().parents[2]
TOOLS_SOURCE = ROOT / "memory_agent" / "agent_loop" / "tools.py"

LIST_NAMES = [SEARCH_TOOL, GET_TOOL, LIST_TOOL, GREP_TOOL, READ_TOOL, OUTLINE_TOOL,
              HISTORY_TOOL, LINKS_TOOL]


# --------------------------------------------------------------------- 假件

class FakeIndex:
    """鸭子类型的索引替身：只实现导航工具用到的 `known_ids` / `get` / `search`。"""

    def __init__(self, entries: dict[str, dict]):
        self._entries = {key: dict(value) for key, value in entries.items()}
        self.write_touched: list[str] = []
        self.search_calls: list[tuple] = []

    def known_ids(self) -> list[str]:
        return list(self._entries)

    def get(self, entry_id):
        if entry_id not in self._entries:
            raise KeyError(entry_id)
        return dict(self._entries[entry_id])

    def search(self, query, k=5, exclude_retired=False):
        self.search_calls.append((query, k, exclude_retired))
        return []

    # 写路径哨兵：任何导航工具碰它就炸（证明只读）。
    def add(self, *args, **kwargs):
        self.write_touched.append("add")
        raise AssertionError("导航工具不得调用索引写方法")

    def delete(self, *args, **kwargs):
        self.write_touched.append("delete")
        raise AssertionError("导航工具不得调用索引删除")

    def clear(self, *args, **kwargs):
        self.write_touched.append("clear")
        raise AssertionError("导航工具不得清空索引")


def _meta(entry_id: str, content: str, **overrides) -> dict:
    meta = {
        "id": entry_id,
        "title": entry_id,
        "content": content,
        "source": entry_id,
        "path": "",
        "root": None,
        "writable": True,
        "type": "topic",
        "tags": ["alpha"],
        "status": "current",
        "owner": "owner-a",
        "classification": "private",
        "residency": "local",
        "tenant": None,
    }
    meta.update(overrides)
    return meta


DOC = (
    "---\n"
    "id: topics/doc\n"
    "title: \"Doc\"\n"
    "tags: [alpha]\n"
    "status: current\n"
    "---\n"
    "\n"
    "# Doc\n"
    "\n"
    "line three\n"
    "line four NEEDLE here\n"
    "line five\n"
    "```\n"
    "# not-a-heading\n"
    "```\n"
    "## Sub A\n"
    "\n"
    "tail line\n"
)

OTHER = (
    "---\n"
    "id: topics/other\n"
    "title: \"Other\"\n"
    "tags: [beta]\n"
    "status: draft\n"
    "---\n"
    "\n"
    "# Other\n"
    "\n"
    "other body\n"
)


def _registry(entries=None, **kwargs) -> tuple[MemoryNavToolRegistry, FakeIndex]:
    index = FakeIndex(entries or {
        "topics/doc": _meta("topics/doc", DOC, source="topics/doc", title="Doc"),
        "topics/other": _meta("topics/other", OTHER, source="topics/other", title="Other",
                              tags=["beta"], status="draft", owner="owner-b"),
    })
    return MemoryNavToolRegistry(index, **kwargs), index


# ------------------------------------------------------------------- 纯函数

class TestTextHelpers:
    def test_line_window_basic(self):
        window = line_window("a\nb\nc\nd\n", 2, 3)
        assert window["line_range"] == [2, 3]
        assert window["text"] == "b\nc"
        assert window["clamped"] is False

    def test_line_window_clamps_both_ends(self):
        window = line_window("a\nb\nc\n", 0, 99)
        assert window["line_range"] == [1, 3]
        assert window["clamped"] is True
        assert window["out_of_range"] is False

    def test_line_window_start_beyond_eof(self):
        window = line_window("a\nb\n", 10, 12)
        assert window["out_of_range"] is True
        assert window["text"] == ""
        assert window["line_range"] is None

    def test_line_window_reversed_range(self):
        window = line_window("a\nb\nc\n", 3, 1)
        assert window["out_of_range"] is True

    def test_line_window_max_lines(self):
        window = line_window("\n".join(str(i) for i in range(50)), 1, 50, max_lines=10)
        assert window["line_range"] == [1, 10]
        assert window["clamped"] is True

    def test_line_window_empty_text(self):
        window = line_window("", 1, 5)
        assert window["total_lines"] == 0
        assert window["out_of_range"] is False

    def test_scan_headings_skips_fences(self):
        headings = scan_headings(DOC)
        assert [h["text"] for h in headings] == ["Doc", "Sub A"]
        assert [h["line"] for h in headings] == [8, 16]

    def test_grep_lines_literal_and_case(self):
        assert grep_lines("Alpha\nbeta\n", "alpha") == [(1, "Alpha")]
        assert grep_lines("Alpha\nbeta\n", "alpha", ignore_case=False) == []

    def test_glob_match_double_star(self):
        assert glob_match("topics/doc", "topics/**/*") is True
        assert glob_match("docs/adr/x.md", "docs/**/*.md") is True
        assert glob_match("memory_agent/x.py", "docs/**/*.md") is False

    def test_frontmatter_scalar(self):
        assert frontmatter_scalar('---\nsupersedes: old-1\n---\n', "supersedes") == "old-1"
        assert frontmatter_scalar("no frontmatter", "supersedes") is None


# --------------------------------------------------------------- 工具目录

class TestCatalog:
    def test_catalog_shape_includes_six_nav_tools(self):
        registry, _ = _registry()
        names = [tool["name"] for tool in registry.list_tools()]
        assert names == LIST_NAMES
        assert set(NAV_TOOLS) <= set(names)

    def test_describe_tools_renders_required_markers(self):
        registry, _ = _registry()
        text = describe_tools(registry)
        assert f"{GREP_TOOL}(pattern*, glob, regex, ignore_case, context, limit)" in text
        assert f"{READ_TOOL}(entry_id*, start_line, end_line, max_lines)" in text

    def test_search_registry_shape_unchanged(self):
        registry = MemoryToolRegistry(FakeIndex({}))
        assert [tool["name"] for tool in registry.list_tools()] == [SEARCH_TOOL, GET_TOOL]

    def test_unknown_tool_raises_keyerror(self):
        registry, _ = _registry()
        with pytest.raises(KeyError):
            registry.call("nope", {})


# ------------------------------------------------------------------ 列目录

class TestList:
    def test_lists_all_and_groups_by_source(self):
        registry, _ = _registry()
        result = registry.call(LIST_TOOL, {})
        assert result["total"] == 2
        assert result["returned"] == 2
        assert {group["key"] for group in result["groups"]} == {"topics"}
        assert result["entries"][0]["id"] == "topics/doc"
        assert result["entries"][0]["lines"] > 0

    def test_filter_narrows_by_owner_tag_status(self):
        registry, _ = _registry()
        assert [e["id"] for e in registry.call(LIST_TOOL, {"owner": "owner-b"})["entries"]] \
            == ["topics/other"]
        assert [e["id"] for e in registry.call(LIST_TOOL, {"tag": "alpha"})["entries"]] \
            == ["topics/doc"]
        assert [e["id"] for e in registry.call(LIST_TOOL, {"status": "draft"})["entries"]] \
            == ["topics/other"]

    def test_glob_filter_and_empty_result(self):
        registry, _ = _registry()
        assert registry.call(LIST_TOOL, {"glob": "topics/other"})["total"] == 1
        empty = registry.call(LIST_TOOL, {"owner": "nobody"})
        assert empty["total"] == 0
        assert empty["entries"] == []
        assert empty["groups"] == []

    def test_limit_truncates(self):
        registry, _ = _registry()
        result = registry.call(LIST_TOOL, {"limit": 1})
        assert result["returned"] == 1
        assert result["truncated"] is True

    def test_bad_group_by(self):
        registry, _ = _registry()
        assert registry.call(LIST_TOOL, {"group_by": "nope"})["error"] == "bad_group_by"


# -------------------------------------------------------------------- 精搜

class TestGrep:
    def test_literal_hit_reports_line(self):
        registry, _ = _registry()
        hits = registry.call(GREP_TOOL, {"pattern": "NEEDLE"})
        assert len(hits) == 1
        assert hits[0]["id"] == "topics/doc"
        assert hits[0]["line"] == 11

    def test_context_window(self):
        registry, _ = _registry()
        hit = registry.call(GREP_TOOL, {"pattern": "line five", "context": 1})[0]
        assert hit["context_range"] == [11, 13]
        assert hit["context"].startswith("line four")

    def test_glob_narrows_and_empty_result(self):
        registry, _ = _registry()
        assert len(registry.call(GREP_TOOL, {"pattern": "body"})) == 1
        scoped = registry.call(GREP_TOOL, {"pattern": "body", "glob": "topics/doc"})
        assert scoped == []
        assert registry.call(GREP_TOOL, {"pattern": "no-such-token-anywhere"}) == []

    def test_limit_truncates(self):
        registry, _ = _registry()
        hits = registry.call(GREP_TOOL, {"pattern": "line", "limit": 1})
        assert len(hits) == 1

    def test_empty_pattern_and_bad_regex(self):
        registry, _ = _registry()
        assert registry.call(GREP_TOOL, {"pattern": ""})["error"] == "empty_pattern"
        assert registry.call(GREP_TOOL, {"pattern": "(", "regex": True})["error"] == "bad_regex"

    def test_regex_case_sensitive(self):
        registry, _ = _registry()
        assert len(registry.call(GREP_TOOL, {"pattern": r"NEEDLE\s+here",
                                             "regex": True})) == 1
        assert registry.call(GREP_TOOL, {"pattern": "needle", "regex": True,
                                         "ignore_case": False}) == []


# -------------------------------------------------------------------- 读回

class TestRead:
    def test_window_reads_exact_lines(self):
        registry, _ = _registry()
        result = registry.call(READ_TOOL, {"entry_id": "topics/doc", "start_line": 10,
                                           "end_line": 12})
        assert result["line_range"] == [10, 12]
        assert result["text"].splitlines() == ["line three", "line four NEEDLE here",
                                               "line five"]
        assert result["total_lines"] == 18

    def test_out_of_range_start(self):
        registry, _ = _registry()
        result = registry.call(READ_TOOL, {"entry_id": "topics/doc", "start_line": 999})
        assert result["out_of_range"] is True
        assert result["text"] == ""

    def test_end_clamped_to_eof(self):
        registry, _ = _registry()
        result = registry.call(READ_TOOL, {"entry_id": "topics/doc", "start_line": 16,
                                           "end_line": 999})
        assert result["line_range"] == [16, 18]
        assert result["clamped"] is True

    def test_max_lines_clamped(self):
        registry, _ = _registry(max_read_lines=2)
        result = registry.call(READ_TOOL, {"entry_id": "topics/doc", "start_line": 1,
                                           "end_line": 18, "max_lines": 999})
        assert result["line_range"] == [1, 2]

    def test_missing_entry(self):
        registry, _ = _registry()
        assert registry.call(READ_TOOL, {"entry_id": "nope"})["error"] == "unknown_entry"
        assert registry.call(READ_TOOL, {})["error"] == "unknown_entry"


# -------------------------------------------------------------------- 大纲

class TestOutline:
    def test_entry_outline_skips_fences(self):
        registry, _ = _registry()
        result = registry.call(OUTLINE_TOOL, {"entry_id": "topics/doc"})
        assert [(h["level"], h["text"], h["line"]) for h in result["headings"]] \
            == [(1, "Doc", 8), (2, "Sub A", 16)]

    def test_missing_entry(self):
        registry, _ = _registry()
        assert registry.call(OUTLINE_TOOL, {"entry_id": "nope"})["error"] == "unknown_entry"

    def test_global_outline(self):
        registry, _ = _registry()
        result = registry.call(OUTLINE_TOOL, {})
        assert result["with_outline"] == 2
        assert {item["id"] for item in result["entries"]} == {"topics/doc", "topics/other"}

    def test_global_outline_limit(self):
        registry, _ = _registry()
        result = registry.call(OUTLINE_TOOL, {"limit": 1})
        assert result["returned"] == 1
        assert result["truncated"] is True


# -------------------------------------------------------------------- 历史

class TestHistory:
    def _entry(self):
        return {
            "topics/doc": _meta("topics/doc", DOC, source="topics/doc",
                                path=str(ROOT / "AGENTS.md"), root=str(ROOT)),
        }

    def test_argv_is_read_only_and_well_formed(self):
        seen: list[list[str]] = []

        def runner(args):
            seen.append(list(args))
            return {"returncode": 0, "stdout": "abc123\t2026-10-01\tsubject\n", "stderr": ""}

        registry = MemoryNavToolRegistry(FakeIndex(self._entry()), git_runner=runner)
        result = registry.call(HISTORY_TOOL, {"entry_id": "topics/doc", "op": "log",
                                              "pattern": "D7.7", "added_only": True,
                                              "limit": 5})
        assert result["count"] == 1
        assert result["commits"][0]["sha"] == "abc123"
        args = seen[0]
        assert args[0] == "-C"
        assert args[1] == str(ROOT)
        assert args[2] == "log"
        assert "--diff-filter=A" in args
        assert args[args.index("-S") + 1] == "D7.7"
        assert args[-2:] == ["--", "AGENTS.md"]
        assert "commit" not in args and "checkout" not in args

    def test_unsupported_op_is_rejected_without_git(self):
        def runner(args):  # pragma: no cover - 不应被调用
            raise AssertionError("非法 op 不得触 git")

        registry = MemoryNavToolRegistry(FakeIndex(self._entry()), git_runner=runner)
        assert registry.call(HISTORY_TOOL, {"entry_id": "topics/doc",
                                            "op": "commit"})["error"] == "unsupported_op"

    def test_missing_entry_and_non_repo(self, tmp_path):
        registry, _ = _registry()
        assert registry.call(HISTORY_TOOL, {"entry_id": "nope"})["error"] == "unknown_entry"
        assert registry.call(HISTORY_TOOL, {"entry_id": "topics/doc"})["error"] == "missing_file"
        loose = tmp_path / "loose.md"
        loose.write_text(DOC, encoding="utf-8")
        only_files = {"topics/doc": _meta("topics/doc", DOC, path=str(loose),
                                          root=str(tmp_path))}
        assert MemoryNavToolRegistry(FakeIndex(only_files)).call(
            HISTORY_TOOL, {"entry_id": "topics/doc"})["error"] == "not_a_git_repo"

    def test_git_failure_reported(self):
        def runner(args):
            return {"returncode": 128, "stdout": "", "stderr": "fatal: bad object"}

        registry = MemoryNavToolRegistry(FakeIndex(self._entry()), git_runner=runner)
        result = registry.call(HISTORY_TOOL, {"entry_id": "topics/doc", "op": "diff"})
        assert result["error"] == "git_failed"
        assert "bad object" in result["detail"]

    def test_real_git_log_is_read_only(self):
        """真实 `git log`（只读）：本仓 AGENTS.md 一定有历史。"""
        result = entry_history(str(ROOT), "AGENTS.md", op="log", limit=3)
        assert result["op"] == "log"
        assert result["count"] >= 1
        assert len(result["commits"][0]["sha"]) == 40

    def test_real_git_added_filter(self):
        result = entry_history(str(ROOT), "memory_agent/trace.py", op="log",
                               added_only=True, limit=5)
        assert result["count"] >= 1


# ------------------------------------------------------------------ 链接

class TestLinks:
    def _chain(self):
        return {
            "kb/old": _meta("kb/old", "---\nstatus: superseded\nsuperseded_by: kb/new\n---\n",
                            status="superseded"),
            "kb/new": _meta("kb/new", "---\nstatus: current\nsupersedes: kb/old\n---\n",
                            status="current"),
            "kb/unrelated": _meta("kb/unrelated", "---\nstatus: current\n---\n"),
        }

    def test_chain_is_ordered_old_to_new(self):
        registry = MemoryNavToolRegistry(FakeIndex(self._chain()))
        result = registry.call(LINKS_TOOL, {"entry_id": "kb/old"})
        assert result["status"] == "superseded"
        assert result["superseded_by"] == "kb/new"
        assert result["chain"] == ["kb/old", "kb/new"]
        assert result["incoming"] == ["kb/new"]

    def test_no_links(self):
        registry = MemoryNavToolRegistry(FakeIndex(self._chain()))
        result = registry.call(LINKS_TOOL, {"entry_id": "kb/unrelated"})
        assert result["chain"] == ["kb/unrelated"]
        assert result["incoming"] == []
        assert result["supersedes"] is None

    def test_missing_entry(self):
        registry = MemoryNavToolRegistry(FakeIndex(self._chain()))
        assert registry.call(LINKS_TOOL, {"entry_id": "nope"})["error"] == "unknown_entry"
        assert registry.call(LINKS_TOOL, {})["error"] == "missing_entry_id"


# ------------------------------------------------------- 治理：只可收窄

class TestGovernance:
    def _entries(self):
        return {
            "org-a/doc": _meta("org-a/doc", DOC, source="org-a/doc", tenant="org-a"),
            "org-b/secret": _meta("org-b/secret", OTHER, source="org-b/secret", tenant="org-b"),
        }

    def _registry(self):
        return MemoryNavToolRegistry(
            FakeIndex(self._entries()),
            visibility=lambda meta: meta.get("tenant") == "org-a",
        )

    def test_list_and_grep_only_see_allowed_tenant(self):
        registry = self._registry()
        listed = registry.call(LIST_TOOL, {})
        assert listed["total"] == 1
        assert listed["entries"][0]["id"] == "org-a/doc"
        hits = registry.call(GREP_TOOL, {"pattern": "other body"})
        assert hits == []

    def test_read_outline_history_links_hide_other_tenant(self):
        registry = self._registry()
        assert registry.call(READ_TOOL, {"entry_id": "org-b/secret"})["error"] == "unknown_entry"
        assert registry.call(OUTLINE_TOOL, {"entry_id": "org-b/secret"})["error"] == "unknown_entry"
        assert registry.call(HISTORY_TOOL, {"entry_id": "org-b/secret"})["error"] == "unknown_entry"
        assert registry.call(LINKS_TOOL, {"entry_id": "org-b/secret"})["error"] == "unknown_entry"

    def test_tool_args_cannot_widen_visibility(self):
        registry = self._registry()
        # 参数里塞 tenant / classification / residency 不产生任何影响（它们不是工具参数）。
        result = registry.call(LIST_TOOL, {"tenant": "org-b", "classification": "public",
                                           "residency": "cloud"})
        assert [e["id"] for e in result["entries"]] == ["org-a/doc"]

    def test_visibility_exception_is_conservative(self):
        registry = MemoryNavToolRegistry(
            FakeIndex(self._entries()),
            visibility=lambda meta: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert registry.call(LIST_TOOL, {})["total"] == 0
        assert registry.call(READ_TOOL, {"entry_id": "org-a/doc"})["error"] == "unknown_entry"

    def test_no_visibility_means_no_restriction(self):
        registry = MemoryNavToolRegistry(FakeIndex(self._entries()))
        assert registry.call(LIST_TOOL, {})["total"] == 2

    def test_exclude_retired_filters_superseded(self):
        entries = self._entries()
        entries["org-a/old"] = _meta("org-a/old", OTHER, source="org-a/old",
                                     tenant="org-a", status="superseded")
        registry = MemoryNavToolRegistry(
            FakeIndex(entries), exclude_retired=True,
            visibility=lambda meta: meta.get("tenant") == "org-a")
        ids = [e["id"] for e in registry.call(LIST_TOOL, {})["entries"]]
        assert "org-a/old" not in ids


# ------------------------------------------------------------- 只读证明

class TestReadOnly:
    FORBIDDEN = (
        r"\bopen\s*\(", r"\.write\s*\(", r"\bos\.remove\b", r"\bos\.unlink\b",
        r"\bshutil\b", r"['\"](commit|checkout|reset|push|clean)['\"]",
    )

    def test_source_has_no_write_paths(self):
        source = TOOLS_SOURCE.read_text(encoding="utf-8")
        offenders = [pattern for pattern in self.FORBIDDEN if re.search(pattern, source)]
        assert not offenders, f"导航工具源码出现写路径：{offenders}"

    def test_git_ops_are_read_only_whitelist(self):
        from memory_agent.agent_loop.tools import GIT_OPS
        assert GIT_OPS == frozenset({"log", "diff", "blame"})

    def test_calling_every_nav_tool_never_touches_index_writes(self):
        registry, index = _registry()
        for name in NAV_TOOLS:
            registry.call(name, {"entry_id": "topics/doc", "pattern": "line"})
        assert index.write_touched == []


# ----------------------------------------------------- 与 AgentLoop 的派发

class _ScriptedLLM:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def complete(self, messages, **kwargs):
        return self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]


class TestLoopDispatch:
    def test_named_nav_tool_is_dispatched_and_becomes_evidence(self):
        registry, index = _registry()
        llm = _ScriptedLLM([
            f'TOOL: {GREP_TOOL} {{"pattern": "NEEDLE"}}',
            "ANSWER: found",
        ])
        trace = AgentLoop(llm, registry, budget=Budget(max_rounds=3)).run("q", trace_id="t1")
        assert trace.stop.trigger == STOP_ANSWER
        tools_called = [call.tool for rnd in trace.rounds for call in rnd.tool_calls]
        assert GREP_TOOL in tools_called
        assert "topics/doc" in trace.final["evidence_ids"]
        # 检索面仍走协议（首轮 memory_search 被调用）
        assert index.search_calls

    def test_read_tool_result_enters_evidence(self):
        registry, _ = _registry()
        llm = _ScriptedLLM([
            f'TOOL: {READ_TOOL} {{"entry_id": "topics/doc", "start_line": 10, "end_line": 12}}',
            "ANSWER: ok",
        ])
        trace = AgentLoop(llm, registry).run("q", trace_id="t2")
        assert trace.final["evidence_ids"] == ["topics/doc"]

    def test_unknown_entry_does_not_crash_the_loop(self):
        registry, _ = _registry()
        llm = _ScriptedLLM([
            f'TOOL: {READ_TOOL} {{"entry_id": "nope"}}',
            "ANSWER: done",
        ])
        trace = AgentLoop(llm, registry).run("q", trace_id="t3")
        assert trace.stop.trigger == STOP_ANSWER
        assert trace.rounds[0].tool_calls
