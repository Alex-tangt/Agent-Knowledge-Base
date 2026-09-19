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
# pwsh install.ps1              # Windows
```

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

## 4. 在 opencode 里手工验收（A–G）

> WSL 里 opencode 原生二进制在 `~/.opencode/bin/opencode`（PATH 上的 `opencode`
> 可能是 Windows shim）。在**装好的仓库目录**里启动 opencode 会话，即可看到
> `memory-agent` 的 MCP 工具与 skill。

**A. 写入记忆** —— 对 agent 说：
> 「记住一条：Agent-Knowledge-Base 的一键部署命令是 `bash install.sh`（Windows 用 `install.ps1`），标签 agent-knowledge-base，写到 topics。」
- 预期：agent 调用 `memory_add`；返回 `status=written`（写入全局 KB 并单文件 git commit）。
- 若返回 `status=duplicate`：说明命中了近似条目 —— 换措辞，或明确要求 `allow_duplicate`。

**B. 跨会话召回** —— **新开一个会话**：
> 「我的一键部署命令是什么？」
- 预期：agent 调 `memory_search`（或 `exclude_retired=true`），命中 A 写入的条目。

**C. 读回全文**：
> 「用 memory_get 把刚才那条完整读出来。」
- 预期：返回该条目的**真实 Markdown**（含 frontmatter）。

**D. 生命周期 / 取新弃旧**：
> 「把那条部署命令改成『WSL 用 `bash install.sh --with-tests`』，用 memory_supersede 替代旧的。」
- 预期：agent 先返回 `confirmation_required` 预览（**不落盘**）；你同意后它才以确认重试 →
  新条目建立 + 旧条目标 `superseded`（旧文件保留）。
- 再问部署命令：应只看到新条目（`exclude_retired` 生效）。

**E. 索引状态自洽**：
> 「调 memory_index_status 看看索引。」
- 预期：`built=true`、`consistent=true`、`entries==points`。

**F. 只读语料检索**：
> 「按 ADR-0028，这套部署支持哪些平台？」
- 预期：命中本仓 `docs/adr/` 的**只读**条目（`writable=false`，不可写）。

**G. 文档上传收录（可选，需解析环境）**：
```bash
python -m venv venv-parse
venv-parse/bin/python -m pip install docling pypdf python-dotenv
venv/bin/python -m memory_agent.ingest <your.pdf> --label mydocs \
  --parse-python venv-parse/bin/python
```
- 预期：物化 `.md` 到 `memory_agent/imports/`，写 overlay（只读语料域）；下一次
  `memory_search` 的惰性刷新即召回（免重启）。也可让 agent 调 `memory_ingest_list` 查看收录全景。

## 5. 常见问题

- **`venv` 建失败 / `ensurepip` 缺失** → `sudo apt install python3-venv`。
- **模型下载卡住** → 见「前置」的 `HF_HUB_DISABLE_XET=1`。
- **端口 8765 被占** → `MEMORY_MCP_PORT` 换端口（安装与 proxy 都读）。
- **daemon 不退** → 正常：单实例常驻；`proxy.py --stop` 手动回收，不做自动空闲卸载（ADR-0013 D2）。
- **想撤销安装** → 删除 clone 目录；opencode 注册与 skill 在 `~/.config/opencode/`（安装时会
  自动备份旧 `opencode.json` 为 `opencode.json.bak-<时间戳>`）。
