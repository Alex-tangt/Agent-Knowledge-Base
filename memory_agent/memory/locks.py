"""进程内串行化锁（issue #19）。

为什么需要：改成"单进程常驻 + N 个瘦客户端"后，一个 daemon 要同时服务多个会话。
实测 **Qdrant local mode 同一进程内也无法并发开两个 client**（第二个构造直接
`RuntimeError: ... already accessed ...`），所以并发检索/写入必须在进程内串行化，
不能只靠退避重试。

两把锁，职责不同、加锁顺序恒为 WRITE -> INDEX -> (store session)：

- `INDEX_LOCK`：保护派生索引的逻辑临界区——search 的「自洽核对 + 查询」要作为整体、
  refresh/rebuild/status 要互斥、reindex 与它们互斥。用 RLock，允许同一线程重入
  （方法之间会互相调用）。
- `WRITE_LOCK`：写入网关的互斥——一次 add/supersede/archive 从头（去重检索）到尾
  （git commit + 索引刷新）串成一整个临界区，避免 `.git/index.lock` 争用与去重的
  TOCTOU。
"""
from __future__ import annotations

import functools
import threading

INDEX_LOCK = threading.RLock()
WRITE_LOCK = threading.Lock()


def locked(lock):
    """把方法体整体放进 `lock` 的装饰器（避免大段重排缩进）。"""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with lock:
                return fn(*args, **kwargs)
        return wrapper
    return decorator
