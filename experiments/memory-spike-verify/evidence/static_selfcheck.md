# 独立复核（自测）结果

- 阶段：`static`
- 生成时间：2026-10-10T14:25:10+0800
- 判定标准指纹（跑前预注册）：`17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`
- 结果载荷指纹：`5fdc1aeb5817ec6d6489d27c55a0680627abbcc5b12dbe8e401835236c48a1cb`
- 隔离实例：openviking-server (候选实现, 隔离实例) @ 127.0.0.1:8783

- 判定：**PASS**

| 静态自检项 | 通过 |
|---|---|
| criteria_structure | ✅ |
| criteria_fingerprint | ✅ |
| hash_determinism | ✅ |
| port_isolation | ✅ |
| verify_root_outside_repo | ✅ |
| forbidden_paths_declared | ✅ |
| write_guard | ✅ |
| bom_detector | ✅ |
| no_cloud_model_in_conf | ✅ |
| novproxy_sanitizer | ✅ |
| framework_static_expectations | ✅ |
| versions_recorded | ✅ |
| harness_pinned | ✅ |
