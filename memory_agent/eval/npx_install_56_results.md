# #56 npx 薄包装安装器 —— 验收证据

> 2026-09-19，Windows 本机。对齐 `docs/adr/0028`（D2 一条命令 / D6 免发布形态），
> 产品运行时与部署编排来自 #52（`memory_agent/deploy.py`）。本票**只加薄包装**，
> 不改 Python 运行时、不改 `install.sh` / `install.ps1` 语义、不迁 XDG（真·打包归 v2 `#54`）。

## 交付物

- `package.json`（root，`name=agent-knowledge-base`；`bin` = `agent-kb` + `agent-knowledge-base`；
  `files=[bin/,package.json]` → npx 只拉包装器，源码由包装器自取）。
- `bin/agent-kb.js`（Node stdlib，零第三方依赖；`chmod +x` 入库）。
- `docs/deploy-quickstart.md`（新增「npx 安装」小节）。
- `AGENTS.md` 地图同步（目录布局 + 一键部署 bullet）。

## 包装器行为（与 ticket 对齐）

1. 目标目录 = 第一个位置参数 / `--dir` / `AGENT_KB_DIR` / 默认 `./agent-knowledge-base`。
2. 取源码 = `git clone`（需 git；**不做 tarball 兜底**——git 是记忆写入的硬依赖，
   一次写入一个 commit）。已存在则 `git fetch` + 检出 + `--ff-only`，**幂等**；
   目标目录非空且非本仓库 → 明确报错（不覆盖）。
3. 调用安装 = `python -m memory_agent.deploy install <透传参数>`（`cwd=仓库根`、
   `PYTHONPATH=仓库根`）——与 `install.sh` / `install.ps1` **同一入口**。
4. 退出码透传；缺 git / Python 给明确指引。
5. `--dry-run` 不创建目标目录：克隆到临时目录跑安装器 dry-run，随后删除临时目录。

环境：node v24.14.1 / npm 11.12.1 · git 2.49.0.windows.1 · Python 3.12.2。
被装的 ref：`feat/56-npx`（包装器）；源码默认分支 `master`（已含 #52）。

## 测试矩阵

| # | 场景 | 命令要点 | 结果 |
|---|------|----------|------|
| 1 | `--help` / `--version`（本地 + 经 npx） | `npx --yes github:…#feat/56-npx --help` | ✅ 帮助 / `0.1.0`，exit 0 |
| 2 | `--dry-run` 经真 npx（GitHub source） | `npx --yes github:…#feat/56-npx <dir> --dry-run --opencode-home <tmp>` | ✅ 临时克隆 → 安装器 dry-run → 删临时；**目标目录未创建**；exit 0 |
| 3 | 端口隔离透传 | 同上 + `MEMORY_MCP_PORT=8766` | ✅ `[daemon] … 127.0.0.1:8766/mcp` |
| 4 | 真装（npx，跳过模型重活） | `npx --yes github:…#feat/56-npx <dir> --no-index --no-daemon --no-smoke --cpu-torch --opencode-home <tmp>` | ✅ venv + deps + 注册 + skill；exit 0 |
| 5 | 幂等复跑 | 同 4 + `--skip-deps` | ✅ `复用已有源码` · `[register] 已是最新（幂等）` · `[skill] unchanged` |
| 6 | 已有 clone 的 origin 不匹配 | `--dir <非本仓库非空目录>` | ✅ 明确报错，exit 1 |
| 7 | 退出码透传 | 传非法安装参数 `--not-a-real-flag` | ✅ 安装器 argparse 报错，exit 2 |
| 8 | `--ref` | `--ref master` | ✅ 检出 master |

## 关键观察（真装产物）

- 注册写入隔离的 `--opencode-home`：
  `mcp.memory-agent.command = [<新 clone>\venv\Scripts\python.exe, <新 clone>\memory_agent\proxy.py]`
  → 指向**新部署**而非主树；skill 落到 `<tmp>/.config/opencode/skills/memory-agent/SKILL.md`。
- 新 venv `python -c "import memory_agent, ragcore"` → `import-ok`；
  `venv\Scripts\memory-agent.exe` 存在。
- Windows 上 `--cpu-torch` 下载 `torch 2.14.0+cpu`（124 MB）；**默认（不显式指定）在 Windows
  走 PyPI 的 CUDA 轮子**——`deploy.py::_use_cpu_torch` 只在 `sys.platform.startswith("linux")`
  时为真。若希望 Windows 个人模式也默认 CPU，是 `#52` 范围的小改进（另议），本票不动。

## 未在本会话跑的一段（gate）

- **索引 + daemon + 冒烟**（`--no-index --no-daemon --no-smoke` 之外的重活）**未跑**：
  需要再加载一份 BGE-M3（~3.9GB）+ 在 CPU 上重建约 188 条条目的索引（分钟级），
  且本机已有主树 daemon（8765）在服务当前 opencode 会话（`#55` 串台场景）。
  #52 的 WSL final test（`one_click_deploy_52_results.md`）已验证该段；包装器只负责
  「取源码 + 同一入口」，对该段是透明的。**是否在本机补跑全量 npx 安装 → 待 owner 放行**
  （建议 `MEMORY_MCP_PORT=8766` + 独立 `--opencode-home` 隔离）。

## 复现命令（Windows）

```powershell
# 1) 干跑（不落盘）
npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir> --dry-run

# 2) 真装（注册/端口都隔离，避免污染日常 opencode 与串台主树 daemon）
$env:MEMORY_MCP_PORT="8766"
npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir> --opencode-home C:\tmp\oc
```
