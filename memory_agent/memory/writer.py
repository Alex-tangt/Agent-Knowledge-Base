"""写入网关（issue #11）：memory_add 的 搜索 → 去重 → 渲染 → 校验 → 落盘 → 提交。

契约（PRD #7）：
- 调用方给结构化字段，路径由本模块决定——**不提供裸文件写工具**。
- 不覆盖、不删除、不原地编辑任何已有记忆。
- 命中近似重复时**只报告、不写**，由调用方改用生命周期工具（#12）处理。

已知边界：写入后不刷新派生索引（增量 reindex 属 #13）；因此在重建前，新条目
不会被后续 memory_search / 去重检索看到。
"""
from __future__ import annotations

import os
import subprocess
import sys

from memory_agent.memory import authoring
from memory_agent.settings import DEDUP_THRESHOLD, KB_DIR


class MemoryWriteError(RuntimeError):
    """写入被拒或失败；消息面向调用方，含修正方向。"""


class MemoryWriter:
    def __init__(self, index, kb_dir: str | None = None, tag_vocab=None):
        self._index = index
        self._kb_dir = os.path.abspath(kb_dir or KB_DIR)
        self._tag_vocab = tag_vocab
        self._vocab_loaded = tag_vocab is not None

    @property
    def kb_dir(self) -> str:
        return self._kb_dir

    def add(
        self,
        *,
        title: str,
        body: str,
        domain: str,
        type: str,
        tags: list[str],
        slug: str | None = None,
        sources: list[str] | None = None,
        status: str = "current",
        allow_duplicate: bool = False,
        dedup_threshold: float = DEDUP_THRESHOLD,
        today: str | None = None,
    ) -> dict:
        """写入一条新记忆。返回 {"status": "written"|"duplicate", ...}。"""
        if not os.path.isdir(os.path.join(self._kb_dir, ".git")):
            raise MemoryWriteError(
                f"{self._kb_dir} 不是 git 仓库：KB 必须可回滚（一次写入一个 commit）"
            )

        title = (title or "").strip()
        body = (body or "").strip()
        if not title or "\n" in title:
            raise MemoryWriteError("title 必须是非空单行文本")
        if not body:
            raise MemoryWriteError("body 不能为空")
        tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if not tags:
            raise MemoryWriteError("tags 不能为空（从 tags.md 的受控表里取）")

        try:
            domain = authoring.normalize_domain(domain)
        except ValueError as exc:
            raise MemoryWriteError(str(exc)) from exc
        if type not in authoring.VALID_TYPES:
            raise MemoryWriteError(
                f"type {type!r} 不合法：应为 {sorted(authoring.VALID_TYPES)}"
            )
        allowed = authoring.allowed_types(domain)
        if type not in allowed:
            raise MemoryWriteError(
                f"{domain} 下 type 必须是 {sorted(allowed)}，收到 {type!r}"
            )
        if status not in authoring.VALID_STATUS:
            raise MemoryWriteError(f"status {status!r} 不合法")

        resolved_slug = (slug or authoring.slugify(title)).strip()
        if not authoring.SLUG_RE.match(resolved_slug):
            raise MemoryWriteError(
                f"slug {resolved_slug!r} 不合法：标题无法生成英文 slug 时请显式传 slug"
            )
        rel_path = f"{domain}/{resolved_slug}.md"
        entry_id = rel_path[:-3]
        abs_path = os.path.join(self._kb_dir, rel_path)

        if os.path.exists(abs_path):
            return {
                "status": "duplicate",
                "written": False,
                "reason": "exists",
                "id": entry_id,
                "path": abs_path,
                "candidates": [
                    {"id": entry_id, "title": title, "score": None,
                     "reason": "同名文件已存在，写入网关从不覆盖"}
                ],
                "hint": "已存在同路径条目：如需替换，改用 memory_supersede（#12）",
            }

        text = authoring.render_entry(
            entry_id=entry_id, title=title, type=type, tags=tags, status=status,
            updated=today or authoring.today_str(), sources=sources or [], body=body,
        )
        errors, warnings = authoring.validate_entry(
            text, rel_path, self._tag_vocab_for_check()
        )
        if errors:
            raise MemoryWriteError("frontmatter 校验失败：\n  " + "\n  ".join(errors))

        if not allow_duplicate:
            duplicates = self._find_duplicates(title, body, dedup_threshold)
            if duplicates:
                return {
                    "status": "duplicate",
                    "written": False,
                    "reason": "semantic",
                    "id": entry_id,
                    "candidates": duplicates,
                    "hint": "已有近似条目：改用 memory_supersede（#12），"
                            "或确认确实不同后以 allow_duplicate=true 重试",
                }

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            self._run_kb_check(rel_path)
        except MemoryWriteError:
            os.remove(abs_path)
            raise

        commit = self._git_commit(rel_path, f"kb: 新增 {entry_id}")
        return {
            "status": "written",
            "written": True,
            "id": entry_id,
            "path": abs_path,
            "commit": commit,
            "warnings": warnings,
        }

    def _tag_vocab_for_check(self) -> set[str] | None:
        if not self._vocab_loaded:
            self._tag_vocab = authoring.load_tag_vocab(self._kb_dir)
            self._vocab_loaded = True
        return self._tag_vocab

    def _find_duplicates(self, title: str, body: str, threshold: float) -> list[dict]:
        hits = self._index.search(f"{title}\n\n{body}", k=5, writable_only=True)
        keys = ("id", "title", "source", "status", "score")
        return [
            {key: hit[key] for key in keys}
            for hit in hits
            if hit["score"] >= threshold
        ]

    def _run_kb_check(self, rel_path: str) -> None:
        """过 KB 自己的 check（KB 里若有 tools/kb.py）；只对本文件相关的 ERROR 负责。"""
        kb_check = os.path.join(self._kb_dir, "tools", "kb.py")
        if not os.path.isfile(kb_check):
            return
        proc = subprocess.run(
            [sys.executable, kb_check, "check"],
            cwd=self._kb_dir, capture_output=True, text=True, encoding="utf-8",
        )
        related = [
            line for line in (proc.stdout or "").splitlines()
            if line.startswith("ERROR") and rel_path in line
        ]
        if related:
            raise MemoryWriteError("KB check 未通过：\n  " + "\n  ".join(related))

    def _git_commit(self, rel_path: str, message: str) -> str:
        self._git("add", "--", rel_path)
        self._git("commit", "-m", message, "--", rel_path)
        return self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", self._kb_dir, *args],
                capture_output=True, text=True, encoding="utf-8",
            )
        except FileNotFoundError as exc:
            raise MemoryWriteError("找不到 git 可执行文件") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise MemoryWriteError(f"git {' '.join(args)} 失败：{detail}")
        return proc.stdout or ""
