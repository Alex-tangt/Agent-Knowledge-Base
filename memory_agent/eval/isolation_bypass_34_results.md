# #34 隔离绕过对抗性套件结果

- 套件：`memory_agent/eval/isolation_bypass_34.py`（可复跑；**不加载任何模型**——Stub 嵌入 + Qdrant local）
- 运行：`venv\Scripts\python.exe memory_agent/eval/isolation_bypass_34.py`
- 结果：**32/35 通过**，**3 条未被拦 = 阻塞项**（套件退出码 1）
- 单测：`tests/unit/test_gateway_isolation_bypass.py` → 本文件新增 **17 passed + 3 xfailed**；
  全套 `venv\Scripts\python.exe -m pytest tests/unit -q` → **308 passed, 3 xfailed**
- 真实 KB（`C:\Users\Tan\.config\opencode\knowledge`）：HEAD `7b37efb8bc` 前后未变、工作树逐字一致

形式同 #16 写路径 sandbox：隔离 fixture（临时 Qdrant + manifest，4 条条目跨 2 租户 / 3 密级 /
2 驻留）、只读真实 KB、只断言外部行为（MCP 工具响应 + 直接 store 调用 + `git status` + proxy 源码）。

## 结果矩阵

| # | check | 断言（外部行为） | 结果 |
|---|-------|------------------|------|
| 0a | 隔离索引（未绑定租户）建成且自洽 | 4 条 / 4 点 / consistent | ✅ |
| 0b | 隔离索引（绑定 org-a）建成且自洽 | 4 条 / 4 点 / consistent | ✅ |
| 1a | org-a 身份 vector 通道不返回 org-b | `tenants={org-a}` | ✅ |
| 1b | org-a 身份 keyword 通道不返回 org-b | 只返回 org-a 条目 | ✅ |
| 1c | org-b 身份只看得到 org-b | `tenants={org-b}` | ✅ |
| 1d | **[攻击①]未声明 tenant 的身份不得经 keyword 通道看到 org-b** | **泄漏 `topics/b-secret`** | ❌ **阻塞** |
| 1e | **[攻击①]绑定 store 不得把 org-a 数据交给 org-b 身份** | **org-b 拿到全部 org-a 条目** | ❌ **阻塞** |
| 2a | ② tenant 放宽 → 被拦 | 越权：请求 `org-b` 与绑定租户不符 | ✅ |
| 2b | ② classification 放宽 → 被拦 | 越权：`public` 超出授权集 | ✅ |
| 2c | ② residency 放宽 → 被拦 | 越权：`cloud` 超出授权集 | ✅ |
| 2d | ② tenant 多值 → 被拦 | 越权：list vs 绑定租户 | ✅ |
| 2e | ② tenant=None → 被拦 | 越权 | ✅ |
| 2f | ② tenant 空串 → 被拦 | 越权 | ✅ |
| 2g | ② 大小写变体不得放宽 | `Tenant` 非受管控键，结果只含 org-a | ✅ |
| 2h | ② 未知字段仅收窄、不放宽 | 只含 org-a | ✅ |
| 2i | ② memory_get 跨租户 id → 被拦 | `topics/b-secret` 不在授权范围 | ✅ |
| 2j | ② 授权内 memory_get 正常返回 | `topics/a-priv` | ✅ |
| 3a | ③ 绑定 store 向量通道不可被放宽 | 只返回 org-a | ✅ |
| 3b | **③ 绑定 store keyword 通道不得返回其它租户** | **泄漏 `topics/b-secret`** | ❌ **阻塞** |
| 3c | ③ MCP 工具面无裸 store / tenant 参数 | 无 offenders | ✅ |
| 3d | ③ runtime 构造的 store 绑定进程租户（组合前提） | `store.tenant=org-a` | ✅ |
| 4a | ④ 伪造 `X-Tenant` 无 token → 拒绝 | `MCPError: 缺少 Bearer 凭证` | ✅ |
| 4b | ④ 伪造头不改变 token 身份 | `tenant=org-a` | ✅ |
| 4c | ④ 非 Bearer（Basic）→ 拒绝 | `MCPError` | ✅ |
| 4d | ④ 伪造 principal/role/classification 头被忽略 | `principal=svc-a role=writer` | ✅ |
| 4e | ④ proxy 源码无身份 / 凭证 / 网关依赖 | 命中 `[]` | ✅ |
| 5a | ⑤ local 身份不取 cloud 条目 | `residencies={local}` | ✅ |
| 5b | ⑤ cloud 身份不取 local 条目 | `residencies={cloud}` | ✅ |
| 5c | ⑤ local 身份的 keyword 通道不取 cloud 条目 | 只含 local | ✅ |
| 5d | ⑤ cloud 条目的 memory_get 对 local 身份 → 被拦 | 不在授权范围 | ✅ |
| 6a–6c | 真实 KB 快照可读 / 审计不含凭证 | HEAD `7b37efb8bc`、15 行 | ✅ |
| 6d–6e | 真实 KB HEAD 未变 / 工作树逐字一致 | 一致 | ✅ |

## 阻塞项（未被拦的攻击面）

### F1 — 绑定租户的 store，keyword 通道不认绑定租户（check 3b）

- **现象**：`QdrantLocalStore(tenant="org-a").search_by_keywords(["ZEBRA34B"])` 返回归属 `org-b`
  的条目 `topics/b-secret`。
- **根因**：向量通道在 `memory_agent/memory/store.py:52`（`search`）用 `self.tenant` 收窄；
  关键词通道 `memory_agent/memory/store.py:67`（`search_by_keywords`）**原样透传**
  `ragcore/services/vector_store_service.py:256` 的全量 scroll，**不带 tenant**。
  而检索策略 `ragcore/strategies/default.py:138` 会调用该通道，其后置过滤
  `_matches_filter`（`default.py:140`）**只在 `payload_filter` 非空时**生效。
- **定性**：直接违反端口契约「绑定租户只可收窄」（ADR-0019 D3；既有单测
  `test_search_filters_by_bound_tenant_and_cannot_be_widened` 只覆盖了向量通道）。

### F2 — store 绑定租户**覆盖**网关注入的 tenant（check 1e）

- **现象**：绑定 `org-a` 的 store + `org-b` 身份（网关 entitlement 已注入 `tenant=org-b`）
  → `memory_search` 返回**全部 org-a 条目**。
- **根因**：`memory_agent/memory/store.py:57-59` 无条件用 `self.tenant` 覆盖
  `payload_filter["tenant"]`（覆盖，而非求交）。网关以为自己在做唯一强制，实际被 store 层
  改写——从集成视角是「org-b 身份读到 org-a 数据」。
- **定性**：违反 ADR-0018 D2「网关唯一强制」与 ADR-0019 D3「只可收窄不可放宽」。
- **可达性**：`runtime._store_factory`（`memory_agent/runtime.py:53-61`）在进程身份带
  tenant（`MEMORY_AUTH_TENANT`）时构造绑定该 tenant 的 store；若同时启用 per-token 多租户
  （`MEMORY_AUTH_TOKENS`），请求身份 tenant ≠ store 绑定 tenant 即触发。

### F3 — 身份未声明 tenant 时，工具不注入 tenant → keyword 通道漏其它租户（check 1d）

- **现象**：绑定 `org-a` 的 store + `tenant=None` 的身份 → `memory_search("ZEBRA34B")`
  返回 `org-b` 条目。
- **根因**：`memory_agent/gateway/authz.py:80-89` 对 `identity.tenant is None` 不产生 tenant
  子句 → `MemoryIndex.search` 的 `payload_filter` 为 `None` → strategy 后置过滤跳过 →
  keyword 通道返回集合中所有租户的命中（向量通道被 store 绑定兜住，造成「一半隔离」）。
- **可达性**：`MEMORY_AUTH_TENANT=org-a`（绑定 store）且某 token 未声明 tenant。

## 修复方向（本票只报告，未改生产代码）

1. **统一 tenant 语义为「求交」**：store 不得覆盖调用方（网关）的 tenant；绑定租户应与
   传入 filter 求交（不相交则返回空），使「只可收窄」在两层组合下成立。
2. **让强制过滤贯穿所有通道**：`VectorStore.search_by_keywords` 必须继承 store 绑定 tenant；
   或 strategy 在 store 带绑定 tenant 时无条件应用 post-filter。ADR-0019 D4–D7 计划用
   store 原生 hybrid 退役手写关键词通道——落地前 F1/F3 仍在。
3. **消除双重租户来源**：既然「网关唯一强制」，`runtime._store_factory` 的进程租户绑定
   要么删除（store 不再自行裁决），要么在启动时与网关 entitlement 一致性校验（不一致即失败）。

## 说明

- F2 / F3 需要「store 绑定 tenant（`MEMORY_AUTH_TENANT`）」与「per-token 多租户」同时存在；
  F1 是端口契约的直接违反，不需要混合配置。三者同源：**store 层自行做租户裁决，与网关
  不一致**。
- 单测里三条对应用例用 `xfail(strict=True)` 标记（修好后 XPASS 会提醒移除）。
- 审计确认不含凭证；`X-Tenant` 等伪造头不被采信；proxy 源码无身份 / 凭证 / 网关依赖
  （ADR-0018 D2.4）。
