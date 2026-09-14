"""并发回归（issue #19）：单实例 daemon 要同时服务多会话。

核心事实：Qdrant local mode 的独占锁是「按目录 + 全进程」的——同一进程内并发构造
第二个 client 也会直接 RuntimeError，退避重试兜不住。所以 store 的每次操作
（构造 -> 查询/写入 -> close）必须在进程内串行化。这里用真 Qdrant（临时目录）+ 假
嵌入复现并守住该回归；不加载模型。
"""
import os
import sys
import threading
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from services.vector_store_service import VectorStoreService  # noqa: E402
from utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _run_threads(target, count):
    errors = []
    barrier = threading.Barrier(count)

    def worker(i):
        barrier.wait()
        try:
            target(i)
        except Exception as exc:  # noqa: BLE001 - 收集任意并发错误
            errors.append((i, repr(exc)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_searches_do_not_clash_on_local_mode_lock(tmp_path):
    store = VectorStoreService(
        collection_name="concurrency", db_path=str(tmp_path / "qdrant"),
        embeddings=StubEmbeddings(),
    )
    store.add_documents(
        [f"document {i}" for i in range(5)],
        metadata_list=[{"writable": True} for _ in range(5)],
    )

    def search(i):
        print(store.search_documents(f"document {i % 5}", k=3))

    errors = _run_threads(search, count=8)

    assert errors == [], f"并发检索撞锁：{errors}"


def test_mixed_concurrent_writes_and_searches_stay_consistent(tmp_path):
    store = VectorStoreService(
        collection_name="concurrency", db_path=str(tmp_path / "qdrant"),
        embeddings=StubEmbeddings(),
    )
    store.add_documents(["seed"], metadata_list=[{"writable": True}])

    def work(i):
        if i % 2 == 0:
            store.add_documents(
                [f"new {i}"], metadata_list=[{"writable": True}], ids=[str(uuid.uuid4())],
            )
        else:
            print(store.search_documents("seed", k=2))

    errors = _run_threads(work, count=8)

    assert errors == [], f"并发读写撞锁：{errors}"
