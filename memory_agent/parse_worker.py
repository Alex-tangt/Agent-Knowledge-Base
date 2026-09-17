"""解析工作进程：文档 -> 节 JSON（在**专用解析环境**里跑）。

主环境（未装 Docling）通过子进程调用它完成解析（ADR-0027 D7：解析只装进独立环境，
产物交回主环境）。stdout **只输出结果 JSON**，诊断走 stderr；import 轻量——真引擎在
`main()` 里才 import，故本模块可在只有 stdlib 的解释器里被 -m 定位。

    <parse-env-python> -m memory_agent.parse_worker --file <path> [--parser docling|pypdf]
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory-agent-parse-worker",
        description="用本环境的解析引擎把文档切成节，输出 JSON（stdout）。",
    )
    parser.add_argument("--file", required=True, help="PDF / DOCX 路径")
    parser.add_argument("--parser", choices=["docling", "pypdf"], default=None,
                        help="显式引擎；缺省按可用性自动（Docling 优先，pypdf 兜底）")
    args = parser.parse_args(argv)

    try:
        from memory_agent.parse import parse_document
        from memory_agent.parse.engines import available_parsers

        sections = parse_document(args.file, parser=args.parser)
        engine = args.parser or (
            "docling" if "docling" in available_parsers() else "pypdf"
        )
    except Exception as exc:  # noqa: BLE001 - 如实报错给调用方，消息走 stderr
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1

    payload = {
        "file": args.file,
        "engine": engine,
        "sections": [
            {"title": section.title, "level": section.level, "markdown": section.markdown}
            for section in sections
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
