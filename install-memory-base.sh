#!/usr/bin/env bash
# Bootstrap the same installer used on Windows; no old engine dependencies.
set -euo pipefail

task_python="${PYTHON:-python3}"
if [[ "${1:-}" == "--python" ]]; then
    task_python="$2"
    shift 2
fi
if ! "$task_python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
    echo 'Personal memory base requires Python 3.12. Pass --python <path>.' >&2
    exit 1
fi
task_repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$task_repo_dir"
exec "$task_python" -m memory_base install "$@"
