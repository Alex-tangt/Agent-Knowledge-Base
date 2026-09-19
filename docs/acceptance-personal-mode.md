# 个人模式落地验收 · 操作手册（WSL）

> 这是一份**照着敲就行**的操作说明：在全新、隔离的环境里装好 memory-agent，加载一份固定
> **数据基座**，按几条用例真实用一遍，判断「这就是我要的吗」。
>
> - 定位：**上 npm 打包前的可行性门**（#56 / `ADR-0028`）。**不是**功能全回归，**不是**检索质量评测。
> - 平台：**WSL2（Ubuntu）**。Windows 见文末附录（走 npx，隔离方式不同）。
> - 耗时：验收本身约 **5–10 分钟**；首次安装另含下载 BGE-M3（~2.2GB）与 CPU 建索引（数分钟）。
> - 相关文件：基座 `memory_agent/eval/acceptance/seed/`、结果表 `memory_agent/eval/acceptance/cases.md`、
>   setup 助脚 `memory_agent/eval/acceptance/setup.sh`。

---

## 0. 前置（一次性）

```bash
# 需要：git、系统 Python ≥3.10 带 venv
git --version
python3 --version
sudo apt install -y python3-venv        # 若下面装 venv 报缺少 ensurepip

# 弱网 / 大陆：首次下模型若卡住（缓存不增长），安装前打开这两行
export HF_HUB_DISABLE_XET=1
# export HF_ENDPOINT=https://hf-mirror.com
```

## 1. 一条龙（可直接整段复制）

```bash
ROOT="$HOME/akb-acceptance"
REF="feat/56-npx"                       # 合并后用 master

# (1) 全新 clone
git clone -b "$REF" https://github.com/Alex-tangt/Agent-Knowledge-Base.git "$ROOT/app"

# (2) 铺数据基座 + 生成隔离环境（幂等，可重复跑）
bash "$ROOT/app/memory_agent/eval/acceptance/setup.sh" "$ROOT" 8799

# (3) 进入隔离环境 —— 必须 source，且后续都在**这个 shell**里
source "$ROOT/env.sh"

# (4) 一键安装（隔离 HOME 写注册；索引/daemon 走隔离 env）
cd "$ROOT/app"
bash install.sh --opencode-home "$AKB_ACCEPT_HOME"

# (5) 接入自检
HOME="$AKB_ACCEPT_HOME" opencode mcp list
HOME="$AKB_ACCEPT_HOME" opencode debug skill

# (6) 在 $ROOT/app 里启动 opencode，按第 5 节用例逐条做
HOME="$AKB_ACCEPT_HOME" opencode
```

下面把每一步展开，并写清**该看到什么**。

---

## 2. 分步说明与预期输出

### 2.1 准备基座 —— `setup.sh`

```bash
bash "$ROOT/app/memory_agent/eval/acceptance/setup.sh" "$ROOT" 8799
```

应看到（节选）：

```
[acceptance] 基座已就绪：/home/<you>/akb-acceptance/kb
== 数据基座 + 隔离环境就绪 ==
  可写 KB   : .../akb-acceptance/kb      (git xxxxxxx)
  只读语料  : .../akb-acceptance/corpus
  派生索引  : .../akb-acceptance/index
  隔离 HOME : .../akb-acceptance/home
  端口      : 8799
```

- 它把 seed 铺成：`<root>/kb`（可写，**已 `git init` 并 seed commit**）+ `<root>/corpus`（只读），
  并生成 `<root>/env.sh`。**重复跑安全**（已存在则复用，不重置）。
- 若端口被占，会打印 `WARN: 端口 ... 已被占用` —— 换一个数字重跑（例：`bash setup.sh "$ROOT" 8801`）。

### 2.2 进入隔离环境 —— `source env.sh`

```bash
source "$ROOT/env.sh"
env | grep -E 'AGENT_KB_DIR|MEMORY_INDEX_DIR|MEMORY_READONLY_ROOTS|MEMORY_MCP_PORT'
```

应看到 4 个变量都指向 `<root>/...`。**这一步不能省**：安装、daemon、opencode 都靠它隔离，
绝不碰你日常的 KB / 索引 / 端口 / 注册。

### 2.3 一键安装 —— `install.sh`

```bash
cd "$ROOT/app"
bash install.sh --opencode-home "$AKB_ACCEPT_HOME"
```

应依次看到（关键字样）：

```
[deps] 创建 venv：.../akb-acceptance/app/venv
[deps] $ .../venv/bin/python -m pip install -r .../deploy-requirements.txt -e .../ragcore -e .../memory_agent
[index] 重建派生索引（首次下载 / 加载 BGE-M3，CPU 上较慢）...
[register] created：.../akb-acceptance/home/.config/opencode/opencode.json
[skill] created：.../akb-acceptance/home/.config/opencode/skills/memory-agent
[daemon] 确保 127.0.0.1:8799/mcp 在跑（首次加载模型）...
  [PASS] daemon /health — status=200
  [PASS] POST /v1/embeddings dim=1024 — dim=1024
  [PASS] 经 proxy 调 MCP — {... "ok": true, ...}
== 完成 ==
```

要点：

- **`--opencode-home "$AKB_ACCEPT_HOME"`**：把 MCP 注册与 skill 写到隔离 HOME，而不是你日常的 `~/.config/opencode`。
  （`install.sh` 会把参数原样透传给 `memory-agent install`。）
- 索引是**必需**的；别加 `--no-index`。首次会下模型；本机若已缓存则跳过下载、只加载。
- 重复跑**幂等**：register/skill 显示 `unchanged`、索引「已存在且自洽则跳过」都属正常。

### 2.4 接入自检 —— opencode

```bash
HOME="$AKB_ACCEPT_HOME" opencode mcp list      # 期望：memory-agent → connected
HOME="$AKB_ACCEPT_HOME" opencode debug skill   # 期望：列出 memory-agent（指向隔离 skills）
```

`connect` / `connected` 即可；若显示 `failed`/缺失，见第 7 节排障。

---

## 3. 隔离到底隔离了什么（一眼表）

| 维度 | 变量 / 手段 | 指向 |
|---|---|---|
| 可写基座 | `AGENT_KB_DIR` | `<root>/kb`（seed + `git init`） |
| 派生索引 | `MEMORY_INDEX_DIR` | `<root>/index` |
| 只读语料 | `MEMORY_READONLY_ROOTS` | `<root>/corpus`（设了即**整体替换**默认来源） |
| daemon 端口 | `MEMORY_MCP_PORT` | `8799`（**避开 8766**：可能有 WSL 残留 daemon） |
| opencode 配置/skill/会话 | 启动时 `HOME=` | `<root>/home` |

> **为什么 opencode 要换 `HOME`**：注册写在 `$HOME/.config/opencode/opencode.json`。不换 HOME 就会
> **改写你日常的注册**。换之后，配置/skill/会话都落 `<root>/home`，日常环境分毫不动。
> **安装仍用真实 HOME**（只在启动 opencode 时换），所以 pip/HF 缓存照常命中，不会重下模型。

---

## 4. 数据基座内容（答案早知道）

| 来源 | 条目 | 要点 |
|---|---|---|
| 可写 KB | `decisions/deploy-command` | 部署命令 = `bash install.sh` / `pwsh install.ps1` |
| 可写 KB | `topics/memory-runtime` | 检索默认 BM25 + DBSF；代目录 + CURRENT 指针 |
| 可写 KB | `projects/blue-whale/overview` | 演示项目代号「蓝鲸」，负责人 **Alice** |
| 只读语料 | `corpus/external/handbook.md` | 单次报销上限 **800 元** |
| 只读语料 | `corpus/external/oncall.md` | 夜间热线 **555-0199** |

---

## 5. 用例（在 opencode 里逐条做）

> 建议问法给了；只要触发到目标工具、满足判据即可。结果填到 `cases.md`（或直接抄下表）。
> 工具结果里能看到命中的条目 id 与 `writable` 标记。

| # | 你问（自然语言） | 应触发 | 期望 / 判据 | 结果 |
|---|---|---|---|---|
| A | 演示项目的代号是什么？负责人是谁？用 memory_search。 | `memory_search` | 命中 `projects/blue-whale/overview`（`writable=true`），答「蓝鲸 / Alice」 | |
| B | 外部域单次报销上限是多少？用 memory_search。 | `memory_search` | 命中只读条目（`writable=false`），答「800 元」 | |
| C | 记住一条：演示项目新增成员 Carol，角色是测试。用 memory_add，section=projects/blue-whale。 | `memory_add` | `status=written`（含 commit）；若 `duplicate` → 确认后 `allow_duplicate=true` 重试 | |
| D | **新开会话**：演示项目里 Carol 的角色是什么？用 memory_search。 | `memory_search` | 命中 C 的新条目，答「测试」 | |
| E | 用 memory_supersede 把 `projects/blue-whale/overview` 替代为「负责人 Bob」，section=projects/blue-whale，slug=owner-bob，type=project-knowledge。 | `memory_supersede` | **先** `confirmation_required` 预览、**不落盘**；你同意后重试 → 新条 `supersedes=projects/blue-whale/overview`，旧条 `superseded`（文件仍在） | |
| F | 演示项目负责人是谁？用 memory_search 且 **exclude_retired=true**。 | `memory_search` | 只见新条（Bob），**不见** Alice | |
| G | 调 memory_index_status 看看索引。 | `memory_index_status` | `built=true`、`consistent=true`、`entries==points` | |
| H（可选） | 上传一个 PDF/DOCX 并收录，然后召回其内容。 | `ingest_*` | 物化 `.md` → overlay 收录 → `memory_search` 召回（免重启） | |

**附注**：
- 写入后索引是 `mode=lazy`（写不刷索引，下次 `memory_search` 追平）——正常，不是失败。
- D 必须**新开会话**才叫「跨会话召回」。

---

## 6. 记录与收工

- **记录**：把第 5 节结果列 / `cases.md` 填完；通过与否 + 失败点写进 `docs/` 或 issue **#53** comment。
  一句话能说清即可：**装了没 / 用起来没 / 哪条不通**。
- **收工**（回收 daemon 内存 + 删临时目录）：

```bash
"$ROOT/app/venv/bin/python" "$ROOT/app/memory_agent/proxy.py" --stop --port "$MEMORY_MCP_PORT"
rm -rf "$ROOT"
```

---

## 7. 故障排查

| 现象 | 多半原因 | 处理 |
|---|---|---|
| 工具报 `memory-agent daemon 不可用` / 检索命中的内容不对 | 端口串台（#55）：opencode 那个 shell 没有 `MEMORY_MCP_PORT`，proxy 回落 8765 | 确认 `source env.sh` 后再起 opencode；`ss -ltnp \| grep "$MEMORY_MCP_PORT"` 看是不是本实例；或换一个**先验证空闲**的端口重来 |
| `opencode mcp list` 里没有 memory-agent / failed | 启动 opencode 时没带 `HOME="$AKB_ACCEPT_HOME"` | 用 `HOME="$AKB_ACCEPT_HOME" opencode ...`；核对 `cat "$AKB_ACCEPT_HOME/.config/opencode/opencode.json"` 有 `mcp.memory-agent` |
| 建 venv 失败 / `ensurepip` 缺失 | 缺系统 venv 支持 | `sudo apt install -y python3-venv` 后重跑 |
| 模型下载卡住（缓存不增长） | `hf-xet` 弱网 | `export HF_HUB_DISABLE_XET=1`（必要时加 `HF_ENDPOINT=https://hf-mirror.com`）后重跑 |
| 建索引很慢 / 重复「首次下载」 | CPU 建索引 + 首次下模型；或换了 HOME 导致缓存未命中 | 正常等；**安装别换 HOME**（只有启动 opencode 才换） |
| `memory_add` 返回 `duplicate` | 命中了近似条目 | 让 agent 用 `allow_duplicate=true` 重试，或换措辞 |
| E 直接落盘、没给预览 | 没遵守「破坏性先预览」 | 明确要求：先 `confirm=false` 预览，你同意后再 `confirm=true` |
| 想从头再来 | — | 先收工，再 `rm -rf "$ROOT"`，从第 1 节重跑 |

---

## 附录 A · Windows（附，非本流程主目标）

Windows 走 npx 形态：

```powershell
$root="$env:USERPROFILE\akb-acceptance"; $port=8799
# 隔离 env（PowerShell）：
$env:AGENT_KB_DIR="$root\kb"; $env:MEMORY_INDEX_DIR="$root\index"
$env:MEMORY_READONLY_ROOTS="$root\corpus"; $env:MEMORY_MCP_PORT="$port"
npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> "$root\app" --opencode-home "$root\home"
```

但 Windows 上 `opencode` 用 `USERPROFILE` 而非 `HOME` 解析 `~`，**换 HOME 隔离不成立**：
要么接受改写真实注册（安装会留 `.bak-<时间戳>` 备份），要么用 `OPENCODE_CONFIG` 显式指到隔离配置。
WSL 流程无此问题。

## 附录 B · 这套验收验的是什么 / 不验什么

- **验**：从已打包入口装得起来（WSL = `install.sh`；Windows = npx）→ 在固定基座上真实用得起来
  （读 / 写 / 生命周期 / 取新弃旧 / 只读语料 / 索引自洽）→ 人可判。
- **不验**：功能全回归（`tests/unit` + `memory_agent/eval/*` 自动套件）、检索质量（`retrieval_eval`）、
  真 npm publish（`#54`）、多租户/共享面（冻结区）。
