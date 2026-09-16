"""收录接口面（#36 / ADR-0025 D8）：include / exclude / list。

**收录是 DDL（决定基表 extent），不是写数据**（`memory_add` 是 DML，写一行）。
overlay 是独立清单（`settings.overlay_config_file()`，gitignored + `.example`），
运行时被 `corpus.loader` 合并——改它**免重启**生效。

粒度 = 路径模式（精确文件 / 窄 glob；收整棵子树须配 exclude）。`owner` 默认按来源
（文件落在某注册表来源下就继承它的 owner），显式收录可指定。

**移除走预览 + 确认**：离开收录范围的条目会在下一次惰性刷新时删除其派生点
（真相源文件不动），所以 `exclude` 默认只返回预览，`confirm=true` 才写 overlay。

授权（D3 / D15）：需要写入角色；受限身份（`owned_domains` 非空）只能收录 / 移除
自己拥有的域，且收录的文件必须在自己的可见范围内（密级 / 驻留）。
"""
from __future__ import annotations

import json
import os
import tempfile

from memory_agent import settings
from memory_agent.corpus import loader
from memory_agent.gateway.authz import can_own, can_read
from memory_agent.gateway.identity import Identity, default_identity
from memory_agent.memory.entries import Entry

MAX_FILES_IN_PREVIEW = 50
MAX_RESOLVED = 500


class AdmissionError(RuntimeError):
    """收录操作被拒 / 无法执行；消息面向调用方，含修正方向。"""


class AdmissionManager:
    def __init__(self, overlay_path: str | None = None):
        self._overlay_path = overlay_path or settings.overlay_config_file()

    @property
    def overlay_path(self) -> str:
        return self._overlay_path

    # ------------------------------------------------------------------ read

    def list(self, limit: int = MAX_RESOLVED) -> dict:
        """当前收录全景：注册表默认来源 + 显式 overlay + 逐文件解析结果。

        `origin`（default / explicit）区分「注册表默认」与「overlay 显式」，
        满足 #36 验收里 `list` 需能分辨两类来源。
        """
        selection = loader.resolve_selection()
        readonly = [f for f in selection.files if not f.writable]
        resolved = [
            {
                "source": f.source,
                "owner": f.owner,
                "origin": "explicit" if f.explicit else "default",
            }
            for f in readonly
        ]
        return {
            "overlay_path": self._overlay_path,
            "overlay_exists": os.path.isfile(self._overlay_path),
            "complete": selection.complete,
            "sources": [
                {
                    "label": s.label,
                    "root": s.root,
                    "owner": s.owner,
                    "origin": "explicit" if s.explicit else "default",
                }
                for s in selection.sources
            ],
            "overlay": {
                "include": selection.include_specs,
                "exclude": selection.exclude_specs,
            },
            "counts": {
                "selected": len(selection.files),
                "readonly": len(readonly),
                "writable": len(selection.files) - len(readonly),
                "explicit": sum(1 for f in readonly if f.explicit),
            },
            "resolved": resolved[:limit],
            "resolved_truncated": len(resolved) > limit,
        }

    # --------------------------------------------------------------- include

    def include(self, *, pattern: str, owner: str | None = None, label: str | None = None,
                identity: Identity | None = None, confirm: bool = False) -> dict:
        """把一条路径模式加进 overlay 收录清单（非破坏性：只增不减）。"""
        identity = identity or default_identity()
        pattern = (pattern or "").strip()
        if not pattern:
            raise AdmissionError("pattern 不能为空（精确文件路径或窄 glob）")
        overlay, complete = loader.load_overlay()
        if not complete:
            raise AdmissionError(f"overlay 配置非法，请先修好：{self._overlay_path}")

        spec = {
            "pattern": pattern,
            "label": (label or "").strip() or None,
            "owner": (owner or "").strip() or None,
        }
        root, resolved_label, spec_owner, matched = loader.resolve_include(spec)
        resolved_label = spec["label"] or resolved_label
        explicit_owner = spec["owner"] or spec_owner
        registry, _ = settings.readonly_sources()

        files: list[dict] = []
        for path, _mtime_ns, _size in matched:
            file_owner = explicit_owner or loader.owner_for_path(path, registry, resolved_label)
            entry = Entry.from_file(
                path, source=resolved_label, writable=False, owner=file_owner, root=root,
            )
            if not can_read(identity, entry.to_manifest()):
                raise AdmissionError(
                    f"文件超出当前身份的可见范围（密级 / 驻留）：{path}"
                )
            if not can_own(identity, file_owner):
                raise AdmissionError(
                    f"文件不属于当前身份拥有的域（owner={file_owner}）：{path}"
                )
            files.append({"path": path, "owner": file_owner})

        preview = {
            "pattern": pattern,
            "label": resolved_label,
            "owner": explicit_owner or resolved_label,
            "root": root,
            "matched": len(files),
            "files": files[:MAX_FILES_IN_PREVIEW],
            "files_truncated": len(files) > MAX_FILES_IN_PREVIEW,
        }
        if not confirm:
            return {
                "status": "confirmation_required",
                "action": "include",
                "written": False,
                "preview": preview,
                "hint": "新增收录不影响已有条目；确认后以 confirm=true 写入 overlay。",
            }
        if not matched:
            raise AdmissionError(f"pattern 未匹配到任何 .md 文件：{pattern}")

        overlay["include"].append(spec)
        self._write_overlay(overlay)
        return {
            "status": "written",
            "action": "include",
            "written": True,
            "overlay_path": self._overlay_path,
            "preview": preview,
        }

    # --------------------------------------------------------------- exclude

    def exclude(self, *, pattern: str, identity: Identity | None = None,
                confirm: bool = False) -> dict:
        """从收录范围移除：把模式加进 overlay 的 exclude 清单（DDL 收窄）。

        会让**当前命中该模式**的条目离开收录范围 → 下一次惰性刷新删其派生点，
        故默认只预览，`confirm=true` 才落盘（防孤儿误删）。
        """
        identity = identity or default_identity()
        pattern = (pattern or "").strip()
        if not pattern:
            raise AdmissionError("pattern 不能为空")
        overlay, complete = loader.load_overlay()
        if not complete:
            raise AdmissionError(f"overlay 配置非法，请先修好：{self._overlay_path}")
        if pattern in overlay["exclude"]:
            return {
                "status": "noop",
                "action": "exclude",
                "written": False,
                "reason": f"该模式已在 exclude 清单：{pattern}",
            }

        selection = loader.resolve_selection()
        leaving = [
            f for f in selection.files
            if not f.writable and loader.matches_exclude(f, pattern)
        ]
        for selected in leaving:
            if not can_own(identity, selected.owner):
                raise AdmissionError(
                    f"不能移除不属于自己的域：{selected.source}（owner={selected.owner}）"
                )

        preview = {
            "pattern": pattern,
            "leaving": len(leaving),
            "files": [
                {"source": f.source, "owner": f.owner}
                for f in leaving[:MAX_FILES_IN_PREVIEW]
            ],
            "files_truncated": len(leaving) > MAX_FILES_IN_PREVIEW,
        }
        if not confirm:
            return {
                "status": "confirmation_required",
                "action": "exclude",
                "written": False,
                "preview": preview,
                "hint": "移除会让这些条目离开收录范围，下一次检索的惰性刷新将删除其派生点"
                        "（真相源文件不动）；确认后以 confirm=true 重试。",
            }

        overlay["exclude"].append(pattern)
        self._write_overlay(overlay)
        return {
            "status": "written",
            "action": "exclude",
            "written": True,
            "overlay_path": self._overlay_path,
            "preview": preview,
        }

    # ------------------------------------------------------------- internals

    def _write_overlay(self, overlay: dict) -> None:
        path = self._overlay_path
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        data = {"include": overlay.get("include", []), "exclude": overlay.get("exclude", [])}
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", dir=directory, delete=False
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        os.replace(temp_path, path)
