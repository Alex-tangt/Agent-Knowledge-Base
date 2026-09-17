# 0028 一键部署与跨平台（WSL/Linux + Windows）

Status: **accepted**（2026-09-17 owner 拍板）。

Relates: **ADR-0012**（skill 位置）、**ADR-0013**（daemon / proxy 拓扑）、**ADR-0024**（包化 / editable）、
**ADR-0027**（解析专用环境）、#19 / #41 / #43（daemon / proxy / connect）；#50 / #51。

## 背景

个人模式功能票已闭环。final test 的目标形态是：**在全新 WSL + 全新 opencode 上一次装好、功能全通**。
当前部署面缺口：

- **无一条命令安装**：只有 README 手敲步骤，且是 Windows 写法。
- **POSIX 守护缺口**：`proxy._spawn_daemon` 用 `DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`
  （`memory_agent/proxy.py:106`）——**Linux 上取不到 = 0，且无 `start_new_session`** → WSL 上 daemon
  生命周期不可靠。
- **依赖源割裂**：`legal_web/requirements.txt` 是"权威依赖集"，而 `legal_web` 已被声明为**排除**。
- **MCP 注册非一键**：`connect.py` 只**核验 + 打印 patch**，不写 opencode 配置。
- **模型/离线**：BGE-M3（~2.2GB）首次下载与预热未入流程（`ensure_hf_offline()` 已处理缓存感知）。
- 运行面大体可移植：`connect.py` 已兼容 `venv/bin/python`，`_kill_pid` 有 POSIX 分支。

## 决策

- **D1 支持平台 = WSL/Linux + Windows**：两条安装脚本（`install.sh` / `install.ps1`）；运行时保持跨平台；
  **修 POSIX daemonization**（Linux 用 `start_new_session=True`）。
- **D2 一键部署 v1 = `git clone` + 一条命令**：脚本内部调统一的 **`memory-agent install`** CLI（**幂等**），依次
  建 venv → 装依赖 → editable 装 `ragcore` + `memory_agent` → 建索引（模型预热 / 离线提示）→
  写 opencode MCP 注册 → 落位 skill → 起 daemon → 冒烟。**不引入新数据位置**：索引/数据仍在仓库内（gitignored），
  与现有 `ROOT_DIR` 架构一致。
- **D3 依赖源 = `memory_agent` 独立 deploy requirements**：一份精简、完整的运行时依赖集，
  **与 `legal_web` 解耦**（`legal_web/requirements.txt` 不再作产品权威依赖源）。
- **D4 opencode MCP 注册 = 脚本直接写入**：写 `~/.config/opencode/opencode.json` 的 `mcp.memory-agent`，
  **幂等 + 备份 + `--dry-run`**；skill 落位 `~/.config/opencode/skills/memory-agent/`。
- **D5 final test = 全新环境端到端**：在**全新 WSL + 全新 opencode** 上跑：一键装 → 冒烟
  （daemon / MCP / embeddings / 索引自洽）→ 个人模式验收套件（无模型 + 需模型两层）→
  写记忆 / 召回 / 生命周期 / 上传收录。**验收的是"新环境能装能用"，不是当前机器复跑。**
- **D6 v2（真·插件化）= 后续**：发包（私有索引 / `uvx --from git+…`）+ 数据/索引迁 **XDG** +
  解耦 `ROOT_DIR` → **另开票**，不在本次。

## 理由

- v1 改动小、与仓库锚定架构一致，能**最快回答"新环境能不能用"**；v2 的打包 / XDG 是**正交**的后续工作，
  不应阻塞 final test。
- 解耦 `legal_web` 依赖源既纠正语义矛盾，也是 v2 打包的前置。

Considered options：

- **v1 `clone` + 一条命令（采用）**——快、稳、可立即验收。
- **v2 直接打包 / XDG（暂缓）**——目标更彻底，但要先解耦 `ROOT_DIR` + 发包，周期长、final test 延后。
- **维持 README 手敲（弃）**——不算"一键"，新环境易漏步。

## Consequences

- **新功能线 = 部署 + 跨平台**：票 `部署线 v1` → `全新 WSL final test`（blocked）；`v2 插件化`（backlog）。
- 需修 `memory_agent/proxy.py::_spawn_daemon`（POSIX）；`connect.py` 的注册逻辑可作 D4 写入的基础。
- 索引/数据仍在仓库内 ⇒ **clone 仍是前提**（v2 才去掉）；v2 需独立 ADR / 票。
- 解析专用环境（ADR-0027 D7）在部署脚本里作为**可选步骤 / 文档**（`venv-parse`），不进主 venv。
- 与 ADR-0012 / 0013 / 0024 / 0027 一致；本 ADR 不修改它们。
