# #32 网关验收结果：`/mcp` 边界 authn + 工具层 authz（强制过滤注入）

- 套件：`memory_agent/eval/gateway_authz_32.py`（可复跑；**不加载模型**，Qdrant local + 桩嵌入）
- 运行：`venv\Scripts\python.exe memory_agent/eval/gateway_authz_32.py`
- 结果：**13/13 全过**（`passed: true`）
- 单元测试：`venv\Scripts\python.exe -m pytest tests/unit -q` → **248 passed**
  （新增 `tests/unit/test_gateway_authz.py` 21 条）

## 外部行为断言矩阵

| # | check | 断言（外部行为） | 结果 |
|---|-------|------------------|------|
| 1 | `authn_config_default` | 单租户身份 = 进程配置 | ✅ |
| 2 | `authn_token_binds_identity` | `/mcp` 边界的 Bearer token 解析成 `(principal, tenant)` 并绑定到请求上下文 | ✅ |
| 3 | `authn_bad_token_denied` | 无效 token 在**工具执行前**被拒（`call_next` 未跑） | ✅ |
| 4 | `search_tenant_isolation` | `memory_search` 只返回绑定租户 `org-a` 的条目（真实 Qdrant 侧过滤） | ✅ |
| 5 | `search_abac_multi_value` | 多值 ABAC `classification ∈ {private, internal}` 生效（`public` 被排除） | ✅ |
| 6 | `search_widen_tenant_denied` | 调用方 `payload_filter={"tenant": "org-b"}` → **被拦**（负向回归） | ✅ |
| 7 | `search_widen_classification_denied` | 调用方请求越权密级 → **被拦**（负向回归） | ✅ |
| 8 | `get_other_tenant_denied` | `memory_get` 跨租户 id → 被拦 | ✅ |
| 9 | `get_within_entitlement_allowed` | 授权范围内 `memory_get` 正常返回 | ✅ |
| 10 | `reader_write_denied` | `reader` 角色调用写工具 → 被拦 | ✅ |
| 11 | `audit_records_call` | 审计记录 `tools/call` + 工具名 + 身份（principal/tenant/role/ABAC 范围） | ✅ |
| 12 | `audit_no_token` | 审计中**不含** token | ✅ |
| 13 | `audit_no_token_on_reject` | 拒绝路径的审计也**不含** token | ✅ |

## 关键负面回归（本票核心）

1. **tenant 不可放宽**：绑定 `org-a` 的身份请求 `org-b` → `AuthorizationError` → 工具返回
   `ToolError`（不是静默改成 `org-a`，也不是放行）。
2. **ABAC 不可放宽**：entitlement `{private, internal}` 的身份请求 `public` → 被拦。
3. **免托管加固**：`MEMORY_AUTH_TOKENS` 配置后默认 `require_token`；无 token / 无效 token
   直接拒绝，**绝不回落到默认身份**（多租户形状下 proxy 不带身份也不能绕过，ADR-0018 D2.4）。

## 说明

- ABAC 维度用**允许集**表达；部分多值集（如 `{private, internal}`）经端口 `payload_filter`
  的多值形态下沉为 Qdrant `MatchAny`（`ragcore/services/vector_store_service.py`，就地 amend
  ADR-0019 D3）。关键词通道的后置过滤同步支持多值（`ragcore/strategies/default.py`）。
- 审计事件只含身份，凭证不进内存事件、不进 JSONL、不进异常消息。
- 隔离绕过**对抗性**测试套件是 **#34**（blocked by 本票）；本票只保证「强制点存在且可测」。
