"""策略注册与解析 — 按 KB 配置加载切分/检索策略。

KB 元数据中可通过 `split_strategy` / `retrieval_strategy` 字段指定策略名：
- legal：法律域策略（条文感知切分 + 条文/锚点混合检索）
- default：通用策略（递归切分 + 纯向量检索）
未指定时使用 default，保证通用工具开箱即用。
"""
from strategies.base import SplitStrategy, RetrievalStrategy
from strategies.legal import LegalSplitStrategy, LegalRetrievalStrategy
from strategies.default import DefaultSplitStrategy, DefaultRetrievalStrategy

SPLIT_STRATEGIES = {
    "legal": LegalSplitStrategy,
    "default": DefaultSplitStrategy,
}

RETRIEVAL_STRATEGIES = {
    "legal": LegalRetrievalStrategy,
    "default": DefaultRetrievalStrategy,
}

DEFAULT_SPLIT = "default"
DEFAULT_RETRIEVAL = "default"


def _kb_config(kb_name):
    if not kb_name:
        try:
            from services.kb_registry import kb_registry
            kb_name = kb_registry.default_name()
        except Exception:
            pass
    if not kb_name:
        return {}
    try:
        from services.kb_registry import kb_registry
        return kb_registry.get(kb_name) or {}
    except Exception:
        return {}


def get_split_strategy(kb_name=None) -> SplitStrategy:
    name = _kb_config(kb_name).get("split_strategy", DEFAULT_SPLIT)
    cls = SPLIT_STRATEGIES.get(name, DefaultSplitStrategy)
    return cls()


def get_retrieval_strategy(kb_name=None) -> RetrievalStrategy:
    name = _kb_config(kb_name).get("retrieval_strategy", DEFAULT_RETRIEVAL)
    cls = RETRIEVAL_STRATEGIES.get(name, DefaultRetrievalStrategy)
    return cls()


def create_retrieval_strategy(name="default", **kwargs) -> RetrievalStrategy:
    cls = RETRIEVAL_STRATEGIES.get(name, DefaultRetrievalStrategy)
    return cls(**kwargs)


__all__ = [
    "SplitStrategy", "RetrievalStrategy",
    "LegalSplitStrategy", "LegalRetrievalStrategy",
    "DefaultSplitStrategy", "DefaultRetrievalStrategy",
    "get_split_strategy", "get_retrieval_strategy", "create_retrieval_strategy",
]
