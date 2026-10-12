# 预检排序修订（产品执行前）

首次执行在独立 snapshot inventory 比对处退出1，尚未创建 `.mb94`，没有进行 CLI install/restore/start，也没有运行时修改。原失败保存于 `preflight-method-failure.json`。

复核67文件的 path→size/hash 映射与manifest完全一致，目录集合也一致。错误来自排序方法：WindowsPath 的排序大小写不敏感，而manifest按保留大小写的相对字符串排序。独立inventory输出现在明确按相对字符串排序；不调整任何数据、文件hash、manifest或产品。提交后继续首次真实CLI执行。
