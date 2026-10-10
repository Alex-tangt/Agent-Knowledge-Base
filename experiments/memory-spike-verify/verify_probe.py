# -*- coding: utf-8 -*-
"""观测原语：消费者调用（官方 SDK）、磁盘/git 字节核对、源目录快照。

本模块只做"观测"，不含任何判定——判定在 verify_criteria.py，汇总在 verify_checks.py。
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import verify_criteria as C
import verify_env as E


# ---------------------------------------------------------------------------
# 消费者路径（官方 SDK = 公开入口）
# ---------------------------------------------------------------------------

class ConsumerError(Exception):
    pass


class Consumer:
    """一个授权消费者：只用官方 SDK 的公开方法。"""

    def __init__(self, name: str, url: str = E.BASE_URL, user: str = "default"):
        self.name = name
        self.url = url
        self.user = user
        self._client = None

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self) -> "Consumer":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def open(self) -> None:
        from openviking_sdk import SyncHTTPClient  # 延迟 import：静态阶段不需要

        self._client = SyncHTTPClient(
            url=self.url, user=self.user, account="default", timeout=C.CALL_TIMEOUT_S
        )
        self._client.initialize()

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    # -- 调用 -------------------------------------------------------------
    def health_ok(self) -> bool:
        import httpx

        r = httpx.get(f"{E.BASE_URL}/health", timeout=10, trust_env=False)
        return r.status_code == 200

    def write(self, uri: str, content: str, mode: str = "replace", wait: bool = True) -> Dict[str, Any]:
        return self._client.write(uri, content, mode=mode, wait=wait, timeout=C.CALL_TIMEOUT_S)

    def read_raw(self, uri: str) -> str:
        return self._client.read_raw(uri)

    def read_default(self, uri: str) -> str:
        return self._client.read(uri)

    def ls_simple(self, uri: str) -> List[Any]:
        return self._client.ls(uri, simple=True, show_all_hidden=True)

    def stat(self, uri: str) -> Dict[str, Any]:
        return self._client.stat(uri)

    def find(self, query: str, target_uri: str = "", limit: int = 10) -> Dict[str, Any]:
        return self._client.find(query, target_uri=target_uri, limit=limit)

    def add_resource(
        self, path: str, to: Optional[str] = None, parent: Optional[str] = None, wait: bool = True
    ) -> Dict[str, Any]:
        return self._client.add_resource(path, to=to, parent=parent, wait=wait,
                                         timeout=C.CALL_TIMEOUT_S)


def http_post(path: str, payload: Dict[str, Any], timeout: int = 60) -> Tuple[int, str]:
    """裸 HTTP POST（用于探测治理边界，如本地路径是否被拒）。"""
    import httpx

    r = httpx.post(f"{E.BASE_URL}{path}", json=payload, timeout=timeout, trust_env=False)
    return r.status_code, r.text[:4000]


# ---------------------------------------------------------------------------
# 磁盘字节核对
# ---------------------------------------------------------------------------

_SKIP_DIR_NAMES = {".git"}


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for fn in filenames:
            yield Path(dirpath) / fn


def scan_for_bytes(root: Path, marker: bytes, full: bytes) -> Dict[str, Any]:
    """在 root 下扫描普通文件，报告 marker / 完整原文 的出现位置。"""
    hits_marker: List[Dict[str, Any]] = []
    hits_full: List[Dict[str, Any]] = []
    scanned = 0
    skipped_big = 0
    for p in _iter_files(root):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > C.DISK_SCAN_MAX_FILE_BYTES:
            skipped_big += 1
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        scanned += 1
        if marker in data:
            off = data.index(marker)
            hits_marker.append(
                {
                    "path": str(p),
                    "size": size,
                    "file_sha256": E.sha256_bytes(data),
                    "marker_offset": off,
                    "window_utf8": data[max(0, off - 120): off + len(marker) + 120]
                    .decode("utf-8", errors="replace"),
                }
            )
        if full in data:
            hits_full.append(
                {"path": str(p), "size": size, "file_sha256": E.sha256_bytes(data),
                 "full_offset": data.index(full)}
            )
    return {
        "root": str(root),
        "files_scanned": scanned,
        "files_skipped_over_limit": skipped_big,
        "marker_hits": hits_marker,
        "full_content_hits": hits_full,
    }


def scan_git_blobs(root: Path, needle: bytes, expected: bytes, limit: int = C.GIT_BLOB_SCAN_LIMIT) -> Dict[str, Any]:
    """对 workspace 下的 git 仓库做对象级核对（磁盘真实字节，绕开工作区副本）。"""
    repos: List[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            if d == ".git":
                repos.append(Path(dirpath))
                dirnames.remove(d)
    result: Dict[str, Any] = {"repos": [str(r) for r in repos], "blobs": [], "truncated": False,
                              "examined": 0}
    examined = 0
    for repo in repos:
        try:
            listing = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "--batch-all-objects",
                 "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                capture_output=True, text=True, timeout=120, errors="replace",
            ).stdout
            names: Dict[str, str] = {}
            rl = subprocess.run(
                ["git", "-C", str(repo), "rev-list", "--objects", "--all"],
                capture_output=True, text=True, timeout=120, errors="replace",
            ).stdout
            for line in rl.splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    names[parts[0]] = parts[1]
            for line in listing.splitlines():
                parts = line.split()
                if len(parts) != 3 or parts[1] != "blob":
                    continue
                sha, _, size_s = parts
                try:
                    size = int(size_s)
                except ValueError:
                    continue
                if size < len(needle) or size > len(expected) + 8192 or size > C.DISK_SCAN_MAX_FILE_BYTES:
                    continue
                if examined >= limit:
                    result["truncated"] = True
                    break
                examined += 1
                blob = subprocess.run(
                    ["git", "-C", str(repo), "cat-file", "blob", sha],
                    capture_output=True, timeout=60,
                ).stdout
                if needle in blob or blob == expected:
                    result["blobs"].append(
                        {
                            "repo": str(repo),
                            "blob": sha,
                            "path": names.get(sha, ""),
                            "size": size,
                            "exact_equal_to_input": blob == expected,
                            "blob_sha256": E.sha256_bytes(blob),
                        }
                    )
        except Exception as exc:  # 观测失败照实记录，不遮掩
            result.setdefault("errors", []).append(f"{repo}: {type(exc).__name__}: {exc}")
    result["examined"] = examined
    return result


# ---------------------------------------------------------------------------
# 源目录快照（文件清单 + 逐文件 sha256 + git 提交号）
# ---------------------------------------------------------------------------

def source_snapshot(root: Path) -> Dict[str, Any]:
    files: Dict[str, str] = {}
    for p in sorted(_iter_files(root)):
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            files[rel] = E.sha256_bytes(p.read_bytes())
        except OSError as exc:
            files[rel] = f"<unreadable: {exc}>"

    def _git(*args: str) -> Optional[str]:
        try:
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                               timeout=60, errors="replace")
            if r.returncode != 0:
                return f"<rc={r.returncode}: {r.stderr.strip()[:200]}>"
            return r.stdout.strip()
        except Exception as exc:
            return f"<error: {exc}>"

    return {
        "root": str(root),
        "file_count": len(files),
        "files": files,
        "git_head": _git("rev-parse", "HEAD"),
        "git_status_porcelain": _git("status", "--porcelain"),
        "listing_sha256": E.sha256_text(
            "\n".join(f"{k} {v}" for k, v in sorted(files.items()))
        ),
    }


def snapshot_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    b, a = before["files"], after["files"]
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "git_head_same": before.get("git_head") == after.get("git_head"),
        "git_status_same": before.get("git_status_porcelain") == after.get("git_status_porcelain"),
        "identical": not added and not removed and not changed
        and before.get("git_head") == after.get("git_head")
        and before.get("git_status_porcelain") == after.get("git_status_porcelain"),
        "before": before,
        "after": after,
    }


# ---------------------------------------------------------------------------
# 测试内容
# ---------------------------------------------------------------------------

def make_roundtrip_text(marker: str) -> str:
    """含中文 + Markdown + 唯一标记 + 边界形态（尾随空行/CRLF 不参与）。"""
    return (
        f"# 原文往返核验 {marker}\n"
        "\n"
        "这是一条用于独立复核的中文原文，包含 Markdown 语法与唯一标记。\n"
        "\n"
        "- 唯一标记：`" + marker + "`\n"
        "- 用途：验证提交 → 落盘 → 原文读回是否逐字节一致\n"
        "- 期望：任何摘要/解析/清洗都不得改写本正文\n"
        "\n"
        "```text\n"
        f"{marker}:code-block-payload-0123456789\n"
        "```\n"
        "\n"
        "结尾无多余空行，末尾带一个制表符\t与一个全角空格\u3000结束。\n"
    )


def make_source_files(marker: str) -> Dict[str, str]:
    return {
        "a.md": f"# 来源文件 A {marker}\n\n原始正文 A。\n",
        "b.md": f"# 来源文件 B {marker}\n\n原始正文 B。\n",
        "sub/c.md": f"# 来源文件 C {marker}\n\n原始正文 C。\n",
    }


def make_modified_a(marker: str) -> str:
    return f"# 来源文件 A {marker}\n\n已被外部修改的正文（v2，{marker}-MOD）。\n"


def write_source_tree(root: Path, marker: str) -> Dict[str, Any]:
    E.assert_writable(root)
    root.mkdir(parents=True, exist_ok=True)
    files = make_source_files(marker)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "verify@local"], cwd=str(root),
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "verify"], cwd=str(root),
                   capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", f"source snapshot {marker}"],
                   cwd=str(root), capture_output=True, text=True)
    return files


__all__ = [
    "Consumer", "http_post", "scan_for_bytes", "scan_git_blobs", "source_snapshot",
    "snapshot_diff", "make_roundtrip_text", "make_source_files", "make_modified_a",
    "write_source_tree",
]
