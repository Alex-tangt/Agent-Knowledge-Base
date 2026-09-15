"""#13 分块全量重建：代目录 + 指针切换（原子替换）+ 自洽核对。

用真实 Qdrant（Stub 嵌入，不加载 BGE-M3）锁住外部可观察行为：
只有 done 才切指针、中断/不自洽不切指针、旧代保留、空语料拒绝。
"""
import json
import os

import pytest

from memory_agent.memory.entries import Entry  # noqa: E402
from memory_agent.memory.errors import IndexConsistencyError  # noqa: E402
from memory_agent.memory.layout import IndexLayout  # noqa: E402
from memory_agent.memory.reindex import Reindexer  # noqa: E402
from memory_agent.memory.store import QdrantLocalStore  # noqa: E402
from ragcore.utils.model_status import EMBEDDING_DIMENSION  # noqa: E402


class StubEmbeddings:
    def embed_query(self, text):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def _entry(tmp_path, name, content, writable=True, entry_id=None):
    path = os.path.join(str(tmp_path), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Entry.from_file(path, source=name, writable=writable,
                           entry_id=entry_id or name)


def _store_factory(lying_count=None):
    def build(db_path):
        store = QdrantLocalStore(db_path=db_path, collection_name="mem",
                                 embeddings=StubEmbeddings())
        if lying_count is not None:
            store.count = lambda: lying_count
        return store
    return build


def _reindexer(layout, entries, store_factory=None):
    return Reindexer(
        layout=layout,
        entry_loader=lambda: list(entries),
        store_factory=store_factory or _store_factory(),
    )


def _corpus(tmp_path):
    return [
        _entry(tmp_path, "kb/a.md", "# A\n\nbody a\n", entry_id="a"),
        _entry(tmp_path, "kb/b.md", "# B\n\nbody b\n", entry_id="b"),
        _entry(tmp_path, "repo/c.md", "# C\n\nbody c\n", writable=False, entry_id="repo:c"),
    ]


def test_run_all_switches_pointer_and_lands_manifest(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))
    result = _reindexer(layout, _corpus(tmp_path)).run_all(batch=2)

    assert result["done"] is True
    assert result["entries"] == 3
    assert result["writable"] == 2
    assert result["readonly"] == 1
    assert layout.read_pointer() == "gen-1"

    manifest = json.loads(open(layout.manifest_path("gen-1"), encoding="utf-8").read())
    assert set(manifest["entries"]) == {"a", "b", "repo:c"}
    assert manifest["built_at"]


def test_pointer_stays_on_old_gen_until_done(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))
    reindexer = _reindexer(layout, _corpus(tmp_path))

    first = reindexer.reindex(cursor=None, batch=2)
    assert first["done"] is False
    assert first["processed"] == 2
    assert first["cursor"] == {"gen": "gen-1", "offset": 2}
    assert layout.read_pointer() is None  # 未完成绝不切指针

    second = reindexer.reindex(cursor=first["cursor"], batch=2)
    assert second["done"] is True
    assert layout.read_pointer() == "gen-1"


def test_rebuild_into_new_generation_keeps_previous(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))
    _reindexer(layout, _corpus(tmp_path)).run_all()
    _reindexer(layout, _corpus(tmp_path)[:2]).run_all()

    assert layout.read_pointer() == "gen-2"
    assert layout.generations() == ["gen-1", "gen-2"]
    manifest = json.loads(open(layout.manifest_path("gen-2"), encoding="utf-8").read())
    assert set(manifest["entries"]) == {"a", "b"}


def test_inconsistent_build_errors_without_switching_pointer(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))
    reindexer = _reindexer(layout, _corpus(tmp_path), _store_factory(lying_count=999))

    with pytest.raises(IndexConsistencyError):
        reindexer.run_all()

    assert layout.read_pointer() is None  # 不自洽 → 旧代继续服务
    assert layout.generations() == ["gen-1"]  # 未接管的残缺代仍在，但没被启用


def test_empty_corpus_refuses_to_build(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))

    with pytest.raises(IndexConsistencyError):
        _reindexer(layout, []).run_all()

    assert layout.read_pointer() is None
    assert layout.generations() == []


def test_stale_cursor_raises(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))

    with pytest.raises(IndexConsistencyError):
        _reindexer(layout, _corpus(tmp_path)).reindex(cursor={"gen": "gen-9", "offset": 0})


def test_prune_keeps_latest_generations(tmp_path):
    layout = IndexLayout(root=str(tmp_path / "idx"))
    for _ in range(4):
        _reindexer(layout, _corpus(tmp_path)).run_all()

    assert layout.read_pointer() == "gen-4"
    assert layout.generations() == ["gen-3", "gen-4"]
