"""#95 fixed Git-object audit; sensitive inputs never leave this process.

Run with formal Python -B after activating the existing root venv. No models,
installs, writes to product state, historical checkout, or service lifecycle calls.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = "4dadc407d39b26855a96079ce8561e8983dc4401"
UPSTREAM = "4270f13f418c5069316ba819cec11bb5910dcefc"
WHEEL = "45296a58fe2559e0b92f4af851c018b56148865a"
PRIMARY = Path(r"D:\python_work\work2026-4\Agent-Knowledge-Base")
BUBBLE = Path(r"D:\python_work\work2026-10\Agent-bubble")
SECRET_NAMES = ["root-key", "upstream-key", *["host-" + p for p in
                ("agent-bubble", "codex", "dsh", "opencode", "reader")]]
CREDENTIAL_NAME = re.compile(r"(?:api[_-]?key|token|password|secret|root[_-]?key|ov[_-]?key)", re.I)
TOKEN_SHAPES = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,})")
ASSIGNMENTS = re.compile(r"\b([A-Za-z_][A-Za-z0-9_-]*(?:API_KEY|TOKEN|PASSWORD|SECRET|api_key|token|password|secret|root_key))\s*[=:]\s*[\"']?([^\s\"',;<>]{8,})")
LINKS = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def safe_event(stage: str, **data):
    print(json.dumps({"stage": stage, **data}, ensure_ascii=True), flush=True)


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("git command failed: " + args[0])
    return result.stdout


def tree(repo: Path, ref: str) -> dict[str, str]:
    result = {}
    for row in git(repo, "ls-tree", "-r", "-z", ref).split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        _, kind, sha = header.split()
        if kind == b"blob":
            result[path.decode("utf-8", "surrogateescape")] = sha.decode()
    return result


def blob(repo: Path, sha: str) -> bytes:
    return git(repo, "cat-file", "blob", sha)


def json_file(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def credential_values(environment: dict[str, str]) -> list[str]:
    values = []
    for name, value in environment.items():
        if not CREDENTIAL_NAME.search(name) or not value:
            continue
        if name.upper().endswith("AUTH_TOKENS"):
            try:
                values.extend(str(key) for key in json.loads(value))
            except (ValueError, TypeError):
                pass
        else:
            values.append(value)
    return values


def read_env(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if match:
            result[match[1]] = match[2].strip().strip("\"'")
    return result


def sensitive_sources(repo: Path, files: dict[str, str]):
    # Import the installed formal thin package before the optional root adapter.
    from memory_base.credentials import get_secret, env_name
    from memory_base.deploy import owned_component
    from memory_base.upstream import OpenVikingClient

    sys.path.append(str(PRIMARY / "venv" / "Lib" / "site-packages"))
    import psutil

    home = Path.home() / ".agent-memory-base"
    homes = {"formal": home, "r4-default": Path.home() / ".mb86/r4/d",
             "r4-fresh": Path.home() / ".mb86/r4/f",
             "r5-default": Path.home() / ".mb86/r5/d",
             "r5-fresh": Path.home() / ".mb86/r5/f",
             "94-target": Path.home() / ".mb94/a"}
    inputs = []  # (category, literal); never serialised.
    coverage = []
    gaps = []
    def retain(label, values):
        values = [v for v in values if isinstance(v, str) and len(v) >= 8]
        inputs.extend(("known_credential", v) for v in values)
        coverage.append({"source": label, "values": len(values)})
    retain("audit-process-credential-environment", credential_values(dict(os.environ)))
    for label, path in (("bubble-env", BUBBLE / ".env"),
                        ("memory-agent-env", PRIMARY / "memory_agent/.env")):
        try:
            retain(label, credential_values(read_env(path)))
        except (OSError, ValueError):
            gaps.append(label + ":unavailable")
    overrides = {name: os.environ.pop(name) for name in
                 [env_name(n) for n in SECRET_NAMES] if name in os.environ}
    vault = {}
    try:
        for label, fixture in homes.items():
            values = []
            for name in SECRET_NAMES:
                try:
                    value = get_secret(name, fixture)
                    if value:
                        values.append(value)
                        vault[(label, name)] = value
                except Exception:
                    gaps.append(label + ":vault-read-error")
            retain(label + "-vault", values)
            if len(values) != len(SECRET_NAMES):
                gaps.append(label + ":missing-vault-entries")
    finally:
        os.environ.update(overrides)

    for label, fixture in homes.items():
        try:
            config = json_file(fixture / "deployment.json")
            records = json_file(fixture / "processes.json") if (fixture / "processes.json").exists() else {}
        except (OSError, ValueError):
            gaps.append(label + ":process-records-unavailable")
            continue
        inspected = 0
        for component, record in records.items():
            if component not in ("embed", "upstream", "gateway"):
                continue
            try:
                if owned_component(component, fixture.resolve(), config, record):
                    retain(label + "-" + component + "-process-env",
                           credential_values(psutil.Process(record["pid"]).environ()))
                    inspected += 1
                else:
                    gaps.append(label + "-" + component + ":no-current-owned-process")
            except Exception:
                gaps.append(label + "-" + component + ":process-env-unavailable")
        coverage.append({"source": label + "-owned-processes", "processes": inspected})

    # Bubble daemon identity is tied to its exact script, then its current child.
    inspected = 0
    for process in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        try:
            command = process.info["cmdline"] or []
            normalized = [os.path.normcase(os.path.normpath(arg)) for arg in command]
            manager = os.path.normcase(str(BUBBLE / "scripts/local-backend.mjs"))
            server = os.path.normcase(str(BUBBLE / "dist/server/index.js"))
            if manager not in normalized and server not in normalized:
                continue
            # Finite PID/birth/full-command double check before reading env.
            birth = process.info["create_time"]
            if process.create_time() != birth or process.cmdline() != command:
                continue
            retain("bubble-owned-process-env", credential_values(process.environ()))
            inspected += 1
        except (psutil.Error, OSError):
            gaps.append("bubble:process-env-unavailable")
    coverage.append({"source": "bubble-owned-processes", "processes": inspected})

    # Only the three explicit whitelist receipts in the already tracked script.
    receipts = ast.parse(blob(repo, files["experiments/memory-base-live/lead_formal_restore.py"]).decode("utf-8-sig"))
    expected = next(ast.literal_eval(node.value) for node in receipts.body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXPECTED" for t in node.targets))
    config = json_file(home / "deployment.json")
    reader = vault.get(("formal", "host-reader"))
    upstream_key = vault.get(("formal", "upstream-key"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    content_count = metadata_count = private_url_count = title_count = 0
    for capture, revision in expected.items():
        uri = "viking://resources/captures/" + capture + "/content.md"
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{config['ports']['gateway']}/api/memory/get",
                data=json.dumps({"uri": uri, "expected_revision": revision}).encode(),
                headers={"Authorization": "Bearer " + (reader or ""), "Content-Type": "application/json"})
            with opener.open(request, timeout=30) as response:
                payload = json.loads(response.read())
            content = payload["content"]
            if payload["revision"] != revision or hashlib.sha256(content.encode()).hexdigest() != revision:
                raise ValueError("receipt mismatch")
            inputs.append(("actual_full_body", content))
            # Long independent paragraphs and contiguous text expose excerpt leakage.
            fragments = {part.strip() for part in re.split(r"\n\s*\n", content) if len(part.strip()) >= 120}
            fragments.update(content[pos:pos + 160] for pos in range(0, len(content) - 159, 160))
            inputs.extend(("actual_body_fragment", part) for part in fragments)
            content_count += 1
        except Exception:
            gaps.append("original-receipt:read-or-revision-failed")
            continue
        try:
            client = OpenVikingClient(f"http://127.0.0.1:{config['ports']['upstream']}", upstream_key or "", timeout=30)
            metadata = json.loads(client.read(uri.rsplit("/", 1)[0] + "/capture.json"))
            title = metadata.get("title")
            if isinstance(title, str) and len(title) >= 8:
                inputs.append(("actual_title_public_summary_review", title))
                title_count += 1
            source = metadata.get("source_url")
            if isinstance(source, str):
                parsed = urllib.parse.urlsplit(source)
                query_keys = {key.casefold() for key in urllib.parse.parse_qs(parsed.query)}
                public_video_keys = {"v", "p", "t", "start", "list", "index", "si", "feature", "spm_id_from"}
                private = bool(parsed.username or parsed.password or query_keys - public_video_keys)
                if private:
                    inputs.append(("actual_private_source_url", source))
                    private_url_count += 1
            metadata_count += 1
        except Exception:
            gaps.append("original-receipt:metadata-unavailable")
    coverage.append({"source": "three-whitelist-original-receipts", "expected": len(expected),
                     "bodies": content_count, "metadata": metadata_count,
                     "titles_for_summary_review": title_count, "private_urls": private_url_count})
    return inputs, coverage, sorted(set(gaps))


def variants(value: str):
    candidates = {value, value.replace("\r\n", "\n"), value.replace("\r\n", "\n").replace("\n", "\r\n"),
                  json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value, ensure_ascii=True)[1:-1]}
    return {text.encode(encoding) for text in candidates for encoding in ("utf-8", "utf-16-le")}


def fixture(value: str) -> bool:
    lowered = value.casefold()
    return bool(re.search(r"(?:example|dummy|placeholder|replace|changeme|your[_-]|test[_-]|synthetic|fake[_-]|\$\{|os\.environ|process\.env|localhost|127\.0\.0\.1)", lowered)
                or lowered in {"local-embedding", "none", "null", "true", "false"}
                or re.fullmatch(r"[a-z_-]+", lowered))


def links_and_maps(repo: Path, files: dict[str, str], text_cache: dict[str, str]):
    changed = git(repo, "diff", "--name-only", UPSTREAM, BASE, "--", "*.md").decode().splitlines()
    findings = []
    checked = external = anchors = 0
    for path in changed:
        if path not in files:
            continue
        content = text_cache.get(path) or blob(repo, files[path]).decode("utf-8", "replace")
        # Skip fenced code examples when checking actionable document links.
        content = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", content)
        for match in LINKS.finditer(content):
            target = match[1].strip("<>")
            parsed = urllib.parse.urlsplit(target)
            if parsed.scheme or parsed.netloc:
                external += 1
                continue
            if target.startswith("#"):
                anchors += 1
                continue
            target_path = urllib.parse.unquote(parsed.path).replace("\\", "/")
            if not target_path:
                continue
            candidate = os.path.normpath(str(PurePosixPath(path).parent / target_path)).replace("\\", "/")
            candidate = candidate.removeprefix("./")
            checked += 1
            exists = candidate in files or any(name.startswith(candidate.rstrip("/") + "/") for name in files)
            if not exists:
                findings.append({"path": path, "line": content.count("\n", 0, match.start()) + 1,
                                 "category": "local_link_unresolved", "target_path": candidate})
    names = ["__init__.py", "__main__.py", "deploy.py", "credentials.py", "embed.py", "gateway.py",
             "upstream.py", "mcp_stdio.py", "paths.py", "sources.py", "backup.py", "autostart.py",
             "requirements.txt", "constraints.txt", "skill/SKILL.md"]
    frozen = tree(repo, WHEEL)
    payload = []
    for name in names:
        path = "memory_base/" + name
        equal = path in files and path in frozen and blob(repo, files[path]).replace(b"\r\n", b"\n") == blob(repo, frozen[path]).replace(b"\r\n", b"\n")
        payload.append({"path": path, "normalized_equal": equal})
    map_paths = []
    for path in ("README.md", "AGENTS.md", "CONTEXT.md"):
        content = text_cache.get(path) or blob(repo, files[path]).decode("utf-8", "replace")
        refs = sorted(set(re.findall(r"`((?:memory_base/|install-memory-base\.)[^`\s]*(?:\.py|\.txt|\.toml|\.md|\.ps1|\.sh)?)`", content)))
        for reference in refs:
            reference = reference.rstrip("/.,;：")
            exists = reference in files or any(name.startswith(reference.rstrip("/") + "/") for name in files)
            map_paths.append({"map": path, "path": reference, "exists": exists})
    return {"changed_markdown": len(changed), "local_links_checked": checked,
            "external_links_excluded": external, "anchor_only_excluded": anchors,
            "findings": findings, "payload": payload, "map_paths": map_paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    allowed = repo / "experiments/memory-base-publication"
    report = args.report.resolve()
    if not report.is_relative_to(allowed) or report.exists():
        raise RuntimeError("new report must be inside the ticket write scope")
    started = time.monotonic()
    current = tree(repo, BASE)
    objects = {}
    for row in git(repo, "rev-list", "--objects", f"{UPSTREAM}..{BASE}").decode("utf-8", "surrogateescape").splitlines():
        sha, _, path = row.partition(" ")
        objects[sha] = path
    types = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                           input="\n".join(objects).encode(), capture_output=True, check=True).stdout.decode().splitlines()
    history = {row.split()[0]: objects[row.split()[0]] for row in types if row.endswith(" blob")}
    paths = defaultdict(set)
    for path, sha in current.items():
        paths[sha].add(path)
    for sha, path in history.items():
        if path:
            paths[sha].add(path)
    safe_event("started", pid=os.getpid(), tree_blobs=len(set(current.values())), history_blobs=len(history))
    inputs, coverage, gaps = sensitive_sources(repo, current)
    patterns = defaultdict(set)
    for category, value in inputs:
        patterns[category].update(variants(value))
    safe_event("sources-ready", sources=len(coverage), known_unique_credentials=len({v for c, v in inputs if c == "known_credential"}), gaps=len(gaps))
    results = []
    totals = Counter()
    fixture_counts = Counter()
    text_cache = {}
    process = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for index, sha in enumerate(sorted(paths), 1):
            process.stdin.write((sha + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            size = int(header[2])
            raw = process.stdout.read(size)
            if len(raw) != size or process.stdout.read(1) != b"\n":
                raise RuntimeError("incomplete Git blob read")
            scopes = []
            if sha in set(current.values()):
                scopes.append("tree")
                totals["tree_bytes"] += size
            if sha in history:
                scopes.append("history")
                totals["history_bytes"] += size
            categories = []
            for category, literals in patterns.items():
                if any(literal in raw for literal in literals):
                    categories.append(category)
            decoded = raw.decode("utf-8", "replace")
            if "\x00" not in decoded:
                for category, regex in (("unknown_token_shape", TOKEN_SHAPES), ("credential_assignment_review", ASSIGNMENTS)):
                    matches = list(regex.finditer(decoded))
                    review = [match for match in matches if not fixture(match[0] if category == "unknown_token_shape" else match[2])]
                    fixture_counts[category] += len(matches) - len(review)
                    if review:
                        categories.append(category)
            if categories:
                finding = {"blob": sha, "paths": sorted(paths[sha]), "sets": scopes, "categories": sorted(set(categories))}
                results.append(finding)
                if any(c in categories for c in ("known_credential", "actual_full_body", "actual_body_fragment", "actual_private_source_url")):
                    safe_event("sensitive-location", **finding)
            for path in paths[sha]:
                if path in current and current[path] == sha and path.endswith(".md"):
                    text_cache[path] = decoded
            if index % 200 == 0:
                safe_event("scanning", completed=index, total=len(paths))
    finally:
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=10)
    maps = links_and_maps(repo, current, text_cache)
    output = {"base": BASE, "upstream": UPSTREAM, "wheel_reference": WHEEL,
              "commit_count": int(git(repo, "rev-list", "--count", f"{UPSTREAM}..{BASE}")),
              "coverage": {"tree_files": len(current), "tree_unique_blobs": len(set(current.values())),
                           "history_unique_blobs": len(history), "union_unique_blobs": len(paths), **dict(totals)},
              "sensitive_sources": coverage, "gaps": gaps, "findings": results,
              "generic_fixture_matches": dict(fixture_counts), "maps": maps,
              "limits": ["Only currently available authorized credentials; rotated historical values unknown.",
                         "Exact UTF-8/UTF-16LE, normalized newline and JSON-escaped literals; no OCR, archive expansion, base64 or arbitrary encoding proof.",
                         "Three explicit original receipts; no unrelated user files or all-user-corpus scan.",
                         "Title matches are owner summary review, not automatic privacy violations.",
                         "Credential assignments require review; placeholder heuristic is not proof of safety.",
                         "Local link existence only; no external fetching or anchor validity claim.",
                         "Fixed 4dadc40 excludes later documentation and CI corrections; no whole-goal acceptance claim."],
              "elapsed_seconds": round(time.monotonic() - started, 2)}
    report.write_text(json.dumps(output, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    safe_event("complete", report=report.name, findings=len(results), gaps=len(gaps), elapsed_seconds=output["elapsed_seconds"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Exception text can include secrets or URLs. Emit the type only.
        safe_event("failed", error_type=type(exc).__name__)
        raise SystemExit(1) from None
