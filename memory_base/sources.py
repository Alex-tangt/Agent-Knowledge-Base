"""Read-only, live Markdown sources. No source bodies are persisted here."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from memory_base.paths import PROJECT_NAME, contains, logical_path, overlaps

DEFAULT_INCLUDE = ["README.md", "docs/**/*.md"]
DEFAULT_EXCLUDE = ["**/.*/**", "**/node_modules/**", "**/venv/**", "**/.venv/**",
                   "**/outputs/**", "**/secrets/**", "**/credentials/**"]
_BLOCKED_DIRS = {"node_modules", "venv", "__pycache__", "outputs", "secrets", "credentials"}
_BLOCKED_NAMES = {"secrets.md", "credentials.md", "private-key.md"}


class SourceError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _patterns(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 128:
        raise ValueError("source patterns must be a nonempty list")
    result = []
    for pattern in value:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 1024:
            raise ValueError("invalid source pattern")
        if pattern.startswith("/") or any(c in pattern for c in "\\:%?#\x00"):
            raise ValueError("source patterns must be relative")
        if any(p in {"", ".", ".."} for p in pattern.split("/")):
            raise ValueError("invalid source pattern")
        result.append(pattern)
    return result


def validate_registry(data: object) -> dict:
    """Validate the CLI/gateway common schema without requiring sources online."""
    if (not isinstance(data, dict) or set(data) != {"schema", "projects"}
            or type(data["schema"]) is not int or data["schema"] != 1
            or not isinstance(data["projects"], list)):
        raise ValueError("sources registry must contain schema=1 and projects")
    names, roots, result = set(), set(), []
    for item in data["projects"]:
        if (not isinstance(item, dict) or set(item) - {"name", "root", "include", "exclude"}
                or not {"name", "root"} <= set(item)):
            raise ValueError("invalid source project fields")
        name, root = item["name"], item["root"]
        if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name) or name.lower() in names:
            raise ValueError("invalid or duplicate project name")
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError("source root must be an absolute local path")
        # Network paths introduce credentials and a different availability boundary.
        if root.startswith(("\\\\", "//")) or "\x00" in root:
            raise ValueError("source root must be a local path")
        resolved = os.path.normcase(os.path.abspath(root))
        if resolved in roots:
            raise ValueError("duplicate source root")
        include = _patterns(item.get("include", DEFAULT_INCLUDE))
        exclude = item.get("exclude", DEFAULT_EXCLUDE)
        if exclude != []:
            exclude = _patterns(exclude)
        result.append({"name": name, "root": str(Path(root)), "include": include, "exclude": exclude})
        names.add(name.lower())
        roots.add(resolved)
    return {"schema": 1, "projects": result}


def _matches(path: str, pattern: str) -> bool:
    # **/ also matches zero directories; ordinary * never crosses a separator.
    expression = ""
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            expression += "(?:.*/)?"
            index += 3
        elif pattern[index:index + 2] == "**":
            expression += ".*"
            index += 2
        elif pattern[index] == "*":
            expression += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            expression += "[^/]"
            index += 1
        else:
            expression += re.escape(pattern[index])
            index += 1
    return re.fullmatch(expression, path) is not None


def selected(project: dict, rel: str) -> bool:
    parts = rel.split("/")
    if (not rel.endswith(".md") or any(p.startswith(".") for p in parts)
            or any(p.lower() in _BLOCKED_DIRS for p in parts[:-1])
            or parts[-1].lower() in _BLOCKED_NAMES):
        return False
    return (any(_matches(rel, p) for p in project["include"])
            and not any(_matches(rel, p) for p in project["exclude"]))


def _link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(root: Path, rel: str = "") -> Path:
    target = root.joinpath(*rel.split("/")) if rel else root
    # Check every existing component, including root ancestors, for Windows
    # junctions/reparse points and POSIX symlinks. None are followed.
    current = Path(target.anchor)
    try:
        for part in target.parts[1:]:
            current = current / part
            if _link(current.lstat()):
                raise SourceError(403, "SOURCE_LINK_FORBIDDEN")
    except FileNotFoundError:
        raise SourceError(404 if rel and root.is_dir() else 503,
                          "SOURCE_NOT_FOUND" if rel and root.is_dir() else "SOURCE_UNAVAILABLE") from None
    except OSError:
        raise SourceError(503, "SOURCE_UNAVAILABLE") from None
    if not target.resolve().is_relative_to(root.resolve()):
        raise SourceError(403, "SOURCE_PATH_FORBIDDEN")
    return target


class SourceRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            return validate_registry(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return {"schema": 1, "projects": []}
        except (OSError, ValueError, TypeError):
            raise SourceError(503, "SOURCE_REGISTRY_INVALID") from None

    def _project(self, path: str) -> tuple[dict, str]:
        logical_path(path)
        parts = path.split("/", 2)
        if len(parts) != 3 or parts[0] != "projects":
            raise SourceError(422, "SOURCE_PATH_INVALID")
        for project in self.load()["projects"]:
            if project["name"] == parts[1]:
                if not selected(project, parts[2]):
                    raise SourceError(403, "SOURCE_PATH_FORBIDDEN")
                return project, parts[2]
        raise SourceError(404, "SOURCE_NOT_REGISTERED")

    def read(self, path: str, expected_revision: str | None = None) -> dict:
        project, rel = self._project(path)
        root = _safe_path(Path(project["root"]))
        if not root.is_dir():
            raise SourceError(503, "SOURCE_UNAVAILABLE")
        target = _safe_path(root, rel)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            before = target.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise SourceError(403, "SOURCE_FILE_FORBIDDEN")
            with os.fdopen(os.open(target, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise SourceError(409, "SOURCE_CHANGED_DURING_READ")
                body = stream.read()
                after = os.fstat(stream.fileno())
            current = _safe_path(root, rel).lstat()
            if ((opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    or len(body) != after.st_size
                    or (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size)
                    != (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size)):
                raise SourceError(409, "SOURCE_CHANGED_DURING_READ")
            content = body.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError(503, "SOURCE_NOT_UTF8") from None
        except FileNotFoundError:
            raise SourceError(404, "SOURCE_NOT_FOUND") from None
        except OSError:
            raise SourceError(503, "SOURCE_UNAVAILABLE") from None
        revision = hashlib.sha256(body).hexdigest()
        if expected_revision is not None and revision != expected_revision:
            raise SourceError(409, "REVISION_MISMATCH")
        return {"uri": path, "path": path, "content": content, "revision": revision,
                "content_hash": revision, "source": {"project": project["name"], "relative_path": rel},
                "freshness": "live", "bytes": len(body)}

    def scan(self, scopes: list[str]) -> list[dict]:
        """Read only authorized subtrees; fail visibly on a selected scan failure."""
        entries = []
        for project in self.load()["projects"]:
            prefix = "projects/" + project["name"]
            grants = [scope for scope in scopes if overlaps(prefix, scope)]
            if not grants:
                continue
            root = _safe_path(Path(project["root"]))
            if not root.is_dir():
                raise SourceError(503, "SOURCE_UNAVAILABLE")
            pending = [root]
            while pending:
                directory = pending.pop()
                try:
                    with os.scandir(directory) as children:
                        children = sorted(children, key=lambda child: child.name)
                    for child in children:
                        rel = Path(child.path).relative_to(root).as_posix()
                        path = prefix + "/" + rel
                        if not any(overlaps(path, scope) for scope in grants):
                            continue
                        if child.name.startswith(".") or child.name.lower() in _BLOCKED_DIRS:
                            continue
                        logical_path(path)
                        info = child.stat(follow_symlinks=False)
                        if _link(info):
                            raise SourceError(403, "SOURCE_LINK_FORBIDDEN")
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(_safe_path(root, rel))
                        elif selected(project, rel) and any(contains(scope, path) for scope in grants):
                            entries.append(self.read(path))
                except SourceError:
                    raise
                except (OSError, ValueError):
                    raise SourceError(503, "SOURCE_SCAN_INCOMPLETE") from None
        return sorted(entries, key=lambda entry: entry["path"])


def lexical_score(query: str, text: str) -> float:
    terms = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", query.casefold())
    terms = sorted(set(terms))
    lowered = text.casefold()
    return sum(term in lowered for term in terms) / len(terms) if terms else 0.0
