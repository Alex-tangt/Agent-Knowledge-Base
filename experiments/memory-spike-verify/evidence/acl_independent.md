# 独立复核：授权面（执行方 M2.6 / M3 声明）

- 生成时间：2026-10-10T15:56:46+0800
- 运行：`20261010T155557-ebc650` · profile `api_key（root_api_key 随运行随机生成，仅写仓外隔离 conf，证据不含值）` · 账户 `mvpverifyb0adaa`
- 判定标准指纹：`17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`
- 载荷指纹：`0e12b1c2bc20a04e81378032af571299461ed9256c429d7b7f261eb5d85b1d4b`

| 声明 | 判定 |
|---|---|
| ACL-a-default-off | **PASS** |
| ACL-b-off-means-ro-can-write | **PASS** |
| ACL-c-on-means-403 | **PASS** |
| ACL-d-outside-denied | **PASS** |
| ACL-e-ro-write-scope | **PASS** |

## ACL-a-default-off → PASS
- 声明：账户级 ACL 默认关闭（acl.enabled 默认 false）
- 观测：`{"acl_enabled_observed": false, "static_source": {"file": "D:\\python_work\\work2026-4\\Agent-Knowledge-Base\\venv-ov\\Lib\\site-packages\\openviking\\config\\account_config.py", "file_sha256": "cfa2cd2ced5240ade1b5650c57475effad2bb24c2fcd921e6c0d7b52cfe81498", "enabled_default_false": true, "segment": "class AccountAclSettings(BaseModel):\n    \"\"\"Account-scoped ACL switch.\"\"\"\n\n    enabled: bool = RuntimeField(default=False)\n\n\nclass AccountFeishuConfig(BaseModel):\n    \"\"\"Account-level Feishu settings.\n\n    ``domain`` intentionally does not exist here. It is a cluster deployment\n    setting and is se"}}`

## ACL-b-off-means-ro-can-write → PASS
- 声明：ACL 未打开时，只读身份（role=user + ACL read）仍能写入
- 观测：`{"write_accepted_with_acl_off": true, "error": null, "readonly_read_ok": true}`

## ACL-c-on-means-403 → PASS
- 声明：打开 ACL 后，同一身份同一操作被拒（403 PERMISSION_DENIED）
- 观测：`{"write_rejected_with_acl_on": true, "error_code": "PERMISSION_DENIED", "error": {"exception": "PermissionDeniedError", "module": "openviking_sdk.errors", "message": "Access denied for viking://resources/mvpverifyb0adaa/doc.md: write permission required"}, "readonly_read_still_ok": true, "admin_write_still_ok": true}`

## ACL-d-outside-denied → PASS
- 声明：范围外（未授权 restricted 子树）的直接读取被拒且不泄露正文
- 观测：`{"read": {"ok": false, "error": {"exception": "PermissionDeniedError", "module": "openviking_sdk.errors", "message": "Access denied for viking://resources/mvpverifyb0adaa-outside/secret.md: read permission required"}}, "ls": {"ok": false, "error": {"exception": "PermissionDeniedError", "module": "openviking_sdk.errors", "message": "Access denied for viking://resources/mvpverifyb0adaa-outside: read permission required"}}}`

## ACL-e-ro-write-scope → PASS
- 声明：（补 C4b）非 ROOT 身份通过基座写受限范围被显式拒绝
- 观测：`{"target": "viking://resources/mvpverifyb0adaa/doc.md", "error_code": "PERMISSION_DENIED"}`


（原始 JSON 见 `acl_independent.json`；凭据值已全部脱敏。）
