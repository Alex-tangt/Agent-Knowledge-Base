"""Classify supplemental candidates without printing their literals or context."""
from collections import Counter
import json
from pathlib import Path
import re
import runpy
import subprocess


def classify(key, value, bare, text, match, path):
    trimmed = value.strip("`,;:)]")
    if re.search(r"tokeniz|tiktoken|secret_names|api_key_manager|SecretField", key, re.I):
        return "package_inventory_or_noncredential_field"
    if re.search(r"(?:max|input|output|prompt|completion|total|count|ratio|per|budget|latency|mean|median|p\d+)_(?:tokens?|secrets?)|(?:tokens?|secrets?)_(?:count|per|saved|removed|remaining|written|deleted)|max_tokens", key, re.I):
        return "numeric_metric_or_inventory"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", trimmed) or trimmed in ("None", "True", "False", "true", "false"):
        return "number_boolean_null"
    if bare and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:[|:)]+)?", value)
                 or re.fullmatch(r"=+", value)):
        return "code_reference_annotation_or_comparison"
    if value in ("$", "#", "}", "`"):
        return "empty_placeholder_or_syntax"
    if re.fullmatch(r"[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|TOKENS|PASSWORD)", value):
        return "environment_variable_identifier"
    if re.fullmatch(r"\$\{?[A-Z_][A-Z0-9_]*\}?", value) or "${" in value or "\\$" in value:
        return "environment_expansion_placeholder"
    if text[match.end():].lstrip().startswith((".", "+")):
        return "computed_code_expression"
    if bare and ("`" in value or any(ord(char) > 127 for char in value)):
        return "prose_or_markdown_delimiter"
    # Finite manual dispositions from the raw candidate locations: test fixture
    # construction, temp/local fake client, and repr-redaction assertion contexts.
    # No arbitrary literal is exempted merely because it occurs in a test path.
    line = text.count("\n", 0, match.start()) + 1
    manual = {
        ("tests/unit/test_connect.py", 208): "fixture_existing_config_preservation",
        ("tests/unit/test_connect.py", 242): "fixture_existing_config_preservation",
        ("tests/unit/test_agent_loop.py", 369): "fixture_repr_redaction_assertion",
        ("tests/unit/test_memory_base_sources.py", 205): "fixture_reject_embedded_credential",
        ("tests/unit/test_memory_base_sources.py", 207): "fixture_reject_embedded_credential",
        ("tests/unit/test_gateway_authz.py", 62): "fixture_assert_audit_redaction",
        ("experiments/memory-base-live/bubble_bridge_probe.mjs", 73): "fixture_no_external_llm_needed",
        ("experiments/memory-base-live/bubble_bridge_probe.mjs", 74): "fixture_no_external_llm_needed",
        ("memory_agent/eval/shared_service_41.py", 80): "fixture_local_host_identity",
        ("memory_agent/eval/shared_service_41.py", 81): "fixture_local_host_identity",
        ("memory_agent/eval/bge_m3_embeddings_43.py", 166): "fixture_preserve_existing_profile",
    }
    if (path, line) in manual:
        return manual[(path, line)]
    if path.startswith("experiments/memory-framework-spike/mvp/") and re.fullmatch(r"[a-z][a-z_-]*", value) and re.search(r"local|unused|not.?needed", value):
        return "fixture_local_embedding_placeholder"
    return "unresolved_literal_owner_review"


def main():
    repo = Path(__file__).resolve().parents[2]
    scope = repo / "experiments/memory-base-publication"
    output = scope / "candidate-review.json"
    if output.exists():
        raise RuntimeError("Refuse report overwrite")
    module = runpy.run_path(str(scope / "audit.py"))
    original = json.loads((scope / "generic-4dadc40.json").read_text())
    results = []
    counts = Counter()
    process = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for finding in original["findings"]:
            process.stdin.write((finding["blob"] + "\n").encode())
            process.stdin.flush()
            size = int(process.stdout.readline().split()[2])
            raw = process.stdout.read(size)
            assert len(raw) == size and process.stdout.read(1) == b"\n"
            text = raw.decode("utf-8", "replace")
            locations = []
            for match in module["ASSIGNMENTS"].finditer(text):
                if not module["CREDENTIAL_NAME"].search(match[2]):
                    continue
                value = next((group for group in match.groups()[2:] if group is not None), "")
                bare = match[5] is not None
                expression = bare and bool(re.search(r"[.(\[{}]|^\$|^await$", value))
                if not value or module["fixture"](value) or expression:
                    continue
                criterion = classify(match[2], value, bare, text, match, finding["paths"][0])
                counts[criterion] += 1
                locations.append({"line": text.count("\n", 0, match.start()) + 1,
                                  "key_form": "bare" if not match[1] else "quoted",
                                  "value_form": "bare" if bare else "quoted_literal",
                                  "criterion": criterion})
            results.append({"blob": finding["blob"], "paths": finding["paths"],
                            "sets": finding["sets"], "locations": locations})
    finally:
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=10)
    final = {"base": module["BASE"], "upstream": module["UPSTREAM"],
             "raw_candidate_blobs": len(original["findings"]), "criterion_counts": dict(counts),
             "locations": results,
             "limits": ["Raw generic report retained unchanged; classification does not delete findings.",
                        "Finite manual fixture dispositions are tied to path and line within fixed blob hashes.",
                        "Unresolved literals remain owner-review items; no literal or context is serialized.",
                        "No additional vault, environment or HTTP content reads."]}
    output.write_text(json.dumps(final, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": "candidate-review-complete", "counts": dict(counts)}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"stage": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
