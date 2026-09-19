# 一键部署 v1 + 全新 WSL final test 结果（#52 / #53）

- 日期：2026-09-19；分支 `feat/52-deploy`。
- 环境：本机 **WSL2 Ubuntu-22.04.5**（`Linux 6.6.87.2-microsoft-standard-WSL2`），
  从 GitHub **全新 clone** 到 `~/akb-final-test`（无 venv / 无 HF 缓存 / 无 opencode 注册 /
  无 skill），系统 Python 3.10.12。
- 结论：**#52 一键部署通过**（一条命令、幂等、冒烟全绿、opencode 真机接入）；
  **#53 Tier1 全绿，Tier2 全绿（retrieval_eval 因语料口径用过滤集，见「已知限制」）**；
  手工使用 A–G 全通过。过程中发现并修复 5 个真实缺陷（见下），全部带回归测试。

## 环境快照

| 项 | 值 |
|---|---|
| OS | Ubuntu 22.04.5 LTS (WSL2) |
| Python | 3.10.12（venv 内） |
| torch | 2.14.0+cpu（CPU 轮子，D1） |
| sentence-transformers / transformers | 5.4.1 / 4.57.6（#43 固定组合） |
| mcp / qdrant-client / fastembed | 2.2.0 / 1.19.1 / 0.8.0 |
| 索引 | `gen-1`，139 条（32 KB + 107 只读本仓文档），自洽 |
| opencode | 1.14.48（原生 Linux，`~/.opencode/bin/opencode`） |
| git HEAD | `e5b49ac` |

## 一键安装（#52）

```
git clone https://github.com/Alex-tangt/Agent-Knowledge-Base.git   # 分支 feat/52-deploy
bash install.sh --with-tests
```

结果（`install.sh` → `memory-agent install`）：

- venv 创建 + 依赖安装（`memory_agent/deploy-requirements.txt`，与 `legal_web` 解耦）。
- 索引重建（首次下载 / 加载 BGE-M3，CPU）。
- `[register] created` → `~/.config/opencode/opencode.json` 写入 `mcp.memory-agent`。
- `[skill] created` → `~/.config/opencode/skills/memory-agent/SKILL.md`。
- `[daemon] daemon ready` → `127.0.0.1:8766/mcp`（见「已知限制」为何是 8766）。
- 冒烟：`/health` ✅ · `POST /v1/embeddings` dim=1024 ✅ · 经 proxy 调 MCP
  `{"ok": true, "tools": 10, "gen": "gen-1", "hits": 3}` ✅。
- **幂等**：同命令第二次 → register/skill `unchanged`、index「已存在且自洽，跳过」、
  冒烟仍全绿，`rc=0`。

## opencode 真机校验

| 命令 | 结果 |
|---|---|
| `opencode debug skill` | 列出 `memory-agent`（指向落位 SKILL.md） |
| `opencode mcp list` | `memory-agent` → **connected**（指向本仓 `venv/bin/python …/proxy.py`） |
| `opencode debug config` | 解析出的配置含 `mcp.memory-agent` |

## Tier 1（无新模型；各自独立端口/沙箱）

| 套件 | 结果 |
|---|---|
| `pytest tests/unit` | **457 passed / 3 skipped / 3 xfailed** |
| `config_independence_22.py` | PASS（`passed: true`） |
| `gateway_authz_32.py` | exit 0 |
| `personal_mode_36.py` | **15/15** |
| `shared_service_41.py` | **27/27**（DeepTutor 侧校验 SKIP，见限制） |
| `agent_loop_42.py` | **15/15** |
| `ingest_51.py` | **12/12** |
| `mcp_install_smoke_26.py --source-kb <KB>` | **9/9** |

## Tier 2（需模型）

| 套件 | 结果 |
|---|---|
| `bge_m3_embeddings_43.py` | exit 0（含 daemon 冷启动、dim=1024、单位范数、批量、错误码、authn） |
| `write_path_sandbox.py`（真实 KB 只读克隆） | **25/25** |
| `retrieval_eval.py --mode hybrid-rerank`（过滤集，13 题） | **recall@1/3/5/10 = 1.0000，nDCG@10 = 1.0000，MRR = 1.0000，misses 0/13**，`run_hash=e33d077a459326a3` |

## 真实使用 A–G（经 stdio proxy 的 MCP）

`memory_add`（写入并单文件 commit）→ 新会话 `memory_search` 召回 → `memory_get` 读回全文 →
`memory_supersede` 预览（`confirmation_required`，不落盘）→ confirm 落盘（新旧双向标注）→
`exclude_retired` 取新弃旧 → `memory_index_status` 自洽（139/139）→ 只读语料检索命中本仓
`docs/adr/0028`。**8/8 通过**。脚本：`real_usage.py`（临时，逻辑同 `agent_loop_42`）。

## 本轮发现并修复的缺陷（均含回归测试）

| # | 缺陷 | 修复 | 提交 |
|---|---|---|---|
| 1 | `settings.KB_DIR` 硬编码 Windows 路径 → Linux 上写入落到仓库内伪路径 | 默认改 `~/.config/opencode/knowledge`（Windows 展开后不变） | `ca2c507` |
| 2 | 模型服务硬编码 `local_files_only=True` → 全新机器永远下不到模型 | 构造时按 `ensure_hf_offline()` 结果决定 | `f28fe0e` |
| 3 | `is_model_cached` 只看 snapshot 目录存在 → 半成品缓存误判「已缓存」→ 切离线无法自愈 | 要求 `config.json` + 权重 + tokenizer 标记，且跟随 `refs/main` | `f7ee860` |
| 4 | 写前去重用 hybrid 融合分比余弦阈值 → **全新 unique 条目被误判 duplicate、拒绝写入** | `_find_duplicates` 改走 `store.search_dense`（与 `shared_writer` 一致） | `c83a8bc` |
| 5 | 逐模型 HF 离线冲突：embed 已缓存置全局离线 → 未缓存的 reranker 永远下不到 | `ensure_hf_offline` 可撤销「自己置的」离线；embed/reranker 在构造点各自决策 | `e5b49ac` |

其它随票修复：POSIX 守护（`start_new_session`）、`memory-agent install` CLI 自举、opencode 注册写入、
MCP 冒烟传完整 env（SDK `StdioServerParameters` 默认过滤环境）、stub 套件跨平台 + 固定确定性检索口径。

## 已知限制（如实记录）

1. **DeepTutor 不在 WSL**：`shared_service_41` 中依赖 `import deeptutor` 的校验改为 **SKIP**（不计失败）；
   其 MCP/写入/审计断言仍全跑。`bge_m3_embeddings_43` 的 DeepTutor `EmbeddingClient` 端到端同样 SKIP。
2. **`retrieval_eval` 全量集无法在本环境跑**：评测集（51 题）钉在 **gen-2 + 三仓库只读语料**
   （含 `kg-triplet-sft`，本机缺失）上；本环境只有全局 KB + 本仓文档，37 个 gold id 不在索引。
   故用**过滤集**（gold 全在语料内的 13 题）跑通同一链路以验证「BGE-M3 + BM25 + reranker」
   端到端（reranker 首次下载 + 加载也由此验证）。**过滤集分数不可与基线比较**。
3. **WSL2 与宿主 Windows daemon 串台**：WSL2 localhost 转发会让 WSL 内 `127.0.0.1:8765`
   命中**宿主 Windows** 上正在跑的 memory-agent daemon；proxy 的 `/health` 探测无法区分，
   于是不启自己的 daemon、MCP 打到错误索引。**本轮用 `MEMORY_MCP_PORT=8766` 隔离**。
   个人模式是「单 daemon + 单基表」，同机多部署用端口隔离即可，**非数据模型缺陷** →
   **已降 backlog（#55）**，重启条件见该票。
4. **`hf-xet` 弱网**：全新环境首次下载卡在 xet（缓存不增长）→ 用 `HF_HUB_DISABLE_XET=1`
   走经典 HTTP（已写入 `memory_agent/README.md` 与 `docs/deploy-quickstart.md` 的弱网说明）。
5. **系统前置**：Debian/Ubuntu 需 `sudo apt install python3-venv`（脚本检测到会明确提示）。

## 复现命令

```bash
# 环境前置
sudo apt install -y python3-venv
# 全新 clone + 一键（隔离宿主 daemon 时加 MEMORY_MCP_PORT=8766）
git clone -b feat/52-deploy https://github.com/Alex-tangt/Agent-Knowledge-Base.git
cd Agent-Knowledge-Base
HF_HUB_DISABLE_XET=1 bash install.sh --with-tests
# Tier1
venv/bin/python -m pytest tests/unit -q
for s in config_independence_22 gateway_authz_32 personal_mode_36 shared_service_41 \
         agent_loop_42 ingest_51; do venv/bin/python memory_agent/eval/$s.py; done
venv/bin/python memory_agent/eval/mcp_install_smoke_26.py --source-kb ~/.config/opencode/knowledge
# Tier2
venv/bin/python memory_agent/eval/bge_m3_embeddings_43.py
venv/bin/python memory_agent/eval/write_path_sandbox.py --source-kb ~/.config/opencode/knowledge
venv/bin/python memory_agent/eval/retrieval_eval.py --mode hybrid-rerank \
  --eval-set <过滤集> --out <out.json>
```

原始日志（临时）：`%TEMP%/opencode/wsl_install*.log`、`final_test/tier{1,2,2b}.log`、
`final_test/tier1_results.txt` / `tier2_results.txt`。
