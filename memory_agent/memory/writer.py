"""写入网关（#11）+ 生命周期工具（#12）：memory_add / supersede / archive。

契约（PRD #7）：
- 调用方给结构化字段，路径由本模块决定——**不提供裸文件写工具**。
- 不覆盖、不删除、不原地编辑任何已有记忆的文件。
- 变更语义（supersede / archive）先返回预览，`confirm=True` 才落盘；一次操作一个
  commit，且只包含本次触及的文件（#11 的路径级提交约定，见 ADR-0009）。
- supersede 新建条目并双向标注，旧条目置 `superseded`；archive 只置 `archived`，
  两者都不删文件。

写入成功后**增量刷新派生索引**（#13）：只重嵌受影响条目、hash 未变跳过、删除/改名
无残留；刷新失败不回滚已提交的文件（真相源已落盘，派生索引下次刷新即可追平），但会
在返回值 `index` 字段里如实报告 `ok:false`。
"""
from __future__ import annotations

import os
import subprocess
import sys

from memory_agent.memory import authoring
from memory_agent.memory.entries import parse_frontmatter
from memory_agent.memory.locks import WRITE_LOCK, locked
from memory_agent.settings import DEDUP_THRESHOLD, KB_DIR

SUPERSEDABLE_STATUS = frozenset({"current", "draft"})


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

    # ------------------------------------------------------------------ add

    @locked(WRITE_LOCK)
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
        self._require_git_repo()
        today = today or authoring.today_str()
        prepared = self._prepare_new_entry(
            title=title, body=body, domain=domain, type=type, tags=tags, slug=slug,
            sources=sources, status=status, today=today,
        )
        rel_path, entry_id, abs_path = (
            prepared["rel_path"], prepared["id"], prepared["abs_path"]
        )

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

        self._write_all([(rel_path, prepared["text"])], rollback={rel_path: None})
        commit = self._git_commit([rel_path], f"kb: 新增 {entry_id}")
        return {
            "status": "written",
            "written": True,
            "action": "add",
            "id": entry_id,
            "path": abs_path,
            "commit": commit,
            "warnings": prepared["warnings"],
            "index": self._refresh_index(),
        }

    # ------------------------------------------------------------- supersede

    @locked(WRITE_LOCK)
    def supersede(
        self,
        *,
        old_id: str,
        title: str,
        body: str,
        domain: str,
        type: str,
        tags: list[str],
        slug: str | None = None,
        sources: list[str] | None = None,
        confirm: bool = False,
        today: str | None = None,
    ) -> dict:
        """用新条目替代旧条目：双向标注（old.superseded_by / new.supersedes）。

        `confirm=False`（默认）只做校验并返回预览，不落盘；`confirm=True` 才写，
        且新旧两个文件在**同一个 commit** 里。
        """
        self._require_git_repo()
        today = today or authoring.today_str()
        prepared = self._prepare_new_entry(
            title=title, body=body, domain=domain, type=type, tags=tags, slug=slug,
            sources=sources, status="current", today=today,
            extra={"supersedes": old_id},
        )
        old = self._read_writable_entry(old_id)

        if old["status"] not in SUPERSEDABLE_STATUS:
            raise MemoryWriteError(
                f"旧条目 {old_id} 状态为 {old['status']!r}："
                f"只有 {sorted(SUPERSEDABLE_STATUS)} 才能被替代"
            )
        if os.path.exists(prepared["abs_path"]):
            raise MemoryWriteError(
                f"目标条目已存在：{prepared['id']}（换一个 slug，或改用 memory_add）"
            )

        old_changes = {
            "status": "superseded",
            "superseded_by": prepared["id"],
            "updated": today,
        }
        old_text = authoring.update_frontmatter_fields(old["content"], old_changes)
        errors, _ = authoring.validate_entry(old_text, old["rel_path"])
        if errors:
            raise MemoryWriteError("旧条目标注后校验失败：\n  " + "\n  ".join(errors))

        preview = {
            "old": {"id": old_id, "title": old["title"], "status": old["status"],
                    "path": old["rel_path"]},
            "new": {"id": prepared["id"], "title": prepared["title"],
                    "domain": prepared["domain"], "type": type,
                    "tags": prepared["tags"], "path": prepared["rel_path"]},
            "changes": {"old": old_changes, "new": {"supersedes": old_id}},
            "commit_scope": [prepared["rel_path"], old["rel_path"]],
        }
        if not confirm:
            return {
                "status": "confirmation_required",
                "written": False,
                "action": "supersede",
                "preview": preview,
                "hint": "这是破坏性变更（旧条目将被标注退役）：确认后以 confirm=true 重试；"
                        "不确认则什么都不写。",
            }

        self._write_all(
            [(prepared["rel_path"], prepared["text"]), (old["rel_path"], old_text)],
            rollback={prepared["rel_path"]: None, old["rel_path"]: old["content"]},
        )
        commit = self._git_commit(
            [prepared["rel_path"], old["rel_path"]],
            f"kb: 替代 {old_id} -> {prepared['id']}",
        )
        return {
            "status": "written",
            "written": True,
            "action": "supersede",
            "old_id": old_id,
            "new_id": prepared["id"],
            "id": prepared["id"],
            "path": prepared["abs_path"],
            "commit": commit,
            "preview": preview,
            "warnings": prepared["warnings"],
            "index": self._refresh_index(),
        }

    # --------------------------------------------------------------- archive

    @locked(WRITE_LOCK)
    def archive(
        self,
        *,
        entry_id: str,
        reason: str,
        confirm: bool = False,
        today: str | None = None,
    ) -> dict:
        """把条目标记为退役（`status: archived` + `archive_reason`），文件保留。

        `confirm=False`（默认）只返回预览；`confirm=True` 才落盘。
        """
        self._require_git_repo()
        reason = (reason or "").strip()
        if not reason:
            raise MemoryWriteError("reason 不能为空：归档必须留下为什么")
        today = today or authoring.today_str()
        entry = self._read_writable_entry(entry_id)

        if entry["status"] == "archived":
            raise MemoryWriteError(f"条目 {entry_id} 已经归档过了")

        changes = {"status": "archived", "archive_reason": reason, "updated": today}
        text = authoring.update_frontmatter_fields(entry["content"], changes)
        errors, _ = authoring.validate_entry(text, entry["rel_path"])
        if errors:
            raise MemoryWriteError("归档标注后校验失败：\n  " + "\n  ".join(errors))

        preview = {
            "entry": {"id": entry_id, "title": entry["title"],
                      "status": entry["status"], "path": entry["rel_path"]},
            "changes": changes,
            "commit_scope": [entry["rel_path"]],
        }
        if not confirm:
            return {
                "status": "confirmation_required",
                "written": False,
                "action": "archive",
                "preview": preview,
                "hint": "这是破坏性变更（条目将退役，文件保留）：确认后以 confirm=true 重试；"
                        "不确认则什么都不写。",
            }

        self._write_all(
            [(entry["rel_path"], text)],
            rollback={entry["rel_path"]: entry["content"]},
        )
        commit = self._git_commit([entry["rel_path"]], f"kb: 归档 {entry_id}")
        return {
            "status": "written",
            "written": True,
            "action": "archive",
            "id": entry_id,
            "path": entry["abs_path"],
            "commit": commit,
            "preview": preview,
            "index": self._refresh_index(),
        }

    # ------------------------------------------------------------- internals

    def _prepare_new_entry(
        self, *, title, body, domain, type, tags, slug, sources, status, today, extra=None,
    ) -> dict:
        """校验结构化字段并渲染条目文本；返回路径/文本/警告，不落盘。"""
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
        text = authoring.render_entry(
            entry_id=entry_id, title=title, type=type, tags=tags, status=status,
            updated=today, sources=sources or [], body=body, extra=extra,
        )
        errors, warnings = authoring.validate_entry(
            text, rel_path, self._tag_vocab_for_check()
        )
        if errors:
            raise MemoryWriteError("frontmatter 校验失败：\n  " + "\n  ".join(errors))
        return {
            "rel_path": rel_path,
            "id": entry_id,
            "abs_path": os.path.join(self._kb_dir, rel_path),
            "title": title,
            "domain": domain,
            "tags": tags,
            "text": text,
            "warnings": warnings,
        }

    def _read_writable_entry(self, entry_id: str) -> dict:
        """读回可写条目：校验它是 KB 内的可写文件，返回内容与元数据。"""
        try:
            meta = self._index.get(entry_id)
        except KeyError as exc:
            raise MemoryWriteError(
                f"未知条目 id：{entry_id}（先用 memory_search 取 id）"
            ) from exc
        except FileNotFoundError as exc:
            raise MemoryWriteError(f"条目文件已不存在（索引孤儿，需重建）：{exc}") from exc

        if not meta.get("writable"):
            raise MemoryWriteError(f"{entry_id} 是只读参考语料，不可写入")

        abs_path = meta["path"]
        rel_path = os.path.relpath(abs_path, self._kb_dir).replace("\\", "/")
        if rel_path.startswith(".."):
            raise MemoryWriteError(f"条目不在可写 KB 内：{entry_id}")
        if not os.path.isfile(abs_path):
            raise MemoryWriteError(f"条目文件不存在：{abs_path}")

        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        frontmatter, _ = parse_frontmatter(content)
        if not frontmatter:
            raise MemoryWriteError(
                f"{entry_id} 缺少 frontmatter：生命周期工具不给手写裸文件加标注"
            )
        return {
            "abs_path": abs_path,
            "rel_path": rel_path,
            "content": content,
            "title": meta.get("title") or frontmatter.get("title") or entry_id,
            "status": meta.get("status") or frontmatter.get("status"),
        }

    def _require_git_repo(self) -> None:
        if not os.path.isdir(os.path.join(self._kb_dir, ".git")):
            raise MemoryWriteError(
                f"{self._kb_dir} 不是 git 仓库：KB 必须可回滚（一次写入一个 commit）"
            )

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

    def _refresh_index(self) -> dict:
        """写入后增量刷新派生索引（#13）。索引刷新失败不使写入失败。"""
        refresh = getattr(self._index, "refresh", None)
        if refresh is None:
            return {"ok": True, "skipped": True, "reason": "index 不支持 refresh"}
        try:
            return {"ok": True, **refresh()}
        except Exception as exc:  # noqa: BLE001 - 派生索引失败不该吞掉已提交的写入
            return {"ok": False, "error": str(exc)}

    def _write_all(self, writes: list[tuple[str, str]], rollback: dict) -> None:
        """写文件并过 KB 外部闸门；失败则回滚（restore 原文或删除新文件）。"""
        for rel_path, text in writes:
            abs_path = os.path.join(self._kb_dir, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as handle:
                handle.write(text)
        try:
            self._run_kb_check([rel_path for rel_path, _ in writes])
        except MemoryWriteError:
            for rel_path, original in rollback.items():
                abs_path = os.path.join(self._kb_dir, rel_path)
                if original is None:
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                else:
                    with open(abs_path, "w", encoding="utf-8") as handle:
                        handle.write(original)
            raise

    def _run_kb_check(self, rel_paths: list[str]) -> None:
        """过 KB 自己的 check（KB 里若有 tools/kb.py）；只对本次触及文件的 ERROR 负责。"""
        kb_check = os.path.join(self._kb_dir, "tools", "kb.py")
        if not os.path.isfile(kb_check):
            return
        proc = subprocess.run(
            [sys.executable, kb_check, "check"],
            cwd=self._kb_dir, capture_output=True, text=True, encoding="utf-8",
        )
        related = [
            line for line in (proc.stdout or "").splitlines()
            if line.startswith("ERROR") and any(rel in line for rel in rel_paths)
        ]
        if related:
            raise MemoryWriteError("KB check 未通过：\n  " + "\n  ".join(related))

    def _git_commit(self, rel_paths: list[str], message: str) -> str:
        for rel_path in rel_paths:
            self._git("add", "--", rel_path)
        self._git("commit", "-m", message, "--", *rel_paths)
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
