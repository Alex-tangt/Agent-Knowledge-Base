#!/usr/bin/env bash
# 个人模式验收——数据基座与隔离环境准备（#56 / docs/acceptance-personal-mode.md）
#
# 做三件事（幂等，重复跑安全）：
#   1) 把随包的 seed（可写 KB + 只读语料）铺到 <root>/kb 与 <root>/corpus；
#      <root>/kb 初始化为 git 仓库并做一次 seed commit（写入需要）。
#   2) 生成 <root>/env.sh：把 AGENT_KB_DIR / MEMORY_INDEX_DIR / MEMORY_READONLY_ROOTS /
#      MEMORY_MCP_PORT 指向隔离路径，绝不碰你日常的 KB / 索引 / 端口。
#   3) 打印后续步骤（安装 + 启动 opencode 的隔离 HOME）。
#
# 用法：
#   bash setup.sh [root] [port]
#   root 默认 $HOME/akb-acceptance；port 默认 8799（避开 8766，那里可能有 WSL 残留 daemon）。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED="$SELF_DIR/seed"

ROOT="${1:-$HOME/akb-acceptance}"
PORT="${2:-8799}"
ROOT="$(cd "$(dirname "$ROOT")" && pwd)/$(basename "$ROOT")"

if [ ! -d "$SEED/kb" ]; then
  echo "FAIL: 找不到 seed：$SEED/kb" >&2
  exit 2
fi

mkdir -p "$ROOT" "$ROOT/index" "$ROOT/home"

# --- 1) 可写基座（git 仓库） ---
if [ -d "$ROOT/kb/.git" ]; then
  echo "[acceptance] 复用已有基座：$ROOT/kb"
else
  rm -rf "$ROOT/kb"
  cp -r "$SEED/kb" "$ROOT/kb"
  git -C "$ROOT/kb" init -q
  git -C "$ROOT/kb" config user.name "acceptance"
  git -C "$ROOT/kb" config user.email "acceptance@example.invalid"
  git -C "$ROOT/kb" add -A
  git -C "$ROOT/kb" commit -q -m "seed: acceptance base"
  echo "[acceptance] 基座已就绪：$ROOT/kb"
fi

# --- 只读语料 ---
rm -rf "$ROOT/corpus"
cp -r "$SEED/corpus" "$ROOT/corpus"

# --- 2) 隔离环境变量 ---
cat > "$ROOT/env.sh" <<EOF
# source 本文件以进入验收隔离环境（#56 acceptance）
export AKB_ACCEPT_ROOT="$ROOT"
export AKB_ACCEPT_HOME="$ROOT/home"
export AGENT_KB_DIR="$ROOT/kb"
export MEMORY_INDEX_DIR="$ROOT/index"
export MEMORY_READONLY_ROOTS="$ROOT/corpus"
export MEMORY_MCP_PORT="$PORT"
EOF

# --- 端口占用提示 ---
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  exec 3>&- || true
  echo "[acceptance] WARN: 端口 $PORT 已被占用（换 root/port 或先释放）" >&2
fi

cat <<EOF

== 数据基座 + 隔离环境就绪 ==
  可写 KB   : $ROOT/kb      (git $(git -C "$ROOT/kb" rev-parse --short HEAD))
  只读语料  : $ROOT/corpus
  派生索引  : $ROOT/index
  隔离 HOME : $ROOT/home    （opencode 配置 + skill + 会话都落这里）
  端口      : $PORT

下一步（在**同一个 shell**里依次执行；env 必须在此 shell 内）：
  source $ROOT/env.sh
  git clone -b <ref> https://github.com/Alex-tangt/Agent-Knowledge-Base.git $ROOT/app
  cd $ROOT/app
  bash install.sh --opencode-home "\$AKB_ACCEPT_HOME"
  HOME="\$AKB_ACCEPT_HOME" opencode            # 在此目录启动，按 cases.md 逐条验收

收工（回收 daemon 内存 + 删临时目录）：
  $ROOT/app/venv/bin/python $ROOT/app/memory_agent/proxy.py --stop --port $PORT
  rm -rf $ROOT
EOF
