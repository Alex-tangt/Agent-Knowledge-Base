"""Register the real local hosts, preserving existing private configuration.

Run deliberately after installing the runtime. Backups remain beside each
host's private configuration; this script never copies credentials to the repo.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess


def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".before-personal-memory-" +
                                        datetime.now().strftime("%Y%m%d-%H%M%S")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--home", type=Path, default=Path.home() / ".agent-memory-base")
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--bubble-dir", required=True, type=Path)
    args = parser.parse_args()
    assert args.python.is_file(), "The dedicated Python must exist"
    user = Path.home()
    skill = Path(__file__).resolve().parents[2] / "memory_base/skill/SKILL.md"
    common = [str(args.python), "-m", "memory_base.mcp_stdio"]

    # Use the host's own parser/writer rather than rewriting arbitrary TOML.
    backup(user / ".codex/config.toml")
    subprocess.run([str(args.codex), "mcp", "add", "personal-memory", "--",
                    *common, "--profile", "codex", "--home", str(args.home)], check=True)

    dsh = user / ".dsh/profiles/desktop/cordis.patch.yml"
    original = dsh.read_text(encoding="utf-8-sig")
    if "id: personal-memory-mcp" not in original:
        # New Cordis entry IDs require an insert patch. Do not parse/rewrite the
        # existing YAML: it can contain native tags and private configuration.
        backup(dsh)
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        patch = "\n- insert:\n    - id: personal-memory-mcp\n      name: '@deepseek-ai/dsh-mcp-client'\n      config:\n"
        patch += "        serverName: personal-memory\n        transport: stdio\n"
        patch += "        command: " + quote(args.python) + "\n"
        patch += "        args: [" + ", ".join(quote(x) for x in ["-m", "memory_base.mcp_stdio", "--profile", "dsh", "--home", str(args.home)]) + "]\n"
        patch += "        toolCallTimeoutMs: 180000\n        failOnStartupError: false\n"
        with dsh.open("a", encoding="utf-8", newline="") as stream:
            stream.write(patch)

    opencode = user / ".config/opencode/opencode.json"
    config = json.loads(opencode.read_text(encoding="utf-8-sig"))
    mcp = config.setdefault("mcp", {})
    if "memory-agent" in mcp:
        mcp["memory-agent"]["enabled"] = False
    mcp["personal-memory"] = {"type": "local", "command": [*common, "--profile", "opencode", "--home", str(args.home)], "enabled": True}
    backup(opencode)
    opencode.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for directory in [user / ".agents/skills/personal-memory", user / ".dsh/skills/personal-memory", user / ".config/opencode/skills/personal-memory"]:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "SKILL.md"
        if target.exists() and target.read_bytes() != skill.read_bytes():
            backup(target)
        shutil.copy2(skill, target)

    # Only append the three non-secret integration fields; preserve the existing
    # Bubble credentials byte-for-byte, and never print or back them up here.
    env = args.bubble_dir / ".env"
    text = env.read_text(encoding="utf-8-sig")
    values = {"MEMORY_BASE_URL": "http://127.0.0.1:8788", "MEMORY_BASE_PYTHON": str(args.python), "MEMORY_BASE_HOME": str(args.home)}
    for name, value in values.items():
        matches = [line for line in text.splitlines() if line.startswith(name + "=")]
        if matches:
            assert matches == [name + "=" + value], "Existing KB configuration differs; review it before changing"
    with env.open("a", encoding="utf-8", newline="") as stream:
        for name, value in values.items():
            if not any(line.startswith(name + "=") for line in text.splitlines()):
                stream.write("\n" + name + "=" + value + "\n")
    print(json.dumps({"registered": ["codex", "dsh", "opencode"], "skills_installed": 3,
                      "bubble_configured": True, "activation": "new host session / managed Bubble restart"}))


if __name__ == "__main__":
    main()
