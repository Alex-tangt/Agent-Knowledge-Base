# -*- coding: utf-8 -*-
"""复核线驱动器。

用法（一律用主树 venv-ov 的解释器）：
  <main>\\venv-ov\\Scripts\\python.exe run_verify.py --stage static
  <main>\\venv-ov\\Scripts\\python.exe run_verify.py --stage live

静态阶段：不联网、不启服务，跑完把 evidence/static_selfcheck.json 落盘。
实跑阶段：启本线隔离实例（8783）→ 跑 C1–C5 → 停机 → 重启验 C1d → 停机 → 落盘证据。

纪律：只写 experiments/memory-spike-verify/ 与 %USERPROFILE%\\.openviking-mvp-verify\\。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_checks as V  # noqa: E402
import verify_criteria as C  # noqa: E402
import verify_env as E  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="独立复核：MVP 阶段 1–3 关键结论")
    ap.add_argument("--stage", choices=["static", "preflight", "live"], default="static")
    ap.add_argument("--no-start", action="store_true",
                    help="live 阶段不自行启服务（假定已在 8783 运行）")
    args = ap.parse_args()

    if args.stage == "preflight":
        payload = V.run_preflight()
        out = E.write_json(E.EVIDENCE_DIR / "preflight_instruments.json", payload)
        md = E.write_text(Path(out).with_suffix(".md"), V.render_markdown(payload))
        print(json.dumps({"stage": "preflight", "verdict": payload["verdict"],
                          "payload_sha256": payload["payload_sha256"],
                          "problems": payload["problems"],
                          "out": str(out), "md": str(md)}, ensure_ascii=False, indent=2))
        return 0 if payload["verdict"] == "PASS" else 1

    if args.stage == "static":
        payload = V.run_static()
        out = E.write_json(E.EVIDENCE_DIR / "static_selfcheck.json", payload)
        md = E.write_text(Path(out).with_suffix(".md"), V.render_markdown(payload))
        print(json.dumps({"stage": "static", "verdict": payload["verdict"],
                          "criteria_sha256": payload["criteria_sha256"],
                          "payload_sha256": payload["payload_sha256"],
                          "problems": payload["problems"],
                          "out": str(out), "md": str(md)}, ensure_ascii=False, indent=2))
        return 0 if payload["verdict"] == "PASS" else 1

    payload = V.run_live(auto_start=not args.no_start)
    out = E.write_json(E.EVIDENCE_DIR / "live_results.json", payload)
    md = E.write_text(E.EVIDENCE_DIR / "verify_report_self.md", V.render_markdown(payload))
    summary = {
        "stage": "live",
        "run_id": payload["observation"]["run_id"],
        "criteria_sha256": payload["criteria_sha256"],
        "payload_sha256": payload["payload_sha256"],
        "verdicts": {k: v["verdict"] for k, v in payload["results"].items()},
        "out": str(out),
        "md": str(md),
        "final_stop": payload["observation"].get("final_stop"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(v == "PASS" for v in summary["verdicts"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
