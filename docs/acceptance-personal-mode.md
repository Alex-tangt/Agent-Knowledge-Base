# 个人模式 · 人的小抽查（WSL）——一个真实数据基座上的落地验收

> 目的：在**全新、隔离**的个人模式实例上，加载**固定数据基座**，完成一轮**真实使用**，
> 人可判「这就是我要的」。这是**上 npm 打包前的可行性门**（#56 / `ADR-0028`）。
>
> **不是**功能全回归（那是 `tests/unit` + `memory_agent/eval/*` 的自动套件），
> **不是**检索质量评测（那是 `retrieval_eval`）。

## 它补的三件事（此前手动方案缺的）

1. **数据基座**：`memory_agent/eval/acceptance/seed/` —— 3 条可写 KB 条目 + 2 条只读语料，内容/答案确定。
2. **明确 setup**：全新隔离实例（KB / 索引 / 只读语料 / 端口 / opencode 配置全隔离），一眼看清「要不要重建」。
3. **推荐用例**：`seed/../cases.md` —— 每条给「怎么问 / 触发哪个工具 / 判据」，不用自己编。

## 隔离设计（为什么安全）

| 维度 | 变量 / 手段 | 指向 |
|---|---|---|
| 可写基座 | `AGENT_KB_DIR` | `<root>/kb`（seed 拷贝 + `git init`） |
| 派生索引 | `MEMORY_INDEX_DIR` | `<root>/index` |
| 只读语料 | `MEMORY_READONLY_ROOTS` | `<root>/corpus`（设了即**整体替换**默认来源） |
| daemon 端口 | `MEMORY_MCP_PORT` | 默认 `8799`（**避开 8766**，那里可能有 WSL 残留 daemon） |
| opencode 配置/skill/会话 | `HOME=`（仅启动 opencode 时） | `<root>/home` |

**为什么 opencode 要换 `HOME`**：`install` 会把 MCP 注册写到 `$HOME/.config/opencode/opencode.json`。
测试若不换 HOME，就会**改写你日常的注册**、把 opencode 指到测试实例。换 HOME 后，
配置、skill、会话都落在 `<root>/home`，你的日常环境分毫不动。

**安装仍用真实 HOME**（只在启动 opencode 时换），这样 pip / HF 缓存照常命中，不重下模型。

## 前置

- WSL2（本流程按 Linux 写；**npx 需要 Windows 原生 node，不在 WSL 流程里**——WSL 走 `install.sh`）。
- 系统 Python ≥ 3.10 + `venv`：`sudo apt install -y python3-venv`。
- `git`。
- 弱网 / 大陆：首次下模型若卡在 `hf-xet`（缓存不增长），安装前 `export HF_HUB_DISABLE_XET=1`（必要时加 `HF_ENDPOINT=https://hf-mirror.com`）。

## 步骤

```bash
# 0) 全新 clone（ref 用你验收的那条；#56 未合时可用 feat/56-npx，install.sh 与 master 一致）
ROOT="$HOME/akb-acceptance"
git clone -b <ref> https://github.com/Alex-tangt/Agent-Knowledge-Base.git "$ROOT/app"

# 1) 铺数据基座 + 生成隔离环境（幂等，可重复跑）
bash "$ROOT/app/memory_agent/eval/acceptance/setup.sh" "$ROOT" 8799

# 2) 进入隔离环境（**必须 source；后续都在这个 shell**）
source "$ROOT/env.sh"

# 3) 一键安装（隔离 HOME 的注册；索引/daemon 都走隔离 env）
cd "$ROOT/app"
HF_HUB_DISABLE_XET=1 bash install.sh --opencode-home "$AKB_ACCEPT_HOME"

# 4) 接入自检
HOME="$AKB_ACCEPT_HOME" opencode mcp list      # 期望 memory-agent: connected
HOME="$AKB_ACCEPT_HOME" opencode debug skill   # 期望列出 memory-agent

# 5) 在 $ROOT/app 里启动 opencode，按 cases.md 逐条做
HOME="$AKB_ACCEPT_HOME" opencode
```

> `--no-index` 可跳过建索引（首次会下 BGE-M3 ~2.2GB）；但本验收**需要索引**。
> `--opencode-home` 是 `memory-agent install` 的开关，`install.sh` 会原样透传。

用例与结果表：`$ROOT/app/memory_agent/eval/acceptance/cases.md`。

## 结果与证据

- 把 `cases.md` 的「实测 / 结论」列填完；通过/失败与失败点记到 `docs/` 或 issue **#53** comment。
- 至少要能一句话说清：**装了没 / 用起来没 / 哪条不通**。

## 故障排查

- **`memory-agent daemon 不可用` 或检索命中不对**：端口串台（`#55`）——确认 `MEMORY_MCP_PORT`
  是该 shell 里 export 的值，且 `netstat`/`ss` 上该端口是本实例；换一个**先验证空闲**的端口重来。
- **`venv` 建不起来**：`sudo apt install python3-venv`。
- **模型下载卡住**：见「前置」的 `HF_HUB_DISABLE_XET=1`。
- **opencode 没看到 memory-agent**：确认启动时带了 `HOME="$AKB_ACCEPT_HOME"`；
  `cat "$AKB_ACCEPT_HOME/.config/opencode/opencode.json"` 应有 `mcp.memory-agent`。

## 收工

```bash
"$ROOT/app/venv/bin/python" "$ROOT/app/memory_agent/proxy.py" --stop --port "$MEMORY_MCP_PORT"
rm -rf "$ROOT"
```

## Windows（附带，非本流程主目标）

Windows 走 npx 形态：`npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir> --opencode-home <home>`。
但 Windows 上 `opencode` 用 `USERPROFILE` 而非 `HOME` 解析 `~`，**换 HOME 隔离不成立**；
要么接受改写真实注册（安装会留 `.bak-<时间戳>` 备份），要么用 `OPENCODE_CONFIG` 显式指到隔离配置。
WSL 流程无此问题。
