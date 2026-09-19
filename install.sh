#!/usr/bin/env bash
# memory-agent 一键部署（#52 / ADR-0028）——薄壳。
#
# 只做三件事：定位仓库根 → 选一个带 venv 的系统 Python → 交给统一的
# `python -m memory_agent.deploy install`（真正实现：建 venv / 装依赖 / 建索引 /
# 写 opencode 注册 / 落 skill / 起 daemon / 冒烟，全部幂等）。
#
# 用法：
#   bash install.sh                 # 一键部署
#   bash install.sh --dry-run       # 只预览，不落盘
#   bash install.sh --no-index      # 跳过建索引（首次会下载 BGE-M3 ~2.2GB）
#   bash install.sh --with-tests    # 额外装 pytest（跑 tests/unit）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="${MEMORY_INSTALL_PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "FAIL: 找不到 $PY（可设 MEMORY_INSTALL_PYTHON 指定解释器）" >&2
  exit 2
fi

if ! "$PY" -c "import venv" >/dev/null 2>&1; then
  echo "FAIL: $PY 缺少 venv 模块。Debian/Ubuntu: sudo apt install python3-venv" >&2
  exit 2
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m memory_agent.deploy install "$@"
