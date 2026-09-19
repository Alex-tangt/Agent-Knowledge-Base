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
| 9 | 全量安装（index + daemon + smoke） | 同 4 去掉 `--no-*`，`MEMORY_MCP_PORT=8799` | ✅ 见下「全量安装」 |

## 关键观察（真装产物）

- 注册写入隔离的 `--opencode-home`：
  `mcp.memory-agent.command = [<新 clone>\venv\Scripts\python.exe, <新 clone>\memory_agent\proxy.py]`
  → 指向**新部署**而非主树；skill 落到 `<tmp>/.config/opencode/skills/memory-agent/SKILL.md`。
- 新 venv `python -c "import memory_agent, ragcore"` → `import-ok`；
  `venv\Scripts\memory-agent.exe` 存在。
- Windows 上 `--cpu-torch` 下载 `torch 2.14.0+cpu`（124 MB）；**默认（不显式指定）在 Windows
  走 PyPI 的 CUDA 轮子**——`deploy.py::_use_cpu_torch` 只在 `sys.platform.startswith("linux")`
  时为真。若希望 Windows 个人模式也默认 CPU，是 `#52` 范围的小改进（另议），本票不动。

## 全量安装（index + daemon + smoke）

经 npx 在 `my-kb`（复用已装 venv/deps）补跑模型重活：

- **索引**：`gen-1`，**145 条**（28 可写 KB + 117 只读 = 该 clone 自身的 `.md`），
  `index_dir` / `kb_dir` 都指向本部署，`readonly_complete=true`。
- **daemon**：`proxy --ensure --port 8799` —— 日志为 **`starting daemon`**（不是 `already up`），
  随后 `/health` ok。
- **冒烟 3/3 PASS**：`/health status=200` · `POST /v1/embeddings dim=1024` ·
  经 proxy 调 MCP `{"ok": true, "tools": 10, "gen": "gen-1", "hits": 3}`。
- **归属核验**：`daemon-8799.pid = 10164`，`netstat` 显示 `127.0.0.1:8799 LISTENING`（同一 PID）
  → 确为**本部署**的 Windows daemon；`proxy --stop` 后 health DOWN、端口关闭、PID 文件移除。

### ⚠️ 踩到 #55 串台（真实命中）

第一次全量跑用建议端口 **8766**，安装器报 **`daemon already up at 127.0.0.1:8766`**，
但 `--stop` 报 **PID 文件缺失**、`netstat` 里 8766 **没有 Windows LISTENING**（只有 client 侧
`ESTABLISHED`）——查明是 **WSL2 `Ubuntu-22.04` 里残留的 daemon**（上一会话 #53 final test 留下），
经 WSL localhost 转发被 Windows 的 `127.0.0.1:8766` 命中。**该次 daemon/冒烟结果不可信，已作废**；
换 8799（先验证无监听）后重跑才是本部署。这正是 `#55`（daemon 身份核验，已 backlog）活生生的
复现：`/health` 识别不出「这是哪一个部署的 daemon」。**本票不动 #55**，仅记录。

## 未做 / 边界

- Windows 默认（不显式 `--cpu-torch`）会装 PyPI 的 **CUDA** torch（`deploy.py::_use_cpu_torch`
  仅 Linux 默认 CPU）。个人模式其实只需 CPU；是否把 Windows 也默认 CPU 属 `#52` 范围的小改进，另议。
- WSL 无原生 node（`npx` 是 Windows shim），本票平台 = Windows（ticket 已界定）。

## 复现命令（Windows）

```powershell
# 1) 干跑（不落盘）
npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir> --dry-run

# 2) 真装（注册/端口都隔离，避免污染日常 opencode 与串台其它 daemon）
#    选端口前先确认无监听（netstat -ano | findstr :<port>；注意 WSL2 转发也会占 localhost）
$env:MEMORY_MCP_PORT="8799"
npx --yes github:Alex-tangt/Agent-Knowledge-Base#<ref> <dir> --opencode-home C:\tmp\oc
```
