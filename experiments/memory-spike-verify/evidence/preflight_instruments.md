# 独立复核（自测）结果

- 阶段：`preflight`
- 生成时间：2026-10-10T14:25:12+0800
- 判定标准指纹（跑前预注册）：`17d18b7bbe842da6059b341a41319c11fa2824f30817aa191bc96802174f2f4d`
- 结果载荷指纹：`144958bfe5277252bce468942cb69d9e76d9889bb0d10d45c95a5d7ad4a9aaba`
- 隔离实例：openviking-server (候选实现, 隔离实例) @ 127.0.0.1:8783

- 判定：**PASS**

| 静态自检项 | 通过 |
|---|---|
| source_tree_has_git_head | ✅ |
| scan_negative_control | ✅ |
| scan_positive_control | ✅ |
| scan_detects_rewrite | ✅ |
| git_blob_positive_control | ✅ |
| snapshot_diff_detects_change | ✅ |
| cleanup_leaves_no_marker | ✅ |
