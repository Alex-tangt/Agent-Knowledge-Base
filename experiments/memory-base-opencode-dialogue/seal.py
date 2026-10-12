"""Offline evidence packaging after the two permitted model sessions; no model calls."""
import fnmatch
import json
from pathlib import Path

from probe import HERE, CLI, digest, fetch, same_process, snapshot, write

first = json.loads((HERE / "preflight.json").read_text(encoding="utf-8"))
verified = json.loads((HERE / "verified.json").read_text(encoding="utf-8"))
binary = json.loads((HERE / "binary-adapter-evidence.json").read_text(encoding="utf-8"))
# Original safe debug summary remains untouched in Temp. Git keeps only effective rules.
permissions = verified["agent_permission"]
last_catch_all = max(i for i, p in enumerate(permissions) if p["permission"] == "*" and p["action"] == "deny")
verified["agent_permission"] = permissions[last_catch_all:]
verified["bundled_adapter"]["mapping_excerpt"] = next(
    value for value in binary["adapter_mapping_candidates"] if "createOpenAICompatible" in value
)
verified["bundled_adapter"]["evidence_kind"] = "installed executable bundled creator mapping; no independent npm package/cache claimed"
actions = {}
for tool in ("personal-memory_memory_get", "personal-memory_memory_list", "personal-memory_memory_search",
             "personal-memory_memory_add", "bash", "read", "write", "edit", "task", "skill", "memory_research"):
    matches = [p["action"] for p in verified["agent_permission"] if fnmatch.fnmatchcase(tool, p["permission"])]
    actions[tool] = matches[-1] if matches else None
verified["effective_tool_action_samples"] = actions
verified["tool_names_note"] = "debug agent lists built-in registry entries, not enabled tool availability or MCP names; exact MCP names are in actual CLI tool events"
(HERE / "verified.json").write_text(json.dumps(verified, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
_, ollama = fetch("http://127.0.0.1:11434/api/version")
reports = [json.loads((HERE / f"session-{i}.json").read_text(encoding="utf-8")) for i in (1, 2)]
analysis = [json.loads((HERE / f"analysis-{i}.json").read_text(encoding="utf-8")) for i in (1, 2)]
initial = HERE / "analysis-2.json"
old = Path(first["asset"]) / "analysis-2-initial-safe.json"
if old.exists():
    raise RuntimeError("Already sealed")
old.write_bytes(initial.read_bytes())
# Core preregistration checks real tool results. Prompt compliance is a separate dimension.
a = analysis[1]
a["verdict"] = "PASS" if (a["correct_get"] and a["boundaries"] and reports[1]["exit_code"] == 0
                           and not reports[1]["deadline_expired"]) else "FAIL"
a["requested_final_format_met"] = a["final_text_has_path"] and a["final_text_has_actual_revision"]
a["all_prompt_requirements_met"] = (a["verdict"] == "PASS" and a["expected_revision_guard_covered"]
                                      and a["requested_final_format_met"])
a["reason"] = "Actual completed get, correct scope/path, observed body and returned revision match independent file SHA256"
initial.write_text(json.dumps(a, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
remaining = [item for report in reports for item in report["owned"] if same_process(item) is not None]
final = snapshot()
result = {
    "baseline": "a852fab33db109e72a19665877f573e7bd6c2d15", "permitted_sessions": 2, "actual_sessions": 2,
    "cli_version": "1.18.35", "cli_sha256": digest(CLI.read_bytes()), "ollama_version": ollama.get("version"),
    "asset": first["asset"], "model_details": first["models"],
    "session_outcomes": [{"model": r["model"], "verdict": a["verdict"], "wall_seconds": r["wall_seconds"],
                           "exit_code": r["exit_code"], "deadline_expired": r["deadline_expired"],
                           "trace_sha256": r["trace_sha256"],
                           "expected_revision_guard_covered": a.get("expected_revision_guard_covered", False)}
                          for r, a in zip(reports, analysis)],
    "formal_health": final["health"], "capture_file_count": final["capture_file_count"],
    "capture_count_method": "directory enumeration of files in data paths with captures component; no body or source URL read",
    "config_auth_hashes": final["config_auth_sha256"],
    "config_auth_unchanged_since_pre_session_verification": final["config_auth_sha256"] == verified["before"]["config_auth_sha256"],
    "global_json_unchanged_since_first_config_probe": final["global_sha256"] == first["before"]["global_sha256"],
    "source_sha256": final["source_sha256"], "remaining_owned": remaining,
    "cleanup": [r["cleanup"] for r in reports],
    "limits": ["0.6B completed list but omitted get", "3B used expected_revision=null; conditional revision guard not covered",
               "3B final model text omitted requested path/revision; full prompt compliance failed",
               "no third session", "no default commercial model, DSH desktop, mobile or actual login acceptance",
               "no memory_search, permission-denial or add execution tested in model dialogue"],
}
assert not remaining
assert result["config_auth_unchanged_since_pre_session_verification"]
assert result["global_json_unchanged_since_first_config_probe"]
assert final["capture_file_count"] == verified["before"]["capture_file_count"]
assert all(value == 200 for value in final["health"].values())
write(HERE / "delivery.json", result)
files = [p for p in sorted(HERE.iterdir()) if p.is_file() and p.name != "hashmanifest.json"]
write(HERE / "hashmanifest.json", {p.name: digest(p.read_bytes()) for p in files})
print(json.dumps(result, ensure_ascii=False))
