# 部署快速上手 + 手工验收清单

> 面向「亲手体验」：一条命令装好 memory_agent，再在 opencode 里手工验证几个案例。
> 决策依据 `docs/adr/0028`；实现入口 `memory_agent/deploy.py`。

## 1. 前置

- **系统 Python ≥ 3.10**。
- **Debian/Ubuntu/WSL：`sudo apt install python3-venv`**（缺 `venv`/`ensurepip` 时安装脚本会明确提示并退出）。
- **弱网 / 中国大陆**：首次下载模型若卡在 `hf-xet`（HF 缓存长时间不增长），用
  `HF_HUB_DISABLE_XET=1 bash install.sh` 走经典 HTTP；必要时叠加
  `HF_ENDPOINT=https://hf-mirror.com`。二者是进程环境变量，安装与 daemon 都继承。
- **opencode**：MCP 客户端（安装器会把 `mcp.memory-agent` 写进 `~/.config/opencode/opencode.json`）。

## 2. 一键安装

```bash
git clone https://github.com/Alex-tangt/Agent-Knowledge-Base.git
cd Agent-Knowledge-Base
bash install.sh                 # Linux / WSL / macOS
# powershell -File install.ps1  # Windows（系统自带 PowerShell 5.1 即可；装了 pwsh 也行）
```

> Windows：`powershell -File install.ps1` 与 `pwsh install.ps1` 等价，前者不要求装 PowerShell 7。

一条命令**幂等**完成：

建 venv → 装 `memory_agent/deploy-requirements.txt`（**与 `legal_web` 解耦**）→
editable 装 `ragcore` + `memory_agent` → 建派生索引（首次下 BGE-M3 ~2.2GB）→
**写 opencode MCP 注册**（`~/.config/opencode/opencode.json`，幂等 + 备份 + `--dry-run`）→
落位 skill（`~/.config/opencode/skills/memory-agent/`）→ 起共享 daemon →
冒烟（`/health` · `POST /v1/embeddings` dim=1024 · 经 proxy 调 MCP）。

常用开关：`--dry-run` · `--no-index` · `--no-daemon` · `--no-smoke` · `--with-tests` · `--force-index`。
已在 venv 内时等价入口：`memory-agent install`。

> 首次建索引在 CPU 上较慢（本机 ~3 分钟/16 条，取决于条目长度与核数）。之后重跑会
> 检测到「索引已自洽」而跳过（`--force-index` 可强制重建）。

### npx 安装（免 clone；#56）

不想先 `git clone` 时，用 npx 薄包装（`npx github:` 免 npm 发布）：

```powershell
# 在干净目录：取源码到 my-kb → 跑同一个 `memory-agent install`
npx --yes github:Alex-tangt/Agent-Knowledge-Base my-kb

# 先干跑（参数原样透传）；再真装
npx --yes github:Alex-tangt/Agent-Knowledge-Base my-kb --dry-run
npx --yes github:Alex-tangt/Agent-Knowledge-Base my-kb --no-index --opencode-home C:\tmp\oc
```

- **薄壳**：把源码 `git clone` 到 `<目标目录>`（已存在则 `fetch` + 快进，**幂等**），再调用与
  `install.sh` / `install.ps1` **相同**的入口 `python -m memory_agent.deploy install`；
  不改 Python 运行时与安装语义。
- **包装器选项**：`--dir <path>` · `--repo-url <url>` · `--ref <branch|tag>` · `--force`；
  其余参数原样透传给安装器（`--` 之后的也一并透传）。默认目标目录 `./agent-knowledge-base`。
- **前置**：**node ≥ 18 + git + Python ≥ 3.10**。git 是记忆写入的硬依赖（一次写入一个 commit），
  所以不做 tarball 兜底。
- **`--dry-run` 不落盘**：不创建目标目录、不建 venv、不写注册；它把源码克隆到**临时目录**跑
  安装器的 dry-run，结束后删除。
- **固定 ref**：`npx github:...#<ref>` 固定的是**包装器**；源码默认取默认分支，用 `--ref <ref>` 对齐。
- **多份部署**：同机第二个部署要隔离端口与注册——`MEMORY_MCP_PORT=<另一端口>` +
  `--opencode-home <目录>`（见下「常见问题」）。

## 3. 就绪探测（装完自检）

```bash
# daemon 健康
curl -s http://127.0.0.1:8765/health
# OpenAI 兼容 embeddings（dim=1024）
curl -s -X POST http://127.0.0.1:8765/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":"部署自检","model":"BAAI/bge-m3"}' | head -c 200
# 代理运维
venv/bin/python memory_agent/proxy.py --status   # ok / down
venv/bin/python memory_agent/proxy.py --stop     # 回收 ~3.9GB（手动）
venv/bin/python memory_agent/proxy.py --ensure   # 幂等拉起
```

## 4. 落地验收（人的小抽查，带数据基座）

写记忆 / 新会话召回 / supersede 取新弃旧 / 只读语料 / 索引自洽 —— 这一轮落地验收
已独立成篇，带**固定数据基座 + 隔离 setup + 逐条判据**：

**→ [`docs/acceptance-personal-mode.md`](acceptance-personal-mode.md)**（WSL 流程；
基座在 `memory_agent/eval/acceptance/`）。

> 旧版的「A–G 手工清单」已被它取代（不再给没有基座、没有判据的裸步骤）。
> 文档上传收录（需独立解析环境 `venv-parse`）作为其中的可选用例 H。

## 5. 常见问题

- **`venv` 建失败 / `ensurepip` 缺失** → `sudo apt install python3-venv`。
- **模型下载卡住** → 见「前置」的 `HF_HUB_DISABLE_XET=1`。
- **端口 8765 被占** → `MEMORY_MCP_PORT` 换端口（安装与 proxy 都读）。
- **同一台机器同时跑 Windows + WSL（或多份 clone）= 两个部署，各有一张基表**。个人模式的形状
  就是「单 daemon + 单基表」（一个部署持一份引擎/索引/可写 KB），**不需要端口隔离**；但
  `/health` 探测识别不出「这是哪一个部署的 daemon」，第二个部署会误连第一个（典型：WSL2
  localhost 转发到宿主 Windows 的 8765）。**做法**：第二个部署显式设
  `MEMORY_MCP_PORT=<另一端口>`（安装、proxy、opencode 会话都继承该环境变量）。
- **daemon 不退** → 正常：单实例常驻；`proxy.py --stop` 手动回收，不做自动空闲卸载（ADR-0013 D2）。
- **升级** → `git pull` 后**重跑** `bash install.sh`（幂等：依赖补齐、索引自洽则跳过、注册/skill 差分才写）。
  用 npx 装的同理再跑一次 `npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir>`（会 fetch + 快进）。
- **卸载** → `memory-agent uninstall`（`venv/bin/python -m memory_agent.deploy uninstall`）：
  停 daemon + 移除 opencode 注册 `mcp.memory-agent` + 移除 skill（**幂等 + 备份 + `--dry-run`**）。
  它**不删 clone**——彻底移除运行时（venv / 索引 / 本仓 KB）就再删掉 clone 目录。
  只想撤销注册不动 skill：`--no-skill`；只想看计划：`--dry-run`。
