"""第三方复算：对落盘物与仓内封存副本分别算 sha256，与 m5b 证据里的断言比对。

不依赖 spike 脚本内部状态，只读文件与证据 JSON。

    python verify_seal.py
"""

import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[4]
M5B = Path.home() / ".openviking-mvp-spike" / "m5b"
REL = "viking/default/resources/m5b-proj/2026-10-10/note/m5b-versioned.md"
EVIDENCE = REPO / "experiments" / "memory-framework-spike" / "mvp" / "evidence" / "m5b_outofprocess_restore.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report(name: str, path: Path, expect: str | None) -> bool:
    if not path.exists():
        print(f"  {name}: MISSING {path}")
        return False
    got = sha(path)
    ok = (expect is None) or (got == expect)
    print(f"  {name}: bytes={path.stat().st_size} sha256={got} matches_evidence={ok}")
    return ok


def main() -> int:
    d = json.load(open(EVIDENCE, encoding="utf-8"))
    check = next(x for x in d["checks"] if x["id"].startswith("M5B.3"))["detail"]
    expect = check["content_face"]["sha256"]
    sealed = d["corrections"]["sealed_copy_sha256"]
    repo_sealed = d["corrections"]["sealed_copies_in_repo"]

    print(f"evidence verdict: {d['verdict']}   expected content sha256: {expect}")
    print("落盘物（本机隔离目录）:")
    ok = True
    ok &= report("live clean-room artifact", M5B / "data-clean" / REL, expect)
    ok &= report("live reference artifact", M5B / "data-ref" / REL, expect)
    ok &= report("sealed restored-content", M5B / "sealed" / "restored-content.md", sealed["restored_content"])
    ok &= report("sealed retrieval-returned", M5B / "sealed" / "retrieval-returned-bytes.md", sealed["retrieval_returned"])
    print("仓内封存副本（可离线复算）:")
    for name, meta in repo_sealed.items():
        ok &= report(name, REPO / meta["path"], meta["sha256"])
    print("retrieval face serves restored bytes:",
          check["retrieval_face"]["served_sha256_of_first_hit"] == expect)
    print("artifact untouched after probes:", check["artifact_immutability"]["byte_identical_after_probes"])
    print("write probes targeted:", check["artifact_immutability"]["write_probes_all_targeted"])
    print("ALL CHECKS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
