# M3 · 路径范围与授权一致

- 结论：**PASS**
- 生成时间：2026-10-10T14:37:34+0800
- 运行 profile：`authz` · 端口 8781 · 同一隔离 workspace
- 身份：`user:mem-rw`（可写，ROLE=admin，ACL write）/ `user:mem-ro`（只读，ROLE=user，ACL read）
- 关键前提：**账户级 ACL 默认关闭**，必须先 `PATCH /api/v1/admin/accounts/default/settings {"acl":{"enabled":true}}`，否则 restricted/grant 不生效

| 检查 | 结论 |
|---|---|
| M3.0-seed 播种日期/类型/项目路径内容 + 未授权 restricted 子树 | **PASS** |
| M3.0b-acl-default-off 账户级 ACL 默认关闭：同一身份同一操作在开关前后的可观测差异 | **PASS** |
| M3.1-path-addressing 能按日期/类型/项目路径列举 | **PASS** |
| M3.2-search-then-read 检索返回引用，且能读回该引用的正文 | **PASS** |
| M3.3-readonly-identity 只读身份可读授权范围，且写/建目录/删/移全部被拒绝 | **PASS** |
| M3.4-out-of-scope-direct-read-denied 范围外（未授权的 restricted 子树 / 他用户私有命名空间）的直接读取不能泄露内容 | **PASS** |
| M3.5-out-of-scope-retrieval-no-leak 范围外的检索不能泄露内容（含 grep 越界与『返回即不可读』不一致） | **PASS** |
| M3.6-errors-identifiable 无凭据 / 坏凭据 / 越权 / 不存在 四类错误可识别 | **PASS** |

## 范围外的四类错误

```json
{
  "no_credential": {
    "http_status": 401,
    "code": "UNAUTHENTICATED"
  },
  "bad_credential": {
    "http_status": 401,
    "code": "UNAUTHENTICATED"
  },
  "permission_denied": {
    "http_status": 403,
    "code": "PERMISSION_DENIED"
  },
  "not_found": {
    "http_status": 404,
    "code": "NOT_FOUND"
  },
  "distinct_pairs": [
    [
      401,
      "UNAUTHENTICATED"
    ],
    [
      403,
      "PERMISSION_DENIED"
    ],
    [
      404,
      "NOT_FOUND"
    ]
  ]
}
```

## 复现

```powershell
$env:SPIKE_PROFILE='authz'; python spike_server.py start
python m3_authz.py --reset
```
