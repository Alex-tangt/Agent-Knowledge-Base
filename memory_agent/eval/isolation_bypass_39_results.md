# #39 修复证据：store 层租户裁决 = 求交（只可收窄，不相交即空）

> **`memory_agent/eval/isolation_bypass_34_results.md` 保留原样**——那份文档记录的是 **#34 当时的事实**
> （**32/35** + 3 条阻塞项 F1/F2/F3），**不改写**。本文只记录 **#39 修完之后**的状态与证据。

- 票：**#39**（由 #34 隔离绕过套件发现）；分支 `fix/39-tenant-leak`。
- 生产码只动 `memory_agent/memory/store.py`（提交 `c851549`）；回归提交 `e3a4632` / `f656e15`。
- `memory_agent/gateway/**`、`memory_agent/runtime.py`、`memory_agent/mcp_server.py`、`ragcore/**`
  **未动**——keyword 通道修在 **store 适配器**层，`legal_web` 的检索路径逐字不变（不碰检索默认 / 合成）。
- 套件本身（`isolation_bypass_34.py`）**未改**：修复后自然 35/35。

## 结论（头条）

| 闸门 | 修前 | 修后 |
|---|---|---|
| `memory_agent/eval/isolation_bypass_34.py` | **32/35**（FAIL = 1d F3 / 1e F2 / 3b F1） | **35/35，退出码 0** |
| `pytest tests/unit -q` | 584 passed / 3 skipped / **3 xfailed** | **591 passed / 3 skipped / 0 xfailed** |
| xfail 标记 | 3 条 `xfail(strict=True)` | **已物理删除**（`test_gateway_isolation_bypass.py` 中不再存在 `pytest.mark.xfail`） |
| 真实 KB | HEAD `67d9fc56f955e91a06d26f37e905f5b6928a9d61`，`status --short` 18 行 | **前=后，逐字一致** |
| 生产索引 `memory_agent/vector_db` | 28 files / 10,749,785 B / `CURRENT=gen-4` / 指纹 `427a3ce2…ec65` | **前=后，一致** |

数字口径：`584 → 591` = **3 条 xfail 翻绿** + **4 条新增回归**（isolation 2 + store_port 1 + networked 1），
**无下降**；`3 xfailed → 0`，`3 skipped` 不变（2 条 network-store 需自建 Qdrant 服务 + 1 条 Docling 未装）。

## 验收②口径（防误读——最重要）

- store 绑定租户与调用方（网关注入）tenant **求交**；**交集为空 = 返回空**，任一端都不退化为「全量」
  （ADR-0019 **D3.1**）。**「覆盖」不是收窄**——那正是 F2 的缺陷口径。
- **org-b 身份 + 绑定 org-a 的 store → 硬空**（`{org-b} ∩ {org-a} = ∅`），`memory_search` 返回 `[]`。
- **无 tenant 身份 + 绑定 org-a 的 store → 不是「全拒」，而是「只到绑定租户 org-a」**（ADR-0019 **D3.3**）。
  攻击 query（唯一 marker `ZEBRA34B` **只出现在 org-b 条目**里）**返回空**（无泄漏）；但同一身份用命中
  org-a 条目的 query 检索时，**仍会正常返回那些 org-a 条目**——这是 D3.3 的定义，**不是泄漏**。
- **全通道同口径**（ADR-0019 **D3.2**）：向量 / keyword / hybrid / `search_dense` 全路径一致；
  `search_by_keywords` 仅在**存在绑定租户**时收窄（无绑定时留给策略层的 `payload_filter` 后置过滤）。

## 反例矩阵（只断言外部行为；直接跑隔离 fixture）

| # | 场景 | 结果 | 判定 |
|---|---|---|---|
| A | 绑定 org-a + **org-b 身份**，`memory_search("ALPHA34A")` | `[]` | ✅ 不泄漏（F2） |
| B | 绑定 org-a + **无 tenant 身份**，`memory_search("ZEBRA34B")`（marker 只在 org-b 条目） | 只含 org-a 条目，**无 org-b** | ✅ 不泄漏（F3 / D3.3） |
| C | `bound.search_by_keywords(["ZEBRA34B"])` | `[]` | ✅ 不泄漏（F1） |
| F | `bound.search("note", payload_filter={"tenant": "org-b"})` | `[]` | ✅ 直调 store 也不放宽 |
| D | **正例**：绑定 org-a + **org-a 身份**，`memory_search("ALPHA34A")` | `topics/a-priv / a-cloud / a-internal` | ✅ 功能未被改没 |
| G | **正例**：无 tenant 身份，`memory_search("ALPHA34A")` | 同上 3 条 org-a | ✅ D3.3 正例（交集取绑定租户） |
| E | **正例**：`bound.search_by_keywords(["ALPHA34A"])` | `topics/a-priv` | ✅ keyword 通道收窄 ≠ 关掉 |
| H | **正例**：`bound.search(payload_filter={"tenant": "org-a"})` | 同上 3 条 org-a | ✅ 相交分支正常 |

> B/D/G 使用 Stub 嵌入（所有文本同向量）→ 向量通道会把**授权内的 org-a 条目全给出**；这正是断言
> `all(h["tenant"] == "org-a")` 的口径，而不是「结果里只该有 marker 命中的那一条」。

## 修复内容（生产码，`memory_agent/memory/store.py`）

- 新增 `narrow_tenant(bound, requests)`：把 store 绑定与调用方请求（`payload_filter["tenant"]` + `tenant=`
  参数）归一成集合后**求交**；任一端缺失 = 该端不限制；**交集为空 → `possible=False`**；多值结果原样下沉
  （Qdrant 侧 `MatchAny`，与 ADR-0019 D3 的多值 amend 同义）。
- 新增 `_scope_filter(bound, payload_filter, tenant)`：只对 `tenant` 维度做收敛，**其它键原样透传**。
- 新增 `_empty_result()`：交集为空时返回端口契约的空 dict 形，**在触达后端之前短路**。
- 五个读通道统一：`QdrantLocalStore` / `QdrantNetworkStore` 的 `search` / `search_documents` /
  `search_dense` / `search_hybrid` 全部求交；`QdrantLocalStore.search_by_keywords` 在有绑定时按绑定
  租户收窄（修 F1），`QdrantNetworkStore.search_by_keywords` 本就返回空（词法走 store 原生 sparse）。
- `runtime._store_factory` 的进程租户绑定**保留**（#34 套件 3d 把它当组合前提断言）——两层
  「绑定 + 求交」= 纵深防御（ADR-0019 D3.4），**不做启动期失败**。

## 回归（测试侧，防「改测试让票变绿」）

- `tests/unit/test_gateway_isolation_bypass.py`：3 条 xfail **转硬回归**，每条**同时**钉
  「越界请求 → 空」与「授权内请求 → 仍正常返回」；另加 keyword / dense 通道回归。
- `tests/unit/test_memory_store_port.py`：旧断言 `== ["a"]`（**覆盖语义 = F2 缺陷口径**）改为
  **求交 → 空**；补 `tenant=` 参数来源求交、keyword/dense 通道回归。**未放宽任何其它断言。**
- `tests/unit/test_memory_networked_store.py`：同口径改（保留 `@requires_server` 门禁）；新增一条
  **无需服务**的短路回归——不相交时 `search` / `search_dense` / `search_hybrid` 必须**不触达后端**
  直接返回空（对不可达端点也必须成立）。该文件本机 **11 passed / 2 skipped**。

## 隔离与只读证明

- **隔离运行**：临时 git KB（`--source-kb <tmp>`）+ 临时索引 + **无端口** → **35/35**；`python` 进程数
  **before=4 / after=4**；临时目录已删，**无残留进程**。
- **真实 KB 只读**：HEAD 与 `status --short` **前后逐字一致**；那 18 行是**既有未提交改动**（非本票写入）。
- **生产索引未被触碰**：`memory_agent/vector_db` 的文件数 / 字节数 / `CURRENT` / 指纹前后一致，
  newest mtime `2026-10-07 08:47:10` **早于**本会话起点（18:45）。
- **无密钥 / token 落盘**（审计只记身份；套件 6c 复核）。

## 精确复跑命令

worktree 根（`worktree` 内无 `venv`，一律用主树 venv 绝对路径）：

```
$py = D:\python_work\work2026-4\Agent-Knowledge-Base\venv\Scripts\python.exe
$env:PYTHONPATH = D:\python_work\work2026-4\wk-39-tenant
cd D:\python_work\work2026-4\wk-39-tenant
& $py memory_agent/eval/isolation_bypass_34.py          # 35/35, exit 0
& $py -m pytest tests/unit -q                            # 591 passed, 3 skipped, 0 xfailed
```

隔离运行（不碰真实 KB 之外的任何生产状态）：

```
& $py memory_agent/eval/isolation_bypass_34.py --source-kb <临时 git KB> --json-out <临时 json>
```

## 遗留 / 边界

- **网络化 store 的相交分支**需真 Qdrant 服务（`MEMORY_STORE_TEST_URL`，默认
  `http://127.0.0.1:6333`）；本机 6333 端口关闭 → 与基线同样 **skip**。已用**无需服务**的短路回归
  覆盖 shared 平面的「不相交 → 空」；相交分支的断言随服务可用即执行。
- ADR 落点：Lead 已就地 amend **ADR-0019 D3.1–D3.4**（master `4b1c50e`），实现逐条对齐。
- 本文件是**新增证据**，不改 `isolation_bypass_34_results.md`（#34 的不可变记录）。
