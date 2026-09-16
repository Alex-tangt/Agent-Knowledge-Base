"""视图注册表命名迁移（#37 / ADR-0025）：`KBRegistry` / `kb_name` → view 命名。

覆盖：新 canonical 模块 `view_registry`、旧 `kb_registry` 兼容 shim、旧文件
`kb_registry.json` → `view_registry.json` 的一次性迁移、策略解析的 `view_name` 参数。
"""
import json
import os

from ragcore.services import kb_registry as legacy
from ragcore.services import view_registry as canonical
from ragcore.strategies import (
    DefaultRetrievalStrategy,
    DefaultSplitStrategy,
    LegalRetrievalStrategy,
    LegalSplitStrategy,
    get_retrieval_strategy,
    get_split_strategy,
)


def _isolate(monkeypatch, tmp_path, legacy_content=None):
    """把注册表读写锚定到 tmp，绝不碰真实 legal_web/。"""
    view_file = tmp_path / "view_registry.json"
    legacy_file = tmp_path / "kb_registry.json"
    if legacy_content is not None:
        legacy_file.write_text(json.dumps(legacy_content, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(canonical, "VIEW_REGISTRY_FILE", str(view_file))
    monkeypatch.setattr(canonical, "LEGACY_REGISTRY_FILE", str(legacy_file))
    return view_file, legacy_file


# ---------------------------------------------------------------- canonical

def test_load_creates_default_when_absent(monkeypatch, tmp_path):
    view_file, _ = _isolate(monkeypatch, tmp_path)
    reg = canonical._load()
    assert "documents" in reg
    assert reg["documents"]["split_strategy"] == "legal"
    assert view_file.exists(), "默认注册表应落盘到新文件名"


def test_create_get_list_delete(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    registry = canonical.ViewRegistry()

    created = registry.create("tech_docs", "技术文档视图", "RAG 技术文档")
    assert created["name"] == "tech_docs"
    assert registry.get("tech_docs")["label"] == "技术文档视图"

    names = [v["name"] for v in registry.list()]
    assert "technical" not in names
    assert "tech_docs" in names and "documents" in names

    registry.delete("tech_docs")
    assert registry.get("tech_docs") is None


def test_create_duplicate_and_delete_rules(monkeypatch, tmp_path):
    import pytest

    _isolate(monkeypatch, tmp_path)
    registry = canonical.ViewRegistry()
    with pytest.raises(ValueError, match="已存在"):
        registry.create("documents", "重复")
    with pytest.raises(ValueError, match="不存在"):
        registry.delete("nope")
    with pytest.raises(ValueError, match="不能删除默认视图"):
        registry.delete("documents")


def test_missing_registry_reads_empty_order(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, legacy_content={})
    assert canonical.ViewRegistry().list() == []


# ------------------------------------------------------------ 兼容 shim

def test_legacy_module_exposes_view_aliases():
    assert legacy.KBRegistry is canonical.ViewRegistry
    assert legacy.KB_REGISTRY_FILE == canonical.VIEW_REGISTRY_FILE
    assert legacy.kb_registry is canonical.view_registry
    assert os.path.basename(legacy.KB_REGISTRY_FILE) == "view_registry.json"


# ------------------------------------------------------------ 旧文件迁移

def test_legacy_registry_file_migrated(monkeypatch, tmp_path):
    legacy_content = {
        "old_view": {
            "name": "old_view",
            "label": "旧视图",
            "description": "",
            "split_strategy": "default",
            "retrieval_strategy": "default",
        }
    }
    view_file, legacy_file = _isolate(monkeypatch, tmp_path, legacy_content)

    names = [v["name"] for v in canonical.ViewRegistry().list()]
    assert names == ["old_view"]
    assert view_file.exists(), "应一次性迁移到新文件名"
    assert legacy_file.exists(), "旧文件不删除（不写回、不搬运）"


# ------------------------------------------------------- 策略 view_name 参数

def test_strategies_resolve_from_view_name(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, legacy_content={
        "documents": {
            "name": "documents",
            "label": "政策法规视图",
            "split_strategy": "legal",
            "retrieval_strategy": "legal",
        }
    })
    assert isinstance(get_split_strategy(view_name="documents"), LegalSplitStrategy)
    assert isinstance(get_retrieval_strategy("documents"), LegalRetrievalStrategy)


def test_strategies_fall_back_to_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, legacy_content={"documents": {"name": "documents"}})
    assert isinstance(get_split_strategy(view_name="unknown"), DefaultSplitStrategy)
    assert isinstance(get_retrieval_strategy("unknown"), DefaultRetrievalStrategy)
